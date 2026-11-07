#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One official artifact index, dependency freshness and repeatable numeric builds."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sys
import os
import tempfile

import source_archive as archive

SCHEMA = "cfa-case/v1"
STATE_FILE = "case-state.json"
KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
CANDIDATES = {
    "adopted_facts": "workpapers/numbers/adopted-facts.json",
    "csv_facts": "workpapers/numbers/csv-facts.json",
    "pdf_facts": "workpapers/numbers/pdf-facts.json",
    "input_checks": "workpapers/numbers/input-checks.json",
    "calculations": "workpapers/numbers/calculations.json",
    "findings": "analysis/findings.json",
    "returns": "analysis/shareholder-returns.json",
}


def case_path(case):
    case = Path(case).resolve()
    archive.require_case(case)
    return case


def inside(case, path):
    case = Path(case).resolve()
    path = Path(path)
    path = (case / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        path.relative_to(case)
    except ValueError:
        raise ValueError("Artifact paths must remain inside the case directory")
    return path


def output_path(case, path, key=None, inputs=()):
    """Check destinations before a write, including unregistered reserved inputs."""
    case = case_path(case)
    path = inside(case, path)
    reserved = {case / STATE_FILE, case / "manifest.json", case / ".archive.lock",
                case / "analysis/findings.json", case / "analysis/shareholder-returns.json"}
    # Legacy cases may have important inputs before any official registration.
    # Only the matching deterministic build may occupy a canonical output slot.
    reserved.update(case / value for name, value in CANDIDATES.items()
                    if name != key or name in {"adopted_facts", "csv_facts", "pdf_facts"})
    if key != "calculations_markdown":
        reserved.add(case / "workpapers/numbers/calculations.md")
    reserved.update(inside(case, p) for p in inputs)
    if path in reserved or any(path.is_relative_to(case / folder) for folder in ("raw", "reading", "workpapers/pdf")):
        raise ValueError("Derived output would overwrite a protected index, original, ledger or research input")
    state = load_state(case)
    for other, entry in (state["artifacts"] if state else {}).items():
        if other != key and inside(case, entry["path"]) == path:
            raise ValueError(f"Output collides with registered artifact: {other}")
    return path


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_state(case):
    path = case / STATE_FILE
    if not path.exists():
        return None
    value = archive.read_json(path)
    manifest = archive.require_case(case)
    if value.get("schema_version") != SCHEMA:
        raise ValueError("Unsupported case-state schema")
    if (value.get("company"), value.get("code")) != (manifest["company"], manifest.get("code")):
        raise ValueError("Case-state company/code differs from source manifest")
    if not isinstance(value.get("artifacts"), dict):
        raise ValueError("case-state artifacts must be an object")
    return value


def new_state(case):
    m = archive.require_case(case)
    return {"schema_version": SCHEMA, "company": m["company"], "code": m.get("code"),
            "created_at": archive.now(), "artifacts": {}}


def inventory_digest(case):
    manifest = archive.require_case(case)
    value = {"company": manifest["company"], "code": manifest.get("code"), "requested_period": manifest.get("requested_period"),
             "sources": sorted(({k:s.get(k) for k in ("source_id", "sha256", "period", "report_type", "kind")} for s in manifest["sources"]), key=lambda x:x["source_id"])}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def source_ref(case, ref):
    """Bind one citation to a real immutable PDF and physical page range."""
    if not isinstance(ref, dict):
        raise ValueError("Source reference must be an object")
    manifest = archive.require_case(case)
    source = next((s for s in manifest["sources"] if s["source_id"] == ref.get("source_id")), None)
    if not source or source.get("kind") != "pdf":
        raise ValueError("Source reference must name an archived PDF")
    if str(source.get("code", manifest.get("code"))) != str(manifest.get("code")):
        raise ValueError("Source belongs to another company code")
    if ref.get("sha256") not in (None, source["sha256"]):
        raise ValueError("Source citation hash differs from archived version")
    path = inside(case, source["path"])
    if not path.is_file() or archive.sha256(path) != source["sha256"]:
        raise ValueError("Source original is missing or changed")
    pages = ref.get("pages")
    if not archive.valid_pages(pages, source["validation"]["pages"]):
        raise ValueError("Source citation requires valid 1-based physical PDF pages")
    return {**ref, "source_id": source["source_id"], "sha256": source["sha256"],
            "pages": sorted(set(pages))}


def file_dependency(case, path):
    path = inside(case, path)
    if not path.is_file():
        raise ValueError(f"Dependency missing: {path}")
    return {"kind": "file", "path": str(path.relative_to(case)), "sha256": archive.sha256(path)}


def artifact_states(case, state=None):
    state = state if state is not None else load_state(case)
    entries = state["artifacts"] if state else {}
    sources = {s["source_id"]: s for s in archive.require_case(case)["sources"]}
    result = {}

    def inspect(key, stack=()):
        if key in result:
            return result[key]
        if key in stack:
            return {"status": "invalid", "reasons": ["Dependency cycle"]}
        entry = entries.get(key)
        if not entry:
            return {"status": "missing", "reasons": ["Official artifact not registered"]}
        problems = []
        path = inside(case, entry["path"])
        if not path.is_file():
            result[key] = {"status": "missing", "path": str(path), "reasons": ["Artifact file missing"]}
            return result[key]
        current = archive.sha256(path)
        if current != entry.get("sha256"):
            problems.append("Artifact content changed since registration")
        for dep in entry.get("dependencies", []):
            kind = dep.get("kind")
            if kind == "artifact":
                upstream = entries.get(dep.get("key"))
                status = inspect(dep.get("key"), stack + (key,))
                if not upstream or upstream.get("sha256") != dep.get("sha256") or status["status"] != "fresh":
                    problems.append(f"Upstream artifact changed/missing/stale: {dep.get('key')}")
            elif kind in {"file", "tool"}:
                target = inside(case, dep["path"]) if kind == "file" else Path(dep["path"])
                if not target.is_file() or archive.sha256(target) != dep.get("sha256"):
                    problems.append(f"Dependency changed/missing: {dep['path']}")
            elif kind == "source":
                source = sources.get(dep.get("source_id"))
                target = inside(case, source["path"]) if source else None
                if not source or source["sha256"] != dep.get("sha256") or not target.is_file() or archive.sha256(target) != dep.get("sha256"):
                    problems.append(f"Original source changed/missing: {dep.get('source_id')}")
            elif kind == "inventory":
                if inventory_digest(case) != dep.get("sha256"):
                    problems.append("Analysis scope/source inventory changed; review report coverage")
            else:
                problems.append("Invalid dependency kind")
        result[key] = {"status": "stale" if problems else "fresh", "path": str(path),
                       "sha256": current, "reasons": problems, "registered_at": entry.get("registered_at")}
        return result[key]

    for key in entries:
        inspect(key)
    return result


def register_artifact(case, key, path, depends=(), inputs=(), source_ids=(), producer=None, note="", _locked=False):
    case = case_path(case)
    if not KEY.fullmatch(key):
        raise ValueError("Artifact key must be a short lowercase identifier")
    path = inside(case, path)
    if not path.is_file() or path.name in {STATE_FILE, "manifest.json"}:
        raise ValueError("Register an existing output/input file, not the index or manifest itself")
    if path.parts[len(case.parts):len(case.parts) + 1] == ("raw",):
        raise ValueError("Raw originals belong in manifest, not the artifact registry")
    with (contextlib.nullcontext() if _locked else archive.manifest_lock(case)):
        state = load_state(case) or new_state(case)
        if any(other != key and inside(case, item["path"]) == path for other,item in state["artifacts"].items()):
            raise ValueError("One physical file cannot have two official artifact keys")
        statuses = artifact_states(case, state)
        if key in {"adopted_facts", "csv_facts", "pdf_facts"}:
            facts = archive.read_json(path)
            if facts.get("schema_version") != "financial-facts/v1" or str(facts.get("company", {}).get("code")) != str(state["code"]):
                raise ValueError("Official facts require financial-facts/v1 and the case company code as text")
            source_ids = sorted(set(source_ids) | set(facts_source_ids(case, facts)))
        deps = []
        for upstream in depends:
            if upstream == key:
                raise ValueError("An artifact cannot depend on itself")
            if statuses.get(upstream, {}).get("status") != "fresh":
                raise ValueError(f"Register current upstream artifact first: {upstream}")
            deps.append({"kind": "artifact", "key": upstream, "sha256": state["artifacts"][upstream]["sha256"]})
        deps.extend(file_dependency(case, p) for p in inputs)
        sources = {s["source_id"]: s for s in archive.require_case(case)["sources"]}
        for sid in source_ids:
            source = sources.get(sid)
            if not source or not inside(case, source["path"]).is_file() or archive.sha256(inside(case, source["path"])) != source["sha256"]:
                raise ValueError(f"Original source missing/changed: {sid}")
            deps.append({"kind": "source", "source_id": sid, "sha256": source["sha256"]})
        if producer:
            producer = Path(producer).resolve()
            if not producer.is_file():
                raise ValueError("Producer rule/script does not exist")
            deps.append({"kind": "tool", "path": str(producer), "sha256": archive.sha256(producer)})
        if key == "report" or key.startswith("report."):
            deps.append({"kind": "inventory", "sha256": inventory_digest(case)})
        entry = {"path": str(path.relative_to(case)), "sha256": archive.sha256(path),
                 "registered_at": archive.now(), "dependencies": deps, "note": note}
        state["artifacts"][key] = entry
        def check_cycle(current, trail=()):
            if current in trail:
                raise ValueError("Artifact dependency cycle")
            for dep in state["artifacts"].get(current, {}).get("dependencies", []):
                if dep.get("kind") == "artifact":
                    check_cycle(dep["key"], trail + (current,))
        check_cycle(key)
        state["updated_at"] = archive.now()
        archive.atomic_json(case / STATE_FILE, state)
    return entry


def facts_source_ids(case, data):
    """Get actual archived dependencies from evidence, never guess a numeric value."""
    sources = archive.require_case(case)["sources"]
    by_hash = {s["sha256"]: s["source_id"] for s in sources}
    by_path = {str(inside(case, s["path"])): s["source_id"] for s in sources}
    by_id = {s["source_id"]: s for s in sources}
    found = set()

    def visit(item):
        if isinstance(item, dict):
            source = item.get("source")
            if isinstance(source, dict):
                if source.get("sha256") in by_hash:
                    found.add(by_hash[source["sha256"]])
                for field in ("pdf", "file"):
                    if source.get(field):
                        p = Path(source[field])
                        p = (case / p).resolve() if not p.is_absolute() else p.resolve()
                        if str(p) in by_path:
                            sid = by_path[str(p)]
                            if source.get("sha256") not in (None, by_id[sid]["sha256"]):
                                raise ValueError("Fact citation SHA256 contradicts its archived path")
                            found.add(sid)
                        elif field == "pdf" and source.get("sha256") not in by_hash:
                            raise ValueError(f"PDF fact refers to an unarchived original: {p}")
            for value in item.values():
                visit(value)
        elif isinstance(item, list):
            for value in item:
                visit(value)
    visit(data)
    return sorted(found)


def status_report(case, verify_sources=False):
    case = case_path(case)
    manifest = archive.require_case(case)
    state = load_state(case)
    states = artifact_states(case, state)
    source_counts, invalid = {}, []
    reading = []
    for source in manifest["sources"]:
        source_counts[source["kind"]] = source_counts.get(source["kind"], 0) + 1
        if verify_sources and (not inside(case, source["path"]).is_file() or archive.sha256(inside(case, source["path"])) != source["sha256"]):
            invalid.append(source["source_id"])
        if source["kind"] == "pdf":
            path = case / "reading" / (source["source_id"] + ".json")
            try:
                check = archive.check_reading(source, archive.read_json(path)) if path.exists() else {"record_complete": False, "errors": ["Missing reading note"], "evidence_gaps": []}
            except (ValueError, KeyError, TypeError) as exc:
                check = {"record_complete": False, "errors": [str(exc)], "evidence_gaps": []}
            reading.append({"source_id": source["source_id"], "period": source["period"], **check})
    pending = [x["source_id"] for x in reading if not x["record_complete"]]
    next_actions = []
    if state is None:
        next_actions.append({"command": "register", "reason": "Legacy/unregistered case: verify selected files and explicitly register them; filenames alone do not make outputs official"})
    for key, item in states.items():
        if item["status"] != "fresh":
            next_actions.append({"command": "rebuild_or_review", "artifact": key, "reason": item["reasons"]})
    numeric_intent = any(k in states or (case / CANDIDATES[k]).is_file() for k in ("adopted_facts", "csv_facts", "pdf_facts", "calculations"))
    if "adopted_facts" not in states and numeric_intent:
        next_actions.append({"command": "register", "artifact": "adopted_facts", "reason": "Choose evidence-backed adopted values before calculation"})
    elif states.get("adopted_facts", {}).get("status") == "fresh" and "calculations" not in states:
        next_actions.append({"command": "build", "reason": "Adopted values registered; numerical output not built"})
    if pending:
        next_actions.append({"command": "pdf next", "source_ids": pending, "reason": "PDF notes/whole-report evidence incomplete; receipt progress is reported separately"})
    report = {"schema_version": SCHEMA, "case": str(case), "company": manifest["company"], "code": manifest.get("code"),
              "registry": "registered" if state else "unregistered", "source_counts": source_counts,
              "source_hashes": {"verified": verify_sources, "invalid": invalid}, "artifacts": states,
              "unregistered_candidates": {k: v for k, v in CANDIDATES.items() if k not in states and (case / v).is_file()},
              "reading_records": {"total": len(reading), "structurally_complete": sum(x["record_complete"] for x in reading), "reports": reading},
              "next_actions": next_actions,
              "interpretation": "Fresh files, numeric checks, reading receipts and sufficient research evidence are separate. This command does not certify the financial conclusion."}
    try:
        import pdf_reader
        report["reading_receipts"] = pdf_reader.status_report(case)
    except ImportError:
        report["reading_receipts"] = {"mode": "reader_not_installed"}
    except (ValueError, OSError) as exc:
        report["reading_receipts"] = {"error": str(exc)}
    checks = states.get("calculations")
    if checks and checks["status"] == "fresh":
        report["numeric_checks"] = archive.read_json(Path(checks["path"])).get("check_counts", {})
    if (case / "analysis/findings.json").is_file():
        import findings
        report["findings"] = findings.validate_ledger(case)
        if not report["findings"]["valid"]:
            next_actions.append({"command": "finding put", "reason": "Finding evidence/calculation bindings need review"})
    if (case / "analysis/shareholder-returns.json").is_file():
        try:
            import returns_ledger
            report["returns"] = returns_ledger.validate_ledger(case)
        except (ImportError, ValueError, OSError, TypeError, KeyError) as exc:
            report["returns"] = {"error": str(exc)}
    try:
        import evidence_index
        report["evidence_index"] = evidence_index.status_report(case=case, verify=True)
        if report["evidence_index"].get("status") in {"missing", "not_indexed", "stale"} and any(s.get("kind") == "pdf" for s in manifest["sources"]):
            next_actions.append({"command": "index sync", "reason": "Refresh the local query cache from current originals and research records"})
    except ImportError:
        report["evidence_index"] = {"status": "not_installed"}
    except (ValueError, OSError, TypeError, KeyError) as exc:
        report["evidence_index"] = {"status": "unavailable", "error": str(exc)}
    return report


def build_case(case):
    case = case_path(case)
    with archive.manifest_lock(case):
        state = load_state(case)
        states = artifact_states(case, state)
        if states.get("adopted_facts", {}).get("status") != "fresh":
            raise ValueError("Register fresh adopted_facts before build; stale sources require review, not blind recomputation")
        import financial_pipeline as pipeline
        state_sha = archive.sha256(case / STATE_FILE)
        producer_sha = archive.sha256(Path(pipeline.__file__))
        directory = case / "workpapers/numbers"
        result_path = output_path(case, directory / "calculations.json", "calculations")
        markdown = output_path(case, directory / "calculations.md", "calculations_markdown")
        both = all(states.get(k, {}).get("status") == "fresh" for k in ("csv_facts", "pdf_facts"))
        check_path = output_path(case, directory / "input-checks.json", "input_checks") if both else None
        data = archive.read_json(Path(states["adopted_facts"]["path"]))
        calculations = pipeline.calculate(data)
        text = pipeline.markdown(calculations)
        reconciled = pipeline.reconcile(archive.read_json(Path(states["csv_facts"]["path"])), archive.read_json(Path(states["pdf_facts"]["path"]))) if both else None
        current = artifact_states(case)
        inputs = ["adopted_facts"] + (["csv_facts", "pdf_facts"] if both else [])
        if archive.sha256(case / STATE_FILE) != state_sha or archive.sha256(Path(pipeline.__file__)) != producer_sha or any(current.get(k, {}).get("status") != "fresh" or current[k]["sha256"] != states[k]["sha256"] for k in inputs):
            raise ValueError("Inputs/rules changed during build; no outputs written")
        archive.atomic_json(result_path, calculations)
        register_artifact(case, "calculations", result_path, depends=["adopted_facts"], producer=pipeline.__file__, _locked=True)
        atomic_text(markdown, text)
        register_artifact(case, "calculations_markdown", markdown, depends=["calculations"], producer=pipeline.__file__, _locked=True)
        outputs = {"calculations": str(result_path), "check_counts": calculations["check_counts"]}
        if reconciled is not None:
            archive.atomic_json(check_path, reconciled)
            register_artifact(case, "input_checks", check_path, depends=["csv_facts", "pdf_facts"], producer=pipeline.__file__, _locked=True)
            outputs["input_checks"] = str(check_path)
            outputs["input_counts"] = reconciled.get("counts", {})
        else:
            outputs["input_checks"] = "not built: independent csv_facts and pdf_facts not both registered/fresh"
    return outputs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Initialize source manifest and official output index")
    for name in ("company", "code", "start", "end"):
        init.add_argument("--" + name, required=True)
    init.add_argument("--case", type=Path, required=True)
    reg = sub.add_parser("register", help="Choose one official file and capture its real dependencies")
    reg.add_argument("--case", type=Path, required=True)
    reg.add_argument("--key", required=True)
    reg.add_argument("--file", type=Path, required=True)
    reg.add_argument("--depends", action="append", default=[], help="Registered upstream key; repeatable")
    reg.add_argument("--input", action="append", default=[], help="Case-relative independent input path; repeatable")
    reg.add_argument("--source-id", action="append", default=[], help="Archived original dependency; repeatable")
    reg.add_argument("--producer", type=Path, help="Script/rule whose content affects the result")
    reg.add_argument("--note", default="")
    stat = sub.add_parser("status", help="Read official versions, stale dependencies, reading and next actions")
    stat.add_argument("--case", type=Path, required=True)
    stat.add_argument("--verify-sources", action="store_true")
    stat.add_argument("--compact", action="store_true", help="Omit individual PDF notes from JSON")
    build = sub.add_parser("build", help="Rebuild registered numerical outputs using shared rules")
    build.add_argument("--case", type=Path, required=True)
    scope = sub.add_parser("scope", help="Explicitly update analysis window; registered reports become stale")
    scope.add_argument("--case", type=Path, required=True)
    scope.add_argument("--start", required=True)
    scope.add_argument("--end", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = archive.command_init(args)
            case = args.case.resolve()
            with archive.manifest_lock(case):
                if not load_state(case):
                    archive.atomic_json(case / STATE_FILE, new_state(case))
            result["official_index"] = str(case / STATE_FILE)
        elif args.command == "register":
            case = case_path(args.case)
            source_ids = set(args.source_id)
            if args.key in {"adopted_facts", "csv_facts", "pdf_facts"}:
                source_ids.update(facts_source_ids(case, archive.read_json(inside(case, args.file))))
            result = register_artifact(case, args.key, args.file, args.depends, args.input, source_ids, args.producer, args.note)
        elif args.command == "scope":
            if dt.date.fromisoformat(args.end) < dt.date.fromisoformat(args.start):
                raise ValueError("Analysis end date precedes start date")
            case = case_path(args.case)
            with archive.manifest_lock(case):
                manifest = archive.require_case(case)
                previous = manifest.get("requested_period")
                manifest["requested_period"] = {"start": args.start, "end": args.end}
                archive.atomic_json(case / "manifest.json", manifest)
            result = {"previous": previous, "requested_period": manifest["requested_period"], "report_freshness": "inspect status; source/period coverage needs review"}
        elif args.command == "build":
            result = build_case(args.case)
        else:
            result = status_report(args.case, args.verify_sources)
            if args.compact:
                result["reading_records"].pop("reports", None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
