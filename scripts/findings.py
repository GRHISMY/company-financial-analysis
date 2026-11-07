#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One evidence-bound research finding ledger; render current statements by stable ID."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

import source_archive as archive
import case_state as cases

LEDGER = "analysis/findings.json"
SCHEMA = "cfa-findings/v1"
ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
TOKEN = re.compile(r"\{\{finding:([A-Za-z][A-Za-z0-9_.-]{0,79})\}\}")


def pointer_value(document, pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("Calculation pointer must be an absolute JSON pointer")
    value = document
    try:
        for part in pointer.split("/")[1:]:
            part = part.replace("~1", "/").replace("~0", "~")
            value = value[int(part)] if isinstance(value, list) else value[part]
    except (KeyError, IndexError, ValueError, TypeError):
        raise ValueError(f"Calculation pointer not found: {pointer}")
    return value


def digest(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_finding(case, item):
    if not isinstance(item, dict) or not ID.fullmatch(item.get("id", "")):
        raise ValueError("Finding needs a stable id")
    if item.get("claim_type") not in {"fact", "calculation", "inference"}:
        raise ValueError("claim_type must be fact, calculation or inference")
    if item.get("status") not in {"open", "supported", "limited", "resolved"}:
        raise ValueError("status must be open, supported, limited or resolved")
    if item.get("materiality") not in {"material", "watch", "context"}:
        raise ValueError("materiality must be material, watch or context")
    for field in ("title", "statement", "financial_impact"):
        if not isinstance(item.get(field), str) or not item[field].strip():
            raise ValueError(f"Finding requires {field}")
    for field in ("alternatives", "gaps"):
        if not isinstance(item.get(field, []), list) or not all(isinstance(x, str) and x.strip() for x in item.get(field, [])):
            raise ValueError(f"{field} must be a list of explanations")
    if item["status"] == "limited" and not item.get("gaps"):
        raise ValueError("Limited conclusion requires explicit evidence gaps")
    if not isinstance(item.get("sources", []), list) or not isinstance(item.get("calculations", []), list):
        raise ValueError("sources/calculations must be lists")
    if not item.get("sources") and not item.get("calculations"):
        raise ValueError("Finding requires a PDF source or registered calculation")
    normalized = {k: item[k] for k in ("id", "title", "claim_type", "status", "materiality", "statement", "financial_impact")}
    normalized.update({k: list(item.get(k, [])) for k in ("alternatives", "gaps")})
    normalized["sources"] = [cases.source_ref(case, ref) for ref in item.get("sources", [])]
    normalized["calculations"] = []
    states = cases.artifact_states(case)
    for ref in item.get("calculations", []):
        key = ref.get("artifact")
        if states.get(key, {}).get("status") != "fresh":
            raise ValueError(f"Calculation artifact missing/stale: {key}")
        sha = states[key]["sha256"]
        if ref.get("sha256") not in (None, sha):
            raise ValueError("Finding calculation is pinned to an older artifact version")
        value = pointer_value(archive.read_json(Path(states[key]["path"])), ref.get("pointer"))
        normalized["calculations"].append({"artifact": key, "sha256": sha, "pointer": ref["pointer"], "value": value})
    return normalized


def load_ledger(case):
    path = case / LEDGER
    if not path.exists():
        return {"schema_version": SCHEMA, "findings": []}
    data = archive.read_json(path)
    if data.get("schema_version") != SCHEMA or not isinstance(data.get("findings"), list):
        raise ValueError("Unsupported findings ledger")
    ids = [item.get("id") for item in data["findings"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate finding ids")
    return data


def put(case, item):
    case = cases.case_path(case)
    with archive.manifest_lock(case):
        items = item.get("findings", [item]) if isinstance(item, dict) else item
        if not isinstance(items, list) or not items:
            raise ValueError("Put expects one finding or a nonempty findings list")
        normalized_items = [validate_finding(case, value) for value in items]
        if len({x["id"] for x in normalized_items}) != len(normalized_items):
            raise ValueError("Duplicate finding IDs in batch")
        ledger = load_ledger(case)
        records = {x["id"]:x for x in ledger["findings"]}
        updates = []
        for normalized in normalized_items:
            old = records.get(normalized["id"])
            canonical = {k:v for k,v in old.items() if k not in {"revision", "updated_at", "history"}} if old else None
            if canonical == normalized:
                updates.append({"id": normalized["id"], "status": "unchanged", "revision": old["revision"]})
                continue
            history = list(old.get("history", [])) if old else []
            if old:
                history.append({k:v for k,v in old.items() if k != "history"})
            normalized.update(revision=(old.get("revision", 0) if old else 0) + 1,
                              updated_at=archive.now(), history=history)
            records[normalized["id"]] = normalized
            updates.append({"id": normalized["id"], "status": "updated" if old else "created", "revision": normalized["revision"]})
        # Keep old calculation pins stale until all affected findings are explicitly reviewed.
        for finding in records.values():
            validate_finding(case, finding)
        if all(x["status"] == "unchanged" for x in updates):
            return updates[0] if len(updates) == 1 else {"updates": updates}
        ledger["findings"] = list(records.values())
        ledger["updated_at"] = archive.now()
        archive.atomic_json(case / LEDGER, ledger)
        source_ids = {r["source_id"] for f in ledger["findings"] for r in f.get("sources", [])}
        depends = {r["artifact"] for f in ledger["findings"] for r in f.get("calculations", [])}
        cases.register_artifact(case, "findings", case / LEDGER, depends=sorted(depends), source_ids=sorted(source_ids), _locked=True,
                                note="Authoritative current findings; historical revisions remain audit records")
    return updates[0] if len(updates) == 1 else {"updates": updates}


def validate_ledger(case):
    case = cases.case_path(case)
    ledger = load_ledger(case)
    errors, counts = [], {}
    for finding in ledger["findings"]:
        try:
            normalized = validate_finding(case, finding)
            counts[normalized["status"]] = counts.get(normalized["status"], 0) + 1
        except (ValueError, OSError, TypeError, KeyError) as exc:
            errors.append({"id": finding.get("id"), "error": str(exc)})
    return {"schema_version": SCHEMA, "exists": (case / LEDGER).is_file(), "valid": (case / LEDGER).is_file() and not errors,
            "findings": len(ledger["findings"]), "status_counts": counts, "errors": errors,
            "notice": "Structural/provenance validation does not establish the truth of an inference or resolve open gaps."}


def render_finding(case, finding):
    current = validate_finding(case, finding)
    sources = {s["source_id"]: s for s in archive.require_case(case)["sources"]}
    text = f"### {current['id']} · {current['title']}\n\n类型：{current['claim_type']}；状态：{current['status']}；重要性：{current['materiality']}。\n\n{current['statement']}\n\n影响：{current['financial_impact']}\n"
    for ref in current["sources"]:
        source = sources[ref["source_id"]]
        title = source['title'].replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
        target = str(case / source['path']).replace("<", "%3C").replace(">", "%3E")
        text += f"\n来源：[{title}](<{target}>)，PDF物理页 {', '.join(str(x) for x in ref['pages'])}。\n"
    for ref in current["calculations"]:
        value = json.dumps(ref["value"], ensure_ascii=False)
        text += f"\n计算：{ref['artifact']} {ref['pointer']} = {value}。\n"
    if current["alternatives"]:
        text += "\n替代解释：" + "；".join(x.rstrip("。；; ") for x in current["alternatives"]) + "。\n"
    if current["gaps"]:
        text += "\n证据缺口：" + "；".join(x.rstrip("。；; ") for x in current["gaps"]) + "。\n"
    text += f"\n<!-- finding:{finding['id']} revision:{finding.get('revision', 1)} sha256:{digest(current)} -->\n"
    return text


def _render_locked(case, output, template=None, ids=None):
    case = cases.case_path(case)
    key = "findings_markdown" if not template else "report." + hashlib.sha256(str(cases.inside(case, output).relative_to(case)).encode()).hexdigest()[:12]
    output = cases.output_path(case, output, key, [template] if template else [])
    ledger_sha = archive.sha256(case / LEDGER) if (case / LEDGER).exists() else None
    state_sha = archive.sha256(case / cases.STATE_FILE) if (case / cases.STATE_FILE).exists() else None
    producer_sha = archive.sha256(Path(__file__))
    inventory_sha = cases.inventory_digest(case)
    ledger = load_ledger(case)
    records = {x["id"]: x for x in ledger["findings"]}
    if template:
        template = cases.inside(case, template)
        if output == template:
            raise ValueError("Preserve the template separately from generated output")
        template_sha = archive.sha256(template)
        text = template.read_text(encoding="utf-8")
        used = set(TOKEN.findall(text))
        missing = used - set(records)
        if missing:
            raise ValueError("Unknown finding ids: " + ", ".join(sorted(missing)))
        if not used:
            raise ValueError("Template contains no {{finding:ID}} references")
        text = TOKEN.sub(lambda match: render_finding(case, records[match.group(1)]), text)
    else:
        used = set(ids or records)
        if used - set(records):
            raise ValueError("Unknown finding id")
        text = "# 当前研究发现\n\n" + "\n".join(render_finding(case, records[k]) for k in records if k in used)
    if not (case / LEDGER).exists():
        raise ValueError("No authoritative findings ledger; put a finding first")
    # An edit to any current finding makes registered reports stale until rerendered.
    if cases.artifact_states(case).get("findings", {}).get("status") != "fresh":
        raise ValueError("Findings ledger/dependencies changed; review and put current evidence before rendering")
    if archive.sha256(case / LEDGER) != ledger_sha or archive.sha256(case / cases.STATE_FILE) != state_sha or archive.sha256(Path(__file__)) != producer_sha or cases.inventory_digest(case) != inventory_sha or (template and archive.sha256(template) != template_sha):
        raise ValueError("Finding/template inputs or rules changed while rendering; no output written")
    cases.atomic_text(output, text)
    cases.register_artifact(case, key, output, depends=["findings"], inputs=[template] if template else [], producer=__file__, _locked=True)
    return {"path": str(output), "finding_ids": sorted(used), "artifact": key}


def render(case, output, template=None, ids=None):
    case = cases.case_path(case)
    with archive.manifest_lock(case):
        return _render_locked(case, output, template, ids)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    temp = sub.add_parser("template", help="Print the minimal finding input shape")
    put_parser = sub.add_parser("put", help="Validate/upsert one finding and keep earlier revisions")
    put_parser.add_argument("--case", type=Path, required=True)
    put_parser.add_argument("--file", type=Path, required=True)
    for name in ("list", "validate"):
        p = sub.add_parser(name)
        p.add_argument("--case", type=Path, required=True)
    p = sub.add_parser("render", help="Generate evidence passages/current report from stable finding IDs")
    p.add_argument("--case", type=Path, required=True)
    p.add_argument("--output", type=Path, default=Path("analysis/findings.md"))
    p.add_argument("--template", type=Path)
    p.add_argument("--id", action="append")
    args = parser.parse_args(argv)
    try:
        if args.command == "template":
            result = {"id": "F001", "title": "", "claim_type": "inference", "status": "limited", "materiality": "material",
                      "statement": "", "financial_impact": "", "sources": [{"source_id": "", "pages": []}],
                      "calculations": [], "alternatives": [], "gaps": []}
        elif args.command == "put":
            result = put(args.case, archive.read_json(args.file))
        elif args.command == "list":
            result = load_ledger(cases.case_path(args.case))
        elif args.command == "validate":
            result = validate_ledger(args.case)
        else:
            result = render(args.case, args.output, args.template, args.id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if args.command == "validate" and not result["valid"] else 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
