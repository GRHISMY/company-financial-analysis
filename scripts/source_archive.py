#!/usr/bin/env python3
"""Archive original reports/exports, extract page-addressable PDF text, audit reading notes.

Python standard library; PDF validation/extraction requires Poppler pdfinfo/pdftotext.
Public-URL downloads are for already verified report links. Logged-in webpage export
and download remain browser actions; this tool never imports browser credentials.
Run a subcommand with --help for its contract.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile

VERSION = 1
DIRECTORIES = ("raw/pdf", "raw/tables", "workpapers/pdf", "reading", "analysis", "charts")
TOPICS = {
    "business": ("业务、经营回顾与管理层讨论", ("管理层讨论", "经营情况讨论", "经营情况", "主要业务", "经营计划", "风险因素")),
    "governance": ("治理、控制人及股东利益", ("公司治理", "控股股东", "实际控制人", "分红", "利润分配", "股权激励")),
    "significant_matters": ("重大事项、担保承诺诉讼及资金占用", ("重大事项", "重要事项", "担保", "承诺事项", "或有事项", "诉讼", "资金占用")),
    "audit": ("审计意见、关键审计事项与内控", ("审计报告", "审计意见", "关键审计事项", "内部控制", "未经审计")),
    "statements": ("合并及母公司三表、权益变动", ("合并资产负债表", "母公司资产负债表", "合并利润表", "母公司利润表", "现金流量表", "所有者权益变动表")),
    "accounting": ("政策估计变更、收入确认及重述", ("会计政策", "会计估计", "收入确认", "收入的确认", "差错更正", "追溯调整", "返利")),
    "consolidation": ("母子公司、合并范围及权益归属", ("合并范围", "子公司", "联营企业", "合营企业", "少数股东", "长期股权投资")),
    "related_parties": ("关联方、关联交易及往来余额", ("关联方", "关联交易", "关联担保", "关联方应收", "关联方往来")),
    "cash_debt": ("货币资金、受限资产、借款与财务公司", ("货币资金", "现金及现金等价物", "受限", "使用权受到限制", "借款", "财务公司", "吸收存款")),
    "working_capital": ("应收存货合同负债与经营现金桥", ("应收账款", "存货", "合同负债", "预收款项", "补充资料", "经营活动现金")),
    "assets_impairment": ("资产质量、减值与非经常收益", ("减值", "坏账", "公允价值", "投资收益", "政府补助", "非经常性损益")),
    "segments_capex": ("分部产品渠道、资本开支、研发与产能", ("分部信息", "分行业", "分产品", "分地区", "在建工程", "研发", "产能", "资本支出")),
    "subsequent_events": ("期后事项与跨期变化", ("期后事项", "资产负债表日后", "其他重要事项", "持续经营")),
}
STATUSES = {"unread", "read", "not_disclosed", "evidence_gap"}
READING_SCHEMA_VERSION = 2
FOCUSED_TOPICS = {
    "q1": ("business", "statements", "significant_matters", "governance"),
    "q3": ("business", "statements", "significant_matters", "governance"),
    "other": ("significant_matters",),
}


def required_topics(source):
    """The archive report type determines requirements, never a user-edited empty list."""
    return list(FOCUSED_TOPICS.get(source.get("report_type"), TOPICS))


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@contextlib.contextmanager
def manifest_lock(case: Path):
    """Serialize archive mutations on POSIX; files remain usable on other platforms."""
    with (case / ".archive.lock").open("a", encoding="utf-8") as stream:
        try:
            import fcntl
        except ImportError:
            fcntl = None
        if fcntl:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def safe_component(value: str) -> str:
    value = re.sub(r"[\x00-\x1f/\\:*?\"<>|]+", "_", value).strip(" ._")
    # Leave room for the period/hash suffix even on filesystems with 255-byte names.
    return (value or "source").encode("utf-8")[:100].decode("utf-8", errors="ignore")


def require_case(case: Path):
    manifest = case / "manifest.json"
    if not manifest.is_file():
        raise ValueError(f"Case not initialized: {case}; run init first")
    data = read_json(manifest)
    if data.get("schema_version") != VERSION:
        raise ValueError("Unsupported manifest schema_version")
    return data


def command_init(args):
    case = args.case.resolve()
    case.mkdir(parents=True, exist_ok=True)
    with manifest_lock(case):
        if (case / "manifest.json").exists():
            data = require_case(case)
            if (data["company"], data.get("code")) != (args.company, args.code):
                raise ValueError("Existing case belongs to a different company/code")
            result = {"case": str(case), "status": "existing", "sources": len(data["sources"])}
        else:
            data = {
                "schema_version": VERSION, "company": args.company, "code": args.code,
                "requested_period": {"start": args.start, "end": args.end},
                "created_at": now(), "sources": [],
            }
            atomic_json(case / "manifest.json", data)
            result = {"case": str(case), "status": "created", "sources": 0}
        for directory in DIRECTORIES:
            (case / directory).mkdir(parents=True, exist_ok=True)
    return result


def public_url(value: str) -> str:
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in {"https", "http"} or not parts.hostname:
        raise ValueError("Expected an explicit http(s) URL")
    if parts.username or parts.password:
        raise ValueError("Do not place credentials in source URLs")
    credential_parameters = {"token", "access_token", "refresh_token", "authorization", "password", "passwd", "cookie", "session", "sessionid", "api_key", "apikey"}
    if any(key.lower() in credential_parameters for key, _ in urllib.parse.parse_qsl(parts.query)):
        raise ValueError("Do not archive credential/session parameters in source URLs")
    hostname = parts.hostname.lower().rstrip(".")
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith((".local", ".localhost")):
        raise ValueError("--url accepts public report URLs, not local services")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address and not address.is_global:
        raise ValueError("--url accepts public report URLs, not private addresses")
    return value


def validate_settings(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if any(word in str(key).lower() for word in ("password", "cookie", "authorization", "token", "api_key", "secret")):
                raise ValueError("Capture settings describe report controls; credentials/cookies must not be saved")
            validate_settings(item)
    elif isinstance(value, list):
        for item in value:
            validate_settings(item)


class PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, destination: Path, max_bytes: int):
    public_url(url)
    # No cookie processor, Authorization, Referer or browser-profile access.
    opener = urllib.request.build_opener(PublicRedirectHandler())
    request = urllib.request.Request(url, headers={"User-Agent": "financial-report-archive/1.0"})
    with opener.open(request, timeout=60) as response, destination.open("wb") as stream:
        total = 0
        while part := response.read(1024 * 1024):
            total += len(part)
            if total > max_bytes:
                raise ValueError(f"Download exceeded {max_bytes} bytes")
            stream.write(part)
        return {"final_url": response.geturl(), "http_content_type": response.headers.get("Content-Type")}


def poppler(program: str, *arguments: str) -> str:
    binary = shutil.which(program)
    if not binary:
        raise ValueError(f"{program} not found; install Poppler and retry")
    result = subprocess.run([binary, *arguments], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    if result.returncode:
        raise ValueError(f"{program} failed: {result.stderr.strip()[:1500]}")
    return result.stdout


def validate_file(path: Path, kind: str, encoding: str):
    if path.stat().st_size == 0:
        raise ValueError("Empty download/export")
    with path.open("rb") as stream:
        header = stream.read(1024)
    if kind == "pdf":
        if not header.startswith(b"%PDF-"):
            raise ValueError("File is not a PDF (may be an HTML/login/error page)")
        info = poppler("pdfinfo", str(path))
        match = re.search(r"^Pages:\s+(\d+)\s*$", info, re.MULTILINE)
        if not match or int(match.group(1)) < 1:
            raise ValueError("pdfinfo could not establish page count")
        return {"pages": int(match.group(1)), "pdfinfo": info}
    if kind == "csv":
        text = path.read_text(encoding=encoding)
        if re.match(r"\s*(?:<!doctype|<html|<head|<body)", text, re.IGNORECASE):
            raise ValueError("File is HTML, not a CSV export")
        try:
            dialect = csv.Sniffer().sniff(text[:32768], delimiters=",\t;")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ","
        try:
            rows = list(csv.reader(text.splitlines(), delimiter=delimiter, strict=True))
        except csv.Error as exc:
            raise ValueError(f"Invalid CSV: {exc}") from exc
        populated = [row for row in rows if any(cell.strip() for cell in row)]
        if len(populated) < 2 or max(map(len, populated)) < 2:
            raise ValueError("Export must contain at least two populated rows and two columns")
        return {"encoding": encoding, "delimiter": delimiter, "rows": len(rows), "max_columns": max(map(len, rows)), "column_counts": sorted(set(map(len, populated)))}
    if kind == "xlsx":
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if not {"[Content_Types].xml", "xl/workbook.xml"} <= names:
                    raise ValueError("ZIP file is not an XLSX workbook")
                broken = archive.testzip()
                if broken:
                    raise ValueError(f"Corrupt XLSX member: {broken}")
                return {"sheets_xml": sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))}
        except zipfile.BadZipFile as exc:
            raise ValueError("File is not a valid XLSX workbook") from exc
    raise ValueError(f"Unsupported file kind: {kind}")


def command_ingest(args):
    case = args.case.resolve()
    require_case(case)
    source_url = args.source_url or args.url
    if not source_url:
        raise ValueError("--source-url is required for a local browser download")
    public_url(source_url)
    if args.url and not args.verified_public:
        raise ValueError("Public downloading requires --verified-public after checking the report link in the browser/source")
    settings = read_json(args.settings) if args.settings else {}
    if not isinstance(settings, dict):
        raise ValueError("--settings JSON must contain an object")
    validate_settings(settings)
    if args.kind == "pdf" and args.report_type == "statement-export":
        raise ValueError("A statement export is a table; use its actual format")
    with tempfile.TemporaryDirectory(prefix=".source-stage-", dir=case) as temporary:
        staged = Path(temporary) / ("download." + args.kind)
        capture = {"method": "verified-public-url" if args.url else "browser-or-local-file", "source_url": source_url, "captured_at": now(), "settings": settings}
        if args.url:
            capture.update(download(args.url, staged, args.max_bytes))
        else:
            source = args.file.resolve()
            if not source.is_file():
                raise ValueError(f"Local file not found: {source}")
            shutil.copyfile(source, staged)
            capture["local_original_path"] = str(source)
        validation = validate_file(staged, args.kind, args.encoding)
        digest = sha256(staged)
        source_id = args.kind + "-" + digest[:16]
        with manifest_lock(case):
            data = require_case(case)
            existing = next((s for s in data["sources"] if s["sha256"] == digest and s["kind"] == args.kind), None)
            if existing:
                destination = case / existing["path"]
                if not destination.exists() or sha256(destination) != digest:
                    raise ValueError("Previously archived original is missing or changed; restore it before reusing the manifest")
                stable_fields = ("title", "period", "report_type")
                proposed = {"title": args.title, "period": args.period, "report_type": args.report_type}
                if any(existing.get(field) != proposed[field] for field in stable_fields):
                    capture["alternate_description"] = proposed
                # Repeated ingestion stays idempotent; a distinct URL/settings is another observation.
                identity = lambda c: (c.get("method"), c.get("source_url"), c.get("settings"))
                if not any(identity(c) == identity(capture) for c in existing["captures"]):
                    existing["captures"].append(capture)
                    atomic_json(case / "manifest.json", data)
                return {"source_id": existing["source_id"], "status": "existing", "path": str(destination), "sha256": digest, "validation": validation}
            directory = "raw/pdf" if args.kind == "pdf" else "raw/tables"
            filename = f"{safe_component(args.period)}_{safe_component(args.title)}_{digest[:12]}.{args.kind}"
            destination = case / directory / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and sha256(destination) != digest:
                raise ValueError(f"Refusing to overwrite a different original: {destination}")
            if not destination.exists():
                shutil.copyfile(staged, destination)
            entry = {
                "source_id": source_id, "company": data["company"], "code": data.get("code"),
                "title": args.title, "period": args.period, "report_type": args.report_type,
                "kind": args.kind, "path": str(destination.relative_to(case)),
                "sha256": digest, "bytes": destination.stat().st_size, "archived_at": now(),
                "validation": validation, "captures": [capture],
                "identity_status": "metadata-awaits-cover-and-period-verification" if args.kind == "pdf" else "metadata-awaits-company-period-scope-verification",
            }
            data["sources"].append(entry)
            atomic_json(case / "manifest.json", data)
            return {"source_id": source_id, "status": "archived", "path": str(destination), "sha256": digest, "validation": validation}


def select_pdfs(data, source_id):
    sources = [s for s in data["sources"] if s["kind"] == "pdf" and (not source_id or s["source_id"] == source_id)]
    if not sources:
        raise ValueError("No matching archived PDF")
    return sources


def reading_template(source, pages, index):
    return {
        "schema_version": VERSION, "source_id": source["source_id"], "source_sha256": source["sha256"],
        "reading_schema_version": READING_SCHEMA_VERSION, "required_topics": required_topics(source),
        "company": source["company"], "period": source["period"], "report_type": source["report_type"],
        "page_numbering": "physical PDF pages, starting at 1; printed page numbers may differ",
        "identity_check": {"status": "unread", "pages": [], "finding": "", "checked_company": "", "checked_legal_name": "", "checked_code": "", "checked_period": ""},
        "reader": "", "reviewed_at": "", "sections": {
            key: {"label": label, "status": "unread", "pages": [], "finding": "", "year_over_year_change": "", "financial_impact": "", "evidence_gaps": [], "candidate_pages": index["topics"][key]["pages"]}
            for key, (label, _) in TOPICS.items() if key in required_topics(source)
        },
        "whole_report_review": {"status": "unread", "reviewed_page_ranges": [], "unreviewed_page_ranges": [[1, pages]], "finding": ""},
        "material_findings": [],
        "limitations": ["Text extraction and keyword indexing do not establish that a person or AI read this report.", "A topic can be absent in a short quarterly report; record that disclosure limitation rather than treating it as no risk."],
    }


def reading_contract(source):
    """Expose accepted structure from code rather than making the reader guess it."""
    total = source["validation"]["pages"]
    return {
        "reading_schema_version": READING_SCHEMA_VERSION,
        "required_topics": required_topics(source),
        "section_status_enum": ["unread", "read", "not_disclosed", "evidence_gap"],
        "identity_check": {"complete_status": "read", "required_after_actual_verification": ["pages", "finding", "checked_company", "checked_code", "checked_period"], "expected_code": str(source.get("code", "")), "expected_period": source["period"]},
        "pages": {"type": "nonempty list of physical PDF page integers", "minimum": 1, "maximum": total},
        "section": {"required": ["status", "pages", "finding", "year_over_year_change", "financial_impact", "evidence_gaps"], "evidence_gap_status_requires": "at least one substantive missing-evidence explanation"},
        "whole_report_review": {"complete_status": "read", "required": ["reviewed_page_ranges", "unreviewed_page_ranges", "finding"], "ranges": "inclusive physical page [start,end] pairs; complete only after all pages actually reviewed"},
        "material_findings": {"type": "list; may be empty", "each_item_requires": ["pages", "finding", "financial_impact"]},
        "substantive_text": "At least eight characters with actual content; an unread or boilerplate claim does not establish reading.",
        "examples_are_observed_facts": False,
        "shape_examples_after_actual_review": {
            "identity_check": {"status": "read", "pages": [1], "finding": "Example only: the observed cover identifies the company and reporting period.", "checked_company": "Observed company name", "checked_code": "000000", "checked_period": "2025"},
            "section": {"status": "evidence_gap", "pages": [1], "finding": "Example only: a restriction is disclosed but its amount is absent.", "year_over_year_change": "The prior comparable amount cannot be established from this document.", "financial_impact": "Usable cash cannot be fully quantified from this disclosure.", "evidence_gaps": ["The restriction amount and legal-entity breakdown are not disclosed."]},
            "material_findings_item": {"pages": [1], "finding": "Example only: record the specific disclosed fact here.", "financial_impact": "Explain the effect on the financial or business conclusion."},
        },
        "notice": "Examples show structure only. The returned template remains unread; copy no example as a factual reading claim.",
    }


def command_reading_template(args):
    case = args.case.resolve()
    data = require_case(case)
    source = select_pdfs(data, args.source_id)[0]
    original = (case / source["path"]).resolve()
    if not original.is_relative_to(case) or not original.is_file() or sha256(original) != source["sha256"]:
        raise ValueError("Archived original is missing, outside case, or its SHA256 changed")
    index = {"topics": {key: {"pages": []} for key in TOPICS}}
    index_path = case / "workpapers/pdf" / source["source_id"] / "index.json"
    if index_path.is_file():
        candidate = read_json(index_path)
        if candidate.get("source_id") == source["source_id"]:
            for key in TOPICS:
                pages = candidate.get("topics", {}).get(key, {}).get("pages", [])
                if isinstance(pages, list) and all(type(p) is int and 1 <= p <= source["validation"]["pages"] for p in pages):
                    index["topics"][key]["pages"] = pages
    template = reading_template(source, source["validation"]["pages"], index)
    if args.out:
        if args.out.exists():
            raise ValueError("Reading-template output already exists; existing notes are preserved")
        atomic_json(args.out, template)
    return {"source_id": source["source_id"], "existing_note": str(case / "reading" / (source["source_id"] + ".json")), "template": template, "contract": reading_contract(source), "written_template": str(args.out) if args.out else None}


def command_extract(args):
    case = args.case.resolve()
    data = require_case(case)
    results = []
    for source in select_pdfs(data, args.source_id):
        original = case / source["path"]
        if sha256(original) != source["sha256"]:
            raise ValueError(f"Original hash changed: {original}")
        info = validate_file(original, "pdf", "utf-8-sig")
        text = poppler("pdftotext", "-layout", "-enc", "UTF-8", str(original), "-")
        chunks = text.split("\f")
        if chunks and not chunks[-1].strip():
            chunks.pop()
        if len(chunks) != info["pages"]:
            raise ValueError(f"Extracted page count {len(chunks)} differs from pdfinfo {info['pages']}; inspect this PDF before trusting page references")
        pages = [{"pdf_page": n, "text": chunk, "characters": len(chunk.strip()), "needs_visual_or_ocr": len(chunk.strip()) < 30} for n, chunk in enumerate(chunks, 1)]
        directory = case / "workpapers/pdf" / source["source_id"]
        directory.mkdir(parents=True, exist_ok=True)
        extracted = {"schema_version": VERSION, "source_id": source["source_id"], "source_sha256": source["sha256"], "extracted_at": now(), "method": "pdftotext -layout -enc UTF-8", "page_numbering": "physical PDF page, 1-based", "pages": pages}
        atomic_json(directory / "pages.json", extracted)
        (directory / "text.txt").write_text("\n\n".join(f"===== PDF PAGE {p['pdf_page']} =====\n{p['text']}" for p in pages), encoding="utf-8")
        indexed = {}
        for key, (label, keywords) in TOPICS.items():
            matches = []
            for page in pages:
                matching_lines = [line.strip() for line in page["text"].splitlines() if any(word in line for word in keywords)]
                if matching_lines:
                    matches.append({"pdf_page": page["pdf_page"], "snippets": matching_lines[:6]})
            indexed[key] = {"label": label, "keywords": list(keywords), "pages": [m["pdf_page"] for m in matches], "matches": matches}
        index = {"source_id": source["source_id"], "topics": indexed, "notice": "Keyword candidates only. Read the full relevant sections and the rest of the report; a missing hit is not evidence of absence."}
        atomic_json(directory / "index.json", index)
        reading_path = case / "reading" / (source["source_id"] + ".json")
        if not reading_path.exists():
            atomic_json(reading_path, reading_template(source, info["pages"], index))
        blank_pages = [p["pdf_page"] for p in pages if p["needs_visual_or_ocr"]]
        results.append({"source_id": source["source_id"], "pages": len(pages), "directory": str(directory), "reading_note": str(reading_path), "low_text_pages": blank_pages, "reading_status": "not-inferred-from-extraction"})
    return results


def valid_pages(values, total):
    return isinstance(values, list) and bool(values) and all(type(n) is int and 1 <= n <= total for n in values)


def substantive(value):
    return isinstance(value, str) and len(value.strip()) >= 8 and value.strip().lower() not in {"not read", "not applicable", "todo", "none", "无此披露", "已完整阅读", "没有重要发现"}


def ranges_to_pages(ranges, total):
    if not isinstance(ranges, list):
        raise ValueError("Expected a list of inclusive page ranges")
    pages = set()
    for pair in ranges:
        if not isinstance(pair, list) or len(pair) != 2 or not all(type(n) is int for n in pair) or not (1 <= pair[0] <= pair[1] <= total):
            raise ValueError(f"Invalid page range {pair!r}")
        pages.update(range(pair[0], pair[1] + 1))
    return pages


def check_reading(source, note):
    total = source["validation"]["pages"]
    errors, gaps = [], []
    if note.get("source_id") != source["source_id"] or note.get("source_sha256") != source["sha256"]:
        errors.append("Reading note does not match original source_id/SHA256")
    if not str(note.get("reader", "")).strip() or not str(note.get("reviewed_at", "")).strip():
        errors.append("Missing reader or reviewed_at")
    identity = note.get("identity_check", {})
    if identity.get("status") != "read" or not valid_pages(identity.get("pages"), total) or not substantive(identity.get("finding")):
        errors.append("Cover/company/period verification incomplete")
    declared_schema = note.get("reading_schema_version")
    if "reading_schema_version" in note and (type(declared_schema) is not int or declared_schema != READING_SCHEMA_VERSION):
        errors.append("Unsupported reading_schema_version; only absent legacy or explicit version 2 is accepted")
    modern = type(declared_schema) is int and declared_schema == READING_SCHEMA_VERSION
    if modern:
        expected = str(source.get("code", ""))
        if not expected or str(identity.get("checked_code", "")) != expected or not str(identity.get("checked_company", "")).strip():
            errors.append("Verified company code/name does not match case metadata")
        if note.get("required_topics") != required_topics(source):
            errors.append("Required topics do not match archived report-type profile")
        keys = list(dict.fromkeys(required_topics(source) + list(note.get("sections", {}))))
        if any(key not in TOPICS for key in keys):
            errors.append("Unknown reading topic")
    else:
        # Historical notes remain compatible and retain the original thirteen-topic bar.
        if identity.get("checked_company") != source["company"]:
            errors.append("Verified company does not match case metadata")
        keys = list(TOPICS)
    if identity.get("checked_period") != source["period"]:
        errors.append("Verified period does not match case metadata")
    for key in keys:
        section = note.get("sections", {}).get(key, {})
        status = section.get("status")
        if status not in STATUSES or status == "unread":
            errors.append(f"{key}: unread or invalid status")
            continue
        if not valid_pages(section.get("pages"), total):
            errors.append(f"{key}: physical PDF page references required, including the range checked for absent disclosure")
        if not substantive(section.get("finding")):
            errors.append(f"{key}: substantive finding/explanation required")
        evidence_gaps = section.get("evidence_gaps")
        if status == "evidence_gap":
            if not isinstance(evidence_gaps, list) or not any(substantive(x) for x in evidence_gaps):
                errors.append(f"{key}: state the missing evidence and its effect")
        # A section can be fully read while still lacking external evidence.
        if isinstance(evidence_gaps, list) and any(substantive(x) for x in evidence_gaps):
            gaps.append({"topic": key, "evidence_gaps": evidence_gaps})
    whole = note.get("whole_report_review", {})
    if whole.get("status") != "read" or not substantive(whole.get("finding")):
        errors.append("Whole-report review is incomplete")
    try:
        reviewed = ranges_to_pages(whole.get("reviewed_page_ranges"), total)
        unreviewed = ranges_to_pages(whole.get("unreviewed_page_ranges"), total)
        if reviewed & unreviewed:
            errors.append("Reviewed and unreviewed page ranges overlap")
        if reviewed != set(range(1, total + 1)) or unreviewed:
            errors.append("Full PDF reading is incomplete: not all pages reviewed")
    except ValueError as exc:
        errors.append(str(exc))
    findings = note.get("material_findings", [])
    if not isinstance(findings, list):
        errors.append("material_findings must be a list")
    else:
        for i, finding in enumerate(findings, 1):
            if not isinstance(finding, dict) or not valid_pages(finding.get("pages"), total) or not substantive(finding.get("finding")) or not substantive(finding.get("financial_impact")):
                errors.append(f"material_findings[{i}]: pages, finding and financial_impact required")
    profile = "report_type_v2" if modern else "unsupported" if "reading_schema_version" in note else "legacy_13_topics"
    return {"source_id": source["source_id"], "period": source["period"], "report_type": source["report_type"], "pages": total, "record_complete": not errors, "record_profile": profile, "errors": errors, "evidence_gaps": gaps}


def command_coverage(args):
    case = args.case.resolve()
    data = require_case(case)
    results = []
    for source in select_pdfs(data, args.source_id):
        path = case / "reading" / (source["source_id"] + ".json")
        original = case / source["path"]
        if not original.is_file() or sha256(original) != source["sha256"]:
            results.append({"source_id": source["source_id"], "period": source["period"], "record_complete": False, "errors": ["Archived original is missing or its SHA256 changed"], "evidence_gaps": []})
            continue
        if not path.is_file():
            results.append({"source_id": source["source_id"], "period": source["period"], "record_complete": False, "errors": ["Missing reading note; run extract first"], "evidence_gaps": []})
        else:
            try:
                results.append(check_reading(source, read_json(path)))
            except (ValueError, TypeError, KeyError) as exc:
                results.append({"source_id": source["source_id"], "period": source["period"], "record_complete": False, "errors": [f"Malformed reading note: {exc}"], "evidence_gaps": []})
    report = {
        "schema_version": VERSION, "checked_at": now(), "company": data["company"],
        "record_complete": bool(results) and all(r["record_complete"] for r in results),
        "notice": "This checks the reading record, not whether an AI/person actually read or understood the PDF. Open evidence gaps still limit conclusions; numeric validation and material-issue investigation are separate completion requirements.",
        "reports": results,
    }
    # Receipt tracking is separate from the human/AI-authored semantic reading record.
    # Legacy completed notes do not retroactively acquire delivery receipts.
    import pdf_reader
    delivery = {item["source_id"]: item for item in pdf_reader.status_report(case, args.source_id)["sources"]}
    for result in results:
        receipt = delivery.get(result["source_id"], {})
        result["text_delivery"] = receipt
        result["delivery_basis"] = "tracked_receipts" if receipt.get("tracking") == "receipts" else "legacy_or_untracked_no_receipt_claim"
    if args.out:
        atomic_json(args.out, report)
    return report


def parser():
    root = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = root.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create/resume an isolated company research case")
    init.add_argument("--case", type=Path, required=True)
    init.add_argument("--company", required=True)
    init.add_argument("--code", required=True)
    init.add_argument("--start", required=True, help="Requested analysis start, e.g. 2020-01-01")
    init.add_argument("--end", required=True, help="Analysis as-of date, e.g. 2026-11-05")
    init.set_defaults(func=command_init)
    ingest = sub.add_parser("ingest", help="Validate/archive a browser export or verified public report URL")
    ingest.add_argument("--case", type=Path, required=True)
    source = ingest.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="Browser-downloaded local original; never edited in place")
    source.add_argument("--url", help="An already verified public document URL, not a logged-in webpage")
    ingest.add_argument("--verified-public", action="store_true", help="Assert --url was verified as the requested public original report")
    ingest.add_argument("--source-url", help="Document or exporting webpage URL; required with --file")
    ingest.add_argument("--title", required=True)
    ingest.add_argument("--period", required=True, help="Report period, e.g. 2025 / 2026H1 / 2026Q3 or 2020-2026 for bulk exports")
    ingest.add_argument("--report-type", required=True, choices=("annual", "half-year", "q1", "q3", "statement-export", "other"))
    ingest.add_argument("--kind", required=True, choices=("pdf", "csv", "xlsx"))
    ingest.add_argument("--settings", type=Path, help="JSON object: statement, scope, granularity, range, units, restatement settings etc.")
    ingest.add_argument("--encoding", default="utf-8-sig", help="CSV encoding; default utf-8-sig, override explicitly for other exports")
    ingest.add_argument("--max-bytes", type=int, default=512 * 1024 * 1024)
    ingest.set_defaults(func=command_ingest)
    extract = sub.add_parser("extract", help="Extract physical-page text/index and create unread PDF reading templates")
    extract.add_argument("--case", type=Path, required=True)
    extract.add_argument("--source-id", help="One archived PDF ID; omitted means all PDFs")
    extract.set_defaults(func=command_extract)
    template = sub.add_parser("reading-template", help="Show a source-bound unread template, accepted field contract and illustrative shapes")
    template.add_argument("--case", type=Path, required=True)
    template.add_argument("--source-id", required=True)
    template.add_argument("--out", type=Path, help="Write only the unread template to a new file; existing notes are never overwritten")
    template.set_defaults(func=command_reading_template)
    coverage = sub.add_parser("coverage", help="Check substantive per-period reading records (does not infer actual reading)")
    coverage.add_argument("--case", type=Path, required=True)
    coverage.add_argument("--source-id")
    coverage.add_argument("--out", type=Path)
    coverage.add_argument("--summary", action="store_true", help="Print compact coverage counts; --out still retains the full record")
    coverage.set_defaults(func=command_coverage)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = args.func(args)
        displayed = result
        if args.command == "coverage" and args.summary:
            displayed = {"company": result["company"], "record_complete": result["record_complete"], "reports": len(result["reports"]), "record_complete_reports": sum(r["record_complete"] for r in result["reports"]), "errors": sum(len(r["errors"]) for r in result["reports"]), "topics_with_evidence_gaps": sum(len(r["evidence_gaps"]) for r in result["reports"]), "receipt_complete_reports": sum(r.get("text_delivery", {}).get("delivery_complete", False) for r in result["reports"]), "full_record": str(args.out) if args.out else None, "notice": result["notice"]}
        print(json.dumps(displayed, ensure_ascii=False, indent=2))
        return 2 if args.command == "coverage" and not result["record_complete"] else 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
