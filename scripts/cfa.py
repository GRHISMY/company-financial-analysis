#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Financial research CLI: deterministic operations; AI supplies evidence and judgment."""
import hashlib
import importlib
import json
import os
import tempfile
from pathlib import Path
import sys

MODULES = {"source": "source_archive", "data": "financial_pipeline", "pdf": "pdf_reader",
           "returns": "returns_ledger", "finding": "findings", "case": "case_state",
           "index": "evidence_index"}
CASE_COMMANDS = {"init", "status", "register", "build", "scope"}


def flag_value(argv, name):
    for i, value in enumerate(argv):
        if value.startswith(name + "="):
            return value[len(name) + 1:]
        if value == name and i + 1 < len(argv):
            return argv[i + 1]
    return None


def return_source_ids(data):
    refs = []
    for event in data.get("events", []):
        refs.extend(event.get("sources", []))
        refs.extend(item["source"] for item in event.get("alternatives", []))
    return sorted({ref["source_id"] for ref in refs})


def register_return_output(argv):
    """Index the current authoritative ledger after put; queries never register it."""
    import case_state as cases
    import source_archive as archive
    import returns_ledger
    case_arg = flag_value(argv, "--case")
    if not case_arg:
        return
    case = cases.case_path(case_arg)
    ledger = case / returns_ledger.LEDGER
    if not ledger.is_file():
        return
    with archive.manifest_lock(case):
        if returns_ledger.validate_ledger(case).get("status") != "valid":
            raise ValueError("Return ledger validation failed; official index not updated")
        data = archive.read_json(ledger)
        cases.register_artifact(case, "returns", ledger, source_ids=return_source_ids(data), _locked=True,
                                note="Authoritative return events; proposed/bridge/execution/cash measures remain distinct")


def _restore_file(path, previous):
    if previous is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".restore-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(previous)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export_returns(argv):
    """Compute and register one export against a locked, version-checked snapshot."""
    import case_state as cases
    import source_archive as archive
    import returns_ledger as returns
    args = returns.parser().parse_args(argv)
    case = cases.case_path(args.case)
    candidate = cases.inside(case, args.output)
    key = "returns_summary." + hashlib.sha256(str(candidate.relative_to(case)).encode()).hexdigest()[:12]
    with archive.manifest_lock(case):
        ledger = case / returns.LEDGER
        manifest = case / "manifest.json"
        producer = Path(returns.__file__).resolve()
        output = cases.output_path(case, candidate, key, inputs=(ledger, manifest))
        if output == producer:
            raise ValueError("Return export cannot overwrite its producer")
        if not ledger.is_file():
            raise ValueError("Adopt return events before exporting an official summary; read-only queries may be empty")
        versions = {path: archive.sha256(path) for path in (ledger, manifest, producer)}

        def check_versions():
            for path, expected in versions.items():
                if not path.is_file() or archive.sha256(path) != expected:
                    raise ValueError(f"Return export input/producer changed during generation: {path}")

        result = returns.summarize(case, args.start, args.end, args.measure, args.payer, args.recipient)
        content = returns.markdown(result) if args.command == "render" else None
        check_versions()
        if result.get("status") == "invalid":
            print(content if content is not None else json.dumps(result, ensure_ascii=False, indent=2))
            return 2
        data = archive.read_json(ledger)
        check_versions()
        # Preflight precedes every write; retaining prior bytes also covers an index
        # registration failure or a noncooperating file change after computation.
        old_output = output.read_bytes() if output.exists() else None
        state_path = case / cases.STATE_FILE
        old_state = state_path.read_bytes() if state_path.exists() else None
        try:
            cases.register_artifact(case, "returns", ledger, source_ids=return_source_ids(data), _locked=True,
                                    note="Authoritative return events; proposed/bridge/execution/cash measures remain distinct")
            check_versions()
            if content is None:
                archive.atomic_json(output, result)
            else:
                cases.atomic_text(output, content)
            check_versions()
            entry = cases.register_artifact(case, key, output, depends=["returns"], inputs=[manifest],
                                            producer=producer, _locked=True,
                                            note="Version-bound return export; query window/measure/scopes remain in output")
            check_versions()
            states = cases.artifact_states(case)
            if any(states.get(name, {}).get("status") != "fresh" for name in ("returns", key)):
                raise ValueError("Return export dependencies changed during registration")
            if entry["sha256"] != archive.sha256(output):
                raise ValueError("Return export output changed during registration")
        except Exception:
            _restore_file(output, old_output)
            _restore_file(state_path, old_state)
            raise
    if args.command == "summary":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help"}:
        print("usage: cfa.py {init,status,register,build,scope,source,data,pdf,returns,finding,index,case} ...\n\n"
              "  init / status / register / build / scope  Official versions and dependency freshness\n"
              "  source   Archive/verify originals and extract physical-page text\n"
              "  data     Import/map, reconcile, calculate and export shared formulas\n"
              "  pdf      Budgeted reading chunks, explicit acknowledgement, resume and search\n"
              "  returns  Evidence-bound shareholder return events and compatible cash summaries\n"
              "  finding  One research finding ledger and generated evidence/report passages\n\n"
              "  index    Local SQLite evidence search, page expansion, coverage and incremental sync\n\n"
              "Run a command with --help. Existing source_archive.py/financial_pipeline.py stay compatible.")
        return 0
    command = argv.pop(0)
    if command in CASE_COMMANDS:
        return importlib.import_module("case_state").main([command] + argv)
    if command not in MODULES:
        print("Unknown command. Run cfa.py --help", file=sys.stderr)
        return 1
    if command == "returns" and argv and argv[0] in {"summary", "render"} and flag_value(argv, "--output"):
        try:
            return export_returns(argv)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            print("Return export failed: " + str(exc), file=sys.stderr)
            return 1
    result = importlib.import_module(MODULES[command]).main(argv)
    if result == 0 and command == "returns" and argv and argv[0] == "put":
        try:
            register_return_output(argv)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            print("Return output index not updated: " + str(exc), file=sys.stderr)
            return 1
    return result


if __name__ == "__main__":
    raise SystemExit(main())
