#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebuildable, model-free SQLite retrieval over original pages and research records.

Only sync writes the cache. Original PDFs, manifests, reading receipts and adopted
research remain authoritative files; querying never acknowledges PDF reading.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

import source_archive as archive

SCHEMA = 1
CHUNK_CHARS = 1600
OVERLAP = 160
DEFAULT_BUDGET = 12000
KINDS = ("original", "reading_section", "material_finding", "finding", "keyword_navigation")
NOTICE = "Navigation only: original text, AI reading records and findings are distinct. No match is not proof of absence; indexed/structurally complete is not evidence sufficient or a reading acknowledgement."
ALIASES = {
    "利润质量": ("核心利润", "投资收益", "公允价值", "减值", "非经常性损益"),
    "现金质量": ("经营活动现金", "现金及现金等价物", "受限", "财务公司", "吸收存款"),
    "资本配置": ("在建工程", "长期股权投资", "资本支出", "回购", "利润分配"),
    "股东回报": ("分红", "现金股利", "利润分配", "回购", "注销"),
    "分红": ("分红", "现金股利", "利润分配", "派发现金"),
    "受限资金": ("受限", "使用权受到限制", "保证金", "冻结"),
    "母子公司": ("合并范围", "子公司", "少数股东", "母公司", "内部交易"),
    "关联交易": ("关联方", "关联交易", "关联担保", "关联方往来"),
}


def packed(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(packed(value).encode("utf-8")).hexdigest()


def default_db():
    configured = os.environ.get("CFA_INDEX_DB")
    if configured:
        return Path(configured).expanduser().resolve()
    base = Path.home() / ("Library/Caches" if sys.platform == "darwin" else ".cache")
    return base / "company-financial-analysis/evidence.sqlite3"


def db_path(db=None):
    return Path(db).expanduser().resolve() if db is not None else default_db()


def inside(case, relative):
    path = (case / relative).resolve()
    if not path.is_relative_to(case):
        raise ValueError("Case dependency escapes the case directory")
    return path


def rule_sha():
    return digest([archive.sha256(Path(__file__)), archive.sha256(Path(archive.__file__))])


def _connect(db, write=False):
    if write:
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db), timeout=30)
    else:
        conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    if not write:
        conn.execute("PRAGMA query_only=ON")
    return conn


def _schema(conn):
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, SCHEMA):
        raise ValueError("Unsupported evidence index schema; use a new cache path")
    if version == SCHEMA:
        return
    if conn.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone():
        raise ValueError("Refuse to initialize a nonempty unrelated SQLite database")
    conn.executescript("""
        CREATE TABLE cases (
          case_id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, company TEXT NOT NULL,
          code TEXT NOT NULL, fingerprint TEXT NOT NULL, rule_sha TEXT NOT NULL,
          dependencies TEXT NOT NULL, warnings TEXT NOT NULL, synced_at TEXT NOT NULL);
        CREATE TABLE documents (
          doc_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
          source_id TEXT NOT NULL, source_sha256 TEXT NOT NULL, pdf_path TEXT NOT NULL,
          title TEXT NOT NULL, period TEXT NOT NULL, year INTEGER, report_type TEXT,
          page_count INTEGER NOT NULL, extracted INTEGER NOT NULL, reading_complete INTEGER NOT NULL,
          visual_pages TEXT NOT NULL, evidence_gaps TEXT NOT NULL, reading_errors TEXT NOT NULL);
        CREATE TABLE pages (
          doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
          page INTEGER NOT NULL, text TEXT NOT NULL, needs_visual INTEGER NOT NULL,
          PRIMARY KEY(doc_id,page));
        CREATE TABLE entries (
          hit_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
          doc_id TEXT REFERENCES documents(doc_id) ON DELETE CASCADE, kind TEXT NOT NULL,
          topic TEXT NOT NULL, label TEXT NOT NULL, record_status TEXT NOT NULL,
          pages TEXT NOT NULL, page INTEGER, start_offset INTEGER, end_offset INTEGER,
          text TEXT NOT NULL, extra TEXT NOT NULL);
        CREATE INDEX entries_doc ON entries(doc_id);
        CREATE INDEX documents_scope ON documents(case_id,year,report_type);
        CREATE VIRTUAL TABLE entries_fts USING fts5(hit_id UNINDEXED,text,topic,label,tokenize='trigram');
        PRAGMA user_version=1;
    """)


def _check_schema(conn):
    if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA:
        raise ValueError("Unsupported evidence index schema")


def _dependency_hash(path, semantic=False):
    if not path.is_file():
        return None
    if not semantic:
        return archive.sha256(path)
    data = archive.read_json(path)
    # Extraction timestamps, JSON whitespace and derived character counts do not
    # change the source material that was indexed.
    content = {"source_id": data.get("source_id"), "source_sha256": data.get("source_sha256"),
        "pages": [{"pdf_page": p.get("pdf_page"), "text": p.get("text"),
                   "needs_visual_or_ocr": bool(p.get("needs_visual_or_ocr")) or len(str(p.get("text", "")).strip()) < 30}
                  for p in data.get("pages", [])]}
    return digest(content)


def _tracked(path, dependencies, required=False, semantic=False):
    path = Path(path).resolve()
    value = _dependency_hash(path, semantic)
    dependencies[str(path)] = {"kind": "extracted_pages", "sha256": value} if semantic and value is not None else value
    if required and value is None:
        raise ValueError(f"Required source/dependency missing: {path}")
    return value


def _unchanged(dependencies):
    reasons = []
    for name, expected in dependencies.items():
        path = Path(name)
        try:
            current = _dependency_hash(path, isinstance(expected, dict) and expected.get("kind") == "extracted_pages")
        except (ValueError, KeyError, TypeError):
            current = "invalid_dependency"
        target = expected.get("sha256") if isinstance(expected, dict) else expected
        if current != target:
            reasons.append(name)
    return reasons


def _period_year(period):
    match = re.match(r"^(\d{4})(?:\D|$)", str(period))
    return int(match.group(1)) if match else None


def _chunks(text):
    if not text:
        yield 0, 0
        return
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_CHARS)
        if end < len(text):
            newline = text.rfind("\n", max(start + CHUNK_CHARS // 2, end - 400), end)
            if newline >= 0:
                end = newline + 1
        yield start, end
        if end == len(text):
            return
        start = max(start + 1, end - OVERLAP)


def _record(case_id, doc_id, kind, topic, label, status, pages, text, extra=None, start=None, end=None):
    record = {"case_id": case_id, "doc_id": doc_id, "kind": kind, "topic": topic,
              "label": label, "record_status": status, "pages": list(pages),
              "page": pages[0] if len(pages) == 1 else None,
              "start_offset": start, "end_offset": end, "text": text, "extra": extra or {}}
    return {"hit_id": "hit-" + digest(record)[:32], **record}


def _note_text(record):
    fields = ("finding", "statement", "financial_impact", "year_over_year_change", "evidence_gaps", "gaps", "alternatives")
    lines = []
    for key in fields:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(key + ": " + value)
        elif isinstance(value, list) and value:
            lines.append(key + ": " + "; ".join(str(x) for x in value))
    return "\n".join(lines)


def _capture_calc_dependencies(case, ledger, dependencies):
    """Capture only artifacts actually referenced by current findings."""
    if not any(item.get("calculations") for item in ledger.get("findings", [])):
        return
    state_path = case / "case-state.json"
    _tracked(state_path, dependencies)
    if not state_path.is_file():
        return
    state = archive.read_json(state_path)
    sources = {s["source_id"]: s for s in archive.require_case(case).get("sources", [])}
    visited = set()

    def visit(key):
        if key in visited:
            return
        visited.add(key)
        entry = state.get("artifacts", {}).get(key, {})
        if entry.get("path"):
            _tracked(inside(case, entry["path"]), dependencies, True)
        for dep in entry.get("dependencies", []):
            if dep.get("kind") == "artifact":
                visit(dep["key"])
            elif dep.get("kind") == "file":
                _tracked(inside(case, dep["path"]), dependencies, True)
            elif dep.get("kind") == "tool":
                _tracked(Path(dep["path"]), dependencies, True)
            elif dep.get("kind") == "source":
                source = sources.get(dep.get("source_id"))
                if not source:
                    raise ValueError("Calculation depends on an original missing from manifest")
                _tracked(inside(case, source["path"]), dependencies, True)
    for item in ledger.get("findings", []):
        for ref in item.get("calculations", []):
            visit(ref["artifact"])


def snapshot_case(case):
    case = Path(case).expanduser().resolve()
    dependencies, warnings = {}, []
    _tracked(case / "manifest.json", dependencies, True)
    manifest = archive.require_case(case)
    if not isinstance(manifest.get("code"), str) or not manifest["code"]:
        raise ValueError("Case company code must be nonempty text")
    if not isinstance(manifest.get("company"), str) or not manifest["company"]:
        raise ValueError("Case company name is required")
    case_id = "case-" + digest(str(case))[:24]
    result = {"case_id": case_id, "case": str(case), "company": manifest["company"], "code": manifest["code"],
              "documents": [], "entries": [], "dependencies": dependencies, "warnings": warnings, "rule_sha": rule_sha()}
    by_source, source_ids = {}, set()
    for source in manifest.get("sources", []):
        if source.get("kind") != "pdf":
            continue
        sid = source.get("source_id")
        if not isinstance(sid, str) or Path(sid).name != sid or sid in source_ids:
            raise ValueError("Invalid/duplicate PDF source_id")
        source_ids.add(sid)
        if str(source.get("code", manifest["code"])) != manifest["code"]:
            raise ValueError("Archived source company code differs from case")
        original = inside(case, source["path"])
        if _tracked(original, dependencies, True) != source.get("sha256"):
            raise ValueError(f"Original hash mismatch: {sid}")
        total = source.get("validation", {}).get("pages")
        if type(total) is not int or total < 1:
            raise ValueError("Archived PDF requires a positive physical page count")
        directory = inside(case, "workpapers/pdf/" + sid)
        pages_path = directory / "pages.json"
        note_path = inside(case, "reading/" + sid + ".json")
        _tracked(pages_path, dependencies, semantic=True)
        _tracked(note_path, dependencies)
        _tracked(directory / "index.json", dependencies)
        doc_id = "doc-" + digest([case_id, sid, source["sha256"]])[:32]
        doc = {"doc_id": doc_id, "case_id": case_id, "source_id": sid, "source_sha256": source["sha256"],
               "pdf_path": str(original), "title": source.get("title", sid), "period": str(source.get("period", "")),
               "year": _period_year(source.get("period")), "report_type": source.get("report_type", "other"),
               "page_count": total, "extracted": False, "reading_complete": False, "visual_pages": [],
               "evidence_gaps": [], "reading_errors": ["No reading record"], "pages": []}
        result["documents"].append(doc)
        by_source[sid] = doc
        if pages_path.is_file():
            extracted = archive.read_json(pages_path)
            pages = extracted.get("pages")
            if extracted.get("source_id") != sid or extracted.get("source_sha256") != source["sha256"]:
                raise ValueError(f"Extraction identity/hash mismatch: {sid}")
            if not isinstance(pages, list) or len(pages) != total:
                raise ValueError(f"Extraction page count mismatch: {sid}")
            for number, page in enumerate(pages, 1):
                if type(page.get("pdf_page")) is not int or page["pdf_page"] != number or not isinstance(page.get("text"), str):
                    raise ValueError("Extracted pages must be contiguous physical pages with text")
                text = page["text"]
                needs_visual = bool(page.get("needs_visual_or_ocr")) or len(text.strip()) < 30
                doc["pages"].append({"page": number, "text": text, "needs_visual": needs_visual})
                if needs_visual:
                    doc["visual_pages"].append(number)
                for start, end in _chunks(text):
                    result["entries"].append(_record(case_id, doc_id, "original", "", doc["title"],
                        "text_extracted", [number], text[start:end], {"needs_visual_or_ocr": needs_visual}, start, end))
            doc["extracted"] = True
        else:
            warnings.append({"source_id": sid, "reason": "extraction_missing", "next_action": "source extract"})
        navigation_path = directory / "index.json"
        if navigation_path.is_file():
            navigation = archive.read_json(navigation_path)
            if navigation.get("source_id") != sid:
                raise ValueError("Keyword navigation belongs to another source")
            for topic, record in navigation.get("topics", {}).items():
                pages = record.get("pages", [])
                if not pages:
                    continue
                if not archive.valid_pages(pages, total):
                    raise ValueError("Keyword navigation has invalid physical pages")
                result["entries"].append(_record(case_id, doc_id, "keyword_navigation", topic,
                    record.get("label", topic), "keyword_candidates", pages,
                    "Keyword candidates: " + " ".join(str(x) for x in record.get("keywords", [])),
                    {"candidate_pages": pages, "notice": "Deterministic keyword candidates; not evidence of reading or disclosure sufficiency."}))
        if note_path.is_file():
            note = archive.read_json(note_path)
            if note.get("source_id") != sid or note.get("source_sha256") != source["sha256"]:
                raise ValueError(f"Reading record identity/hash mismatch: {sid}")
            if "reading_schema_version" in note and note["reading_schema_version"] != archive.READING_SCHEMA_VERSION:
                raise ValueError("Unsupported reading record version")
            check = archive.check_reading(source, note)
            doc["reading_complete"] = check["record_complete"]
            doc["reading_errors"] = check["errors"]
            doc["evidence_gaps"] = check["evidence_gaps"]
            # Individual notes can be useful before the whole report is complete,
            # but an unverified cover must not lend them a verified identity.
            identity = note.get("identity_check", {})
            identity_ok = identity.get("status") == "read" and identity.get("checked_period") == source.get("period")
            if "reading_schema_version" in note:
                identity_ok = identity_ok and str(identity.get("checked_code")) == manifest["code"]
            else:
                identity_ok = identity_ok and identity.get("checked_company") == source.get("company")
            if identity_ok:
                for topic, record in note.get("sections", {}).items():
                    if record.get("status") not in {"read", "not_disclosed", "evidence_gap"} or not str(record.get("finding", "")).strip():
                        continue
                    if not archive.valid_pages(record.get("pages"), total):
                        raise ValueError(f"Reading section has invalid physical pages: {sid}/{topic}")
                    result["entries"].append(_record(case_id, doc_id, "reading_section", topic,
                        record.get("label", topic), record["status"], record["pages"], _note_text(record), record))
                for i, record in enumerate(note.get("material_findings", [])):
                    if not isinstance(record, dict) or not archive.valid_pages(record.get("pages"), total) or not str(record.get("finding", "")).strip():
                        raise ValueError(f"Material finding has invalid shape/pages: {sid}")
                    result["entries"].append(_record(case_id, doc_id, "material_finding", "material_findings",
                        f"Material finding {i + 1}", "reading_record", record["pages"], _note_text(record), record))
    ledger_path = case / "analysis/findings.json"
    _tracked(ledger_path, dependencies)
    if ledger_path.is_file():
        import findings
        import case_state
        _tracked(Path(findings.__file__), dependencies, True)
        _tracked(Path(case_state.__file__), dependencies, True)
        ledger = findings.load_ledger(case)
        _capture_calc_dependencies(case, ledger, dependencies)
        check = findings.validate_ledger(case)
        if not check["valid"]:
            raise ValueError("Current findings ledger is stale/invalid; review findings before sync: " + packed(check["errors"]))
        for finding in ledger["findings"]:
            refs = finding.get("sources", [])
            for ref in refs or [None]:
                doc = by_source.get(ref["source_id"]) if ref else None
                if ref and not doc:
                    raise ValueError("Finding refers to a PDF absent from manifest")
                result["entries"].append(_record(case_id, doc["doc_id"] if doc else None, "finding", "findings",
                    finding["title"], finding["status"], ref["pages"] if ref else [], _note_text(finding), finding))
    if _unchanged(dependencies) or result["rule_sha"] != rule_sha():
        raise ValueError("Source/research inputs or indexing rules changed during capture; no index committed")
    result["fingerprint"] = digest([dependencies, result["rule_sha"]])
    return result


def _counts(conn, case_ids=None):
    where, args = (" WHERE case_id IN (" + ",".join("?" for _ in case_ids) + ")", list(case_ids)) if case_ids else ("", [])
    result = {"cases": conn.execute("SELECT count(*) FROM cases" + (where if case_ids else ""), args).fetchone()[0],
              "documents": conn.execute("SELECT count(*) FROM documents" + where, args).fetchone()[0],
              "pages": conn.execute("SELECT coalesce(sum(page_count),0) FROM documents" + where, args).fetchone()[0]}
    for kind, field in (("original", "original_entries"), ("reading_section", "reading_sections"), ("material_finding", "material_findings"), ("finding", "findings")):
        clause = where + (" AND" if where else " WHERE") + " kind=?"
        result[field] = conn.execute("SELECT count(*) FROM entries" + clause, args + [kind]).fetchone()[0]
    return result


def sync(cases, db=None):
    db = db_path(db)
    paths = list(dict.fromkeys(str(Path(p).expanduser().resolve()) for p in cases))
    if not paths:
        raise ValueError("Provide at least one --case")
    snapshots = [snapshot_case(path) for path in paths]
    # A cache is an output; never allow it to occupy source or dependency paths.
    if any(str(db) in snap["dependencies"] for snap in snapshots):
        raise ValueError("Index database would overwrite a research input")
    conn = _connect(db, True)
    reports = []
    try:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        for snap in snapshots:
            previous = conn.execute("SELECT fingerprint FROM cases WHERE case_id=?", (snap["case_id"],)).fetchone()
            changed = not previous or previous["fingerprint"] != snap["fingerprint"]
            if changed:
                conn.execute("DELETE FROM entries_fts WHERE hit_id IN (SELECT hit_id FROM entries WHERE case_id=?)", (snap["case_id"],))
                conn.execute("DELETE FROM cases WHERE case_id=?", (snap["case_id"],))
                conn.execute("INSERT INTO cases VALUES(?,?,?,?,?,?,?,?,?)", (snap["case_id"], snap["case"], snap["company"], snap["code"], snap["fingerprint"], snap["rule_sha"], packed(snap["dependencies"]), packed(snap["warnings"]), archive.now()))
                for doc in snap["documents"]:
                    conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(doc[k] for k in ("doc_id", "case_id", "source_id", "source_sha256", "pdf_path", "title", "period", "year", "report_type", "page_count")) + (int(doc["extracted"]), int(doc["reading_complete"]), packed(doc["visual_pages"]), packed(doc["evidence_gaps"]), packed(doc["reading_errors"])))
                    conn.executemany("INSERT INTO pages VALUES(?,?,?,?)", [(doc["doc_id"], p["page"], p["text"], int(p["needs_visual"])) for p in doc["pages"]])
                conn.executemany("INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", [(e["hit_id"], e["case_id"], e["doc_id"], e["kind"], e["topic"], e["label"], e["record_status"], packed(e["pages"]), e["page"], e["start_offset"], e["end_offset"], e["text"], packed(e["extra"])) for e in snap["entries"]])
                conn.executemany("INSERT INTO entries_fts(hit_id,text,topic,label) VALUES(?,?,?,?)", [(e["hit_id"], e["text"], e["topic"], e["label"]) for e in snap["entries"]])
            reports.append({"case_id": snap["case_id"], "case": snap["case"], "company": snap["company"], "code": snap["code"], "status": "updated" if changed else "unchanged", "documents": len(snap["documents"]), "pages": sum(d["page_count"] for d in snap["documents"]), "warnings": snap["warnings"]})
        for snap in snapshots:
            if _unchanged(snap["dependencies"]) or snap["rule_sha"] != rule_sha():
                raise ValueError("Inputs/rules changed during index write; transaction rolled back")
        totals = _counts(conn)
        conn.commit()
        return {"status": "ready", "db": str(db), "cases": reports, "totals": totals, "notice": NOTICE}
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _missing(db):
    return {"status": "missing", "db": str(db), "cases": [], "totals": {}, "next_action": "index sync --case <case>", "notice": NOTICE}


def _select_cases(conn, cases=None, codes=None, companies=None):
    rows = [dict(row) for row in conn.execute("SELECT * FROM cases ORDER BY code,path")]
    paths = {str(Path(x).expanduser().resolve()) for x in (cases or [])}
    codes = set(codes or [])
    names = list(companies or [])
    if names:
        matched = set()
        for name in names:
            matches = [r for r in rows if name in r["company"] or name == r["code"]]
            if len({r["code"] for r in matches}) > 1:
                raise ValueError(f"Company name is ambiguous; use --code: {name}")
            matched.update(r["case_id"] for r in matches)
        rows = [r for r in rows if r["case_id"] in matched]
    if paths:
        rows = [r for r in rows if r["path"] in paths]
    if codes:
        rows = [r for r in rows if r["code"] in codes]
    return rows


def _unmatched_scope(selected, cases=None, codes=None, companies=None):
    paths = {row["path"] for row in selected}
    actual_codes = {row["code"] for row in selected}
    return {
        "cases": [str(Path(x).expanduser().resolve()) for x in (cases or []) if str(Path(x).expanduser().resolve()) not in paths],
        "codes": [code for code in (codes or []) if code not in actual_codes],
        "companies": [name for name in (companies or []) if not any(name in row["company"] or name == row["code"] for row in selected)],
    }


def _scope_error(db, selected, cases=None, codes=None, companies=None):
    unmatched = _unmatched_scope(selected, cases, codes, companies)
    if any(unmatched.values()):
        return {"status": "not_indexed", "db": str(db), "unmatched_scope": unmatched, "hits": [],
                "next_action": "index sync --case <requested case>; use the full stored company name or code", "notice": NOTICE}
    return None


def _freshness(rows, verify=True):
    current_rule = rule_sha()
    reports = []
    for row in rows:
        reasons = []
        if row["rule_sha"] != current_rule:
            reasons.append("Indexing rules changed")
        if verify:
            changed = _unchanged(json.loads(row["dependencies"]))
            reasons.extend("Dependency changed/missing: " + name for name in changed[:10])
            if len(changed) > 10:
                reasons.append(f"{len(changed) - 10} additional changed dependencies")
        reports.append({"case_id": row["case_id"], "case": row["path"], "company": row["company"], "code": row["code"], "status": "stale" if reasons else "ready", "reasons": reasons})
    return reports


def status_report(db=None, case=None, verify=True):
    db = db_path(db)
    if not db.is_file():
        return _missing(db)
    conn = _connect(db)
    try:
        _check_schema(conn)
        selected = _select_cases(conn, [case] if isinstance(case, (str, Path)) else case)
        reports = _freshness(selected, verify)
        status = "not_indexed" if case and not selected else "stale" if any(x["status"] == "stale" for x in reports) else "ready"
        return {"status": status, "db": str(db), "verified_dependencies": bool(verify), "cases": reports,
                "totals": _counts(conn, [r["case_id"] for r in selected]) if selected else {"cases": 0, "documents": 0, "pages": 0},
                "next_action": "index sync --case <case>" if status in {"stale", "not_indexed"} else None, "notice": NOTICE}
    finally:
        conn.close()


def expand_terms(query):
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise ValueError("Query must contain 1–2000 characters")
    direct = re.findall(r"[+-]?\d[\d,]*(?:\.\d+)?%?|[^\W\d_]+(?:_[A-Za-z]+)*", query, re.UNICODE)
    terms = list(dict.fromkeys(t for t in direct if len(t) >= 2 or t.isdigit()))
    for label, words in ALIASES.items():
        if label in query:
            terms.extend(words)
    for topic, (label, words) in archive.TOPICS.items():
        if topic == query.strip() or label == query.strip():
            terms.extend(words)
        else:
            terms.extend(word for word in words if word in query)
    return list(dict.fromkeys(terms))[:40]


def _filter_documents(conn, selected, start_year=None, end_year=None, report_type=None):
    if (start_year is None) != (end_year is None):
        raise ValueError("Provide both --start-year and --end-year")
    if start_year is not None and (not 1900 <= start_year <= end_year <= 2200):
        raise ValueError("Invalid year range")
    if not selected:
        return []
    ids = [r["case_id"] for r in selected]
    sql = "SELECT d.*,c.company,c.code,c.path AS case_path FROM documents d JOIN cases c USING(case_id) WHERE d.case_id IN (" + ",".join("?" for _ in ids) + ")"
    params = list(ids)
    if start_year is not None:
        sql += " AND d.year BETWEEN ? AND ?"
        params += [start_year, end_year]
    if report_type:
        sql += " AND d.report_type=?"
        params.append(report_type)
    return [dict(row) for row in conn.execute(sql + " ORDER BY c.code,d.year,d.period,d.source_id", params)]


def _coverage(selected, documents, start_year=None, end_year=None, match_counts=None, returned_counts=None):
    grouped = defaultdict(list)
    for doc in documents:
        grouped[(doc["code"], doc["year"])].append(doc)
    groups = []
    identities = {r["code"]: r["company"] for r in selected}
    for code, company in identities.items():
        years = range(start_year, end_year + 1) if start_year is not None else sorted({key[1] for key in grouped if key[0] == code}, key=lambda y: (y is None, y or 0))
        for year in years:
            docs = grouped.get((code, year), [])
            report_types = defaultdict(int)
            for doc in docs:
                report_types[doc["report_type"] or "unknown"] += 1
            key = (code, year)
            total_hits = (match_counts or {}).get(key, 0)
            returned = (returned_counts or {}).get(key, 0)
            state = "not_archived" if not docs else "available"
            if docs and match_counts is not None:
                state = "no_match" if not total_hits else "matched" if returned else "omitted_by_budget"
            groups.append({"code": code, "company": company, "year": year, "documents": len(docs),
                "report_types": dict(sorted(report_types.items())),
                "pages": sum(d["page_count"] for d in docs), "extracted_documents": sum(d["extracted"] for d in docs),
                "reading_complete": sum(d["reading_complete"] for d in docs),
                "visual_required_pages": sum(len(json.loads(d["visual_pages"])) for d in docs),
                "evidence_gaps": sum(len(json.loads(d["evidence_gaps"])) for d in docs),
                "total_hits": total_hits, "returned_hits": returned, "state": state})
    return {"groups": groups, "truncated": False, "omitted_groups": 0,
            "notice": "Coverage is by archived report year, not the comparison year mentioned inside a page. Reading counts are structural records; visual/OCR requirements and evidence gaps remain separate."}


def _stale_result(db, reports):
    return {"status": "stale", "db": str(db), "cases": reports, "hits": [],
            "next_action": "index sync --case <stale case>", "notice": "No stale cached evidence returned. " + NOTICE}


def _hit(row, terms=None, full=False):
    text = row["text"]
    start, end = row["start_offset"], row["end_offset"]
    if not full and len(text) > 480:
        positions = [text.lower().find(t.lower()) for t in (terms or []) if t.lower() in text.lower()]
        relative = max(0, (min(positions) if positions else 0) - 100)
        text = text[relative:relative + 480]
        if start is not None:
            start += relative
            end = start + len(text)
    original = row["kind"] == "original"
    return {"hit_id": row["hit_id"], "kind": row["kind"], "case_id": row["case_id"],
        "case": row["case_path"], "company": row["company"], "code": row["code"],
        "period": row["period"], "year": row["year"], "source_id": row["source_id"],
        "source_sha256": row["source_sha256"], "pdf_path": row["pdf_path"],
        "pages": json.loads(row["pages"]), "start_offset": start, "end_offset": end,
        "text": text, "record_status": row["record_status"], "topic": row["topic"], "label": row["label"],
        "text_origin": "pdf_extracted" if original else "deterministic_keyword_navigation" if row["kind"] == "keyword_navigation" else "ai_research_record",
        "requires_original_verification": not original, "extra": json.loads(row["extra"]) if full else None}


ENTRY_SELECT = """SELECT e.*,d.source_id,d.source_sha256,d.pdf_path,d.period,d.year,d.page_count,
    c.company,c.code,c.path AS case_path FROM entries e JOIN cases c ON c.case_id=e.case_id
    LEFT JOIN documents d ON d.doc_id=e.doc_id """


def _budget(value, max_chars):
    if type(max_chars) is not int or max_chars < 700:
        raise ValueError("--max-chars must be at least 700")
    while len(packed(value)) + 1 > max_chars and value.get("hits"):
        value["hits"].pop()
        value["truncated"] = True
        counts = defaultdict(int)
        for hit in value["hits"]:
            counts[(hit["code"], hit["year"])] += 1
        for group in value.get("coverage", {}).get("groups", []):
            group["returned_hits"] = counts[(group["code"], group["year"])]
            if group["total_hits"]:
                group["state"] = "matched" if group["returned_hits"] else "omitted_by_budget"
    groups = value.get("coverage", {}).get("groups", [])
    while len(packed(value)) + 1 > max_chars and groups:
        groups.pop()
        value["coverage"]["truncated"] = True
        value["coverage"]["omitted_groups"] += 1
        value["truncated"] = True
    if len(packed(value)) + 1 > max_chars:
        return {"status": "budget_too_small", "hits": [], "truncated": True,
                "minimum_required_chars": len(packed(value)) + 1,
                "next_action": "Increase --max-chars or narrow company/year filters", "notice": NOTICE}
    return value


def search(query, db=None, cases=None, codes=None, companies=None, start_year=None, end_year=None,
           report_type=None, limit=20, max_chars=DEFAULT_BUDGET, kinds=None):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("--limit must be between 1 and 200")
    if kinds is not None and (not kinds or any(kind not in KINDS for kind in kinds)):
        raise ValueError("Kinds must be selected from: " + ", ".join(KINDS))
    terms = expand_terms(query)
    numeric_query = query.strip() if re.fullmatch(r"[+-]?\d[\d,]*(?:\.\d+)?%?", query.strip()) else None
    db = db_path(db)
    if not db.is_file():
        return _budget({**_missing(db), "hits": [], "truncated": False}, max_chars)
    conn = _connect(db)
    try:
        _check_schema(conn)
        selected = _select_cases(conn, cases, codes, companies)
        scope_error = _scope_error(db, selected, cases, codes, companies)
        if scope_error:
            return _budget(scope_error, max_chars)
        reports = _freshness(selected)
        if any(r["status"] == "stale" for r in reports):
            return _budget(_stale_result(db, reports), max_chars)
        docs = _filter_documents(conn, selected, start_year, end_year, report_type)
        doc_ids, case_ids = [d["doc_id"] for d in docs], [c["case_id"] for c in selected]
        candidates = set()
        long_terms = [t for t in terms if len(t) >= 3]
        if long_terms:
            expression = " OR ".join('"' + t.replace('"', '""') + '"' for t in long_terms)
            candidates.update(row[0] for row in conn.execute("SELECT hit_id FROM entries_fts WHERE entries_fts MATCH ?", (expression,)))
        short_terms = [t for t in terms if len(t) < 3]
        for term in short_terms:
            candidates.update(row[0] for row in conn.execute("SELECT hit_id FROM entries WHERE instr(lower(text),lower(?))>0 OR instr(lower(label),lower(?))>0 OR instr(topic,?)>0", (term, term, term)))
        rows = []
        # Scope before emitting evidence; calculation-only findings have no inferred year.
        for offset in range(0, len(candidates), 500):
            ids = sorted(candidates)[offset:offset + 500]
            found = conn.execute(ENTRY_SELECT + " WHERE e.hit_id IN (" + ",".join("?" for _ in ids) + ")", ids)
            for row in found:
                if kinds is not None and row["kind"] not in kinds:
                    continue
                if row["case_id"] not in case_ids:
                    continue
                if row["doc_id"] not in doc_ids and not (row["doc_id"] is None and start_year is None and not report_type):
                    continue
                item = dict(row)
                if numeric_query and not re.search(r"(?<![\d.,+\-])" + re.escape(numeric_query) + r"(?![\d.,])", item["text"]):
                    continue
                body = (item["text"] + " " + item["label"] + " " + item["topic"]).lower()
                item["score"] = sum(3 if t in query else 1 for t in terms if t.lower() in body)
                rows.append(item)
        # Keyword lists are generated navigation, so accumulating more keywords
        # must never displace actual matched passages or research records.
        rows.sort(key=lambda r: (r["kind"] == "keyword_navigation", -r["score"], r["kind"] != "original", r["hit_id"]))
        queues = defaultdict(deque)
        matches = defaultdict(int)
        for row in rows:
            key = (row["code"], row["year"])
            matches[key] += 1
            queues[key].append(row)
        ordered, returned = [], defaultdict(int)
        keys = sorted(queues, key=lambda k: (k[0], k[1] is None, k[1] or 0))
        while len(ordered) < limit and any(queues.values()):
            for key in keys:
                if queues[key] and len(ordered) < limit:
                    ordered.append(_hit(queues[key].popleft(), terms))
                    returned[key] += 1
        result = {"status": "ready", "db": str(db), "query": query, "expanded_terms": terms,
                  "kinds": list(dict.fromkeys(kinds)) if kinds is not None else list(KINDS),
                  "hits": ordered, "total_hits": len(rows), "truncated": len(ordered) < len(rows),
                  "coverage": _coverage(selected, docs, start_year, end_year, matches, returned),
                  "unscoped_findings": matches.get(next((k for k in matches if k[1] is None), (None, None)), 0),
                  "notice": NOTICE}
        return _budget(result, max_chars)
    finally:
        conn.close()


def coverage(db=None, cases=None, codes=None, companies=None, start_year=None, end_year=None, report_type=None, max_chars=DEFAULT_BUDGET):
    db = db_path(db)
    if not db.is_file():
        return _budget(_missing(db), max_chars)
    conn = _connect(db)
    try:
        _check_schema(conn)
        selected = _select_cases(conn, cases, codes, companies)
        scope_error = _scope_error(db, selected, cases, codes, companies)
        if scope_error:
            return _budget(scope_error, max_chars)
        reports = _freshness(selected)
        if any(r["status"] == "stale" for r in reports):
            return _budget(_stale_result(db, reports), max_chars)
        docs = _filter_documents(conn, selected, start_year, end_year, report_type)
        return _budget({"status": "ready", "db": str(db), "coverage": _coverage(selected, docs, start_year, end_year), "truncated": False, "notice": NOTICE}, max_chars)
    finally:
        conn.close()


def fetch(hit_id, db=None, page=None, offset=0, max_chars=DEFAULT_BUDGET):
    if type(offset) is not int or offset < 0:
        raise ValueError("--offset must be nonnegative")
    if type(max_chars) is not int or max_chars < 700:
        raise ValueError("--max-chars must be at least 700")
    db = db_path(db)
    if not db.is_file():
        return _budget(_missing(db), max_chars)
    conn = _connect(db)
    try:
        _check_schema(conn)
        row = conn.execute(ENTRY_SELECT + " WHERE e.hit_id=?", (hit_id,)).fetchone()
        if not row:
            raise ValueError("Unknown hit_id; it may belong to an older index version")
        selected = _select_cases(conn, [row["case_path"]])
        reports = _freshness(selected)
        if any(r["status"] == "stale" for r in reports):
            return _budget(_stale_result(db, reports), max_chars)
        hit = _hit(row, full=True)
        if row["doc_id"] is None:
            return _budget({"status": "ready", "db": str(db), "hit": hit, "segments": [], "next_offset": None,
                            "truncated": False, "notice": "no_bound_pdf: calculation-only finding; inspect its registered calculation references. " + NOTICE}, max_chars)
        pages = hit["pages"]
        page = page if page is not None else pages[0]
        if type(page) is not int or not 1 <= page <= row["page_count"]:
            raise ValueError("--page must be a physical PDF page within this source")
        original = conn.execute("SELECT * FROM pages WHERE doc_id=? AND page=?", (row["doc_id"], page)).fetchone()
        if not original:
            raise ValueError("Page has no extracted text; run source extract and index sync")
        text = original["text"]
        if offset > len(text):
            raise ValueError("--offset exceeds page text length")
        # Full record fields may be much larger than a page; return them through
        # search/finding list while this operation budgets original-page delivery.
        hit.pop("text", None)
        hit.pop("extra", None)
        result = {"status": "ready", "db": str(db), "hit": hit, "segments": [], "truncated": False,
                  "next_offset": None, "next_page": page + 1 if page < row["page_count"] else None,
                  "notice": "Original-page navigation; this operation does not acknowledge reading. " + NOTICE}
        def with_length(length):
            result["segments"] = [{"pdf_page": page, "start_offset": offset, "end_offset": offset + length,
                "page_characters": len(text), "text": text[offset:offset + length], "needs_visual_or_ocr": bool(original["needs_visual"])}]
            result["truncated"] = offset + length < len(text)
            result["next_offset"] = offset + length if result["truncated"] else None
            return len(packed(result)) + 1
        lo, hi = 0, len(text) - offset
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if with_length(mid) <= max_chars:
                lo = mid
            else:
                hi = mid - 1
        with_length(lo)
        if len(packed(result)) + 1 > max_chars or (lo == 0 and offset < len(text)):
            return {"status": "budget_too_small", "segments": [], "truncated": True, "next_offset": offset,
                    "next_action": "Increase --max-chars; no page characters delivered"}
        return result
    finally:
        conn.close()


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sync_p = sub.add_parser("sync", help="Validate and incrementally import case originals and research records")
    sync_p.add_argument("--case", type=Path, action="append", required=True)
    status_p = sub.add_parser("status", help="Read cache versions and dependency freshness without writing")
    status_p.add_argument("--case", type=Path, action="append")
    status_p.add_argument("--no-verify", action="store_true", help="Inspect stored metadata/rule version only; skip dependency hashes")
    for name in ("search", "coverage"):
        cmd = sub.add_parser(name, help="Search original/AI records" if name == "search" else "List company/year source and reading coverage")
        for key in ("case", "code", "company"):
            cmd.add_argument("--" + key, action="append", type=Path if key == "case" else str)
        cmd.add_argument("--start-year", type=int)
        cmd.add_argument("--end-year", type=int)
        cmd.add_argument("--report-type", choices=["annual", "half-year", "half_year", "half", "q1", "q3", "other"])
        cmd.add_argument("--max-chars", type=int, default=DEFAULT_BUDGET)
        if name == "search":
            cmd.add_argument("--query", required=True, help="Literal terms separated by spaces; known financial topics expand through the versioned dictionary")
            cmd.add_argument("--limit", type=int, default=20)
            cmd.add_argument("--kind", action="append", choices=KINDS,
                             help="Return only selected record kinds; repeatable. Generated keyword navigation ranks after original/research matches")
    fetch_p = sub.add_parser("fetch", help="Expand a hit to its exact original page, with explicit offset continuation")
    fetch_p.add_argument("--hit-id", required=True)
    fetch_p.add_argument("--page", type=int)
    fetch_p.add_argument("--offset", type=int, default=0)
    fetch_p.add_argument("--max-chars", type=int, default=DEFAULT_BUDGET)
    for cmd in sub.choices.values():
        cmd.add_argument("--db", type=Path, help="Cache path; default CFA_INDEX_DB or local user cache directory")
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # argparse's negative-number special case excludes comma-formatted amounts.
    # A value directly following --query is data, including a leading minus sign.
    for i, value in enumerate(argv[:-1]):
        if value == "--query" and not argv[i + 1].startswith("--"):
            argv[i:i + 2] = ["--query=" + argv[i + 1]]
            break
    args = parser().parse_args(argv)
    try:
        if args.command == "sync":
            result = sync(args.case, args.db)
        elif args.command == "status":
            result = status_report(args.db, args.case, not args.no_verify)
        elif args.command == "fetch":
            result = fetch(args.hit_id, args.db, args.page, args.offset, args.max_chars)
        else:
            options = {"db": args.db, "cases": args.case, "codes": args.code, "companies": args.company,
                       "start_year": args.start_year, "end_year": args.end_year,
                       "report_type": args.report_type, "max_chars": args.max_chars}
            result = search(args.query, limit=args.limit, kinds=args.kind, **options) if args.command == "search" else coverage(**options)
        print(packed(result))
        return 0 if result["status"] == "ready" else 2
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        print(packed({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
