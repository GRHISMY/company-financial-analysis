#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evidence-bound shareholder return events. Decimal, stdlib, no inference or FX.

One case ledger: analysis/shareholder-returns.json. Run --help for commands.
"""
from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sys

from source_archive import atomic_json, manifest_lock, now, read_json, require_case, sha256

SCHEMA = "shareholder-return-events/v1"
LEDGER = Path("analysis/shareholder-returns.json")
KINDS = {"dividend", "market_repurchase", "restricted_share_repurchase", "treasury_transfer", "cancellation", "liability_movement", "mixed_financing"}
STAGES = {"proposed", "declared", "executed", "cash_paid", "observed"}
BASES = {"cash_payment", "plan_execution", "earnings_year", "liability_movement", "noncash_change"}
PAYERS = {"listed_parent", "subsidiary", "group", "unknown"}
RECIPIENTS = {"listed_shareholders", "minority_shareholders", "employees", "selling_shareholders", "mixed", "unknown"}
FEES = {"included", "excluded", "not_applicable", "unknown"}
ROLES = {"financial_statement", "implementation_notice", "plan_notice", "liability_bridge", "other"}
AGGREGATIONS = {"single", "period_total", "plan_total"}
CASH_KINDS = {"dividend", "market_repurchase", "restricted_share_repurchase"}
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")


def decimal(value, label):
    if isinstance(value, (float, bool)):
        raise ValueError(f"{label}: use an exact decimal string, not float/bool")
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{label}: expected a finite decimal")
    if not number.is_finite():
        raise ValueError(f"{label}: expected a finite decimal")
    return number


def date(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label}: expected YYYY-MM-DD")
    try:
        result = dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label}: expected YYYY-MM-DD")
    if result.isoformat() != value:
        raise ValueError(f"{label}: expected YYYY-MM-DD")
    return result


def choose(value, allowed, label):
    if value not in allowed:
        raise ValueError(f"{label}: choose one of {', '.join(sorted(allowed))}")
    return value


def identifier(value, label):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError(f"{label}: expected a stable identifier")
    return value


def source_reference(case, ref, manifest, bind=False):
    if not isinstance(ref, dict):
        raise ValueError("sources: expected objects")
    sid = ref.get("source_id")
    source = next((s for s in manifest.get("sources", []) if s.get("source_id") == sid), None)
    if not source or source.get("kind") != "pdf":
        raise ValueError(f"sources: {sid!r} is not an archived PDF")
    if str(source.get("code", manifest.get("code"))) != str(manifest.get("code")):
        raise ValueError(f"sources: {sid} belongs to a different company code")
    target = (case / source["path"]).resolve()
    if not target.is_relative_to(case.resolve()):
        raise ValueError(f"sources: {sid} escapes the case")
    if not target.is_file() or sha256(target) != source["sha256"]:
        raise ValueError(f"sources: {sid} is missing or changed")
    digest = ref.get("sha256")
    if digest is None and bind:
        digest = source["sha256"]
    if digest != source["sha256"]:
        raise ValueError(f"sources: {sid} hash differs from its archived version")
    pages = ref.get("pages")
    total = source.get("validation", {}).get("pages", 0)
    if not isinstance(pages, list) or not pages or any(type(p) is not int or not 1 <= p <= total for p in pages):
        raise ValueError(f"sources: {sid} needs physical PDF pages in 1..{total}")
    role = choose(ref.get("role"), ROLES, "source role")
    result = {"source_id": sid, "sha256": digest, "pages": sorted(set(pages)), "role": role}
    if ref.get("note"):
        result["note"] = str(ref["note"])
    return result


def normalize_event(case, event, manifest=None, bind=True):
    """Validate one adopted event; binding fills source hashes, never amount/date."""
    case = Path(case).resolve()
    manifest = manifest or require_case(case)
    if not isinstance(event, dict):
        raise ValueError("event must be an object")
    result = {"event_id": identifier(event.get("event_id"), "event_id"),
              "economic_id": identifier(event.get("economic_id"), "economic_id")}
    for field, allowed in [("kind", KINDS), ("stage", STAGES), ("period_basis", BASES),
                           ("payer", PAYERS), ("recipient", RECIPIENTS),
                           ("fee_basis", FEES), ("aggregation", AGGREGATIONS)]:
        result[field] = choose(event.get(field), allowed, field)
    entity = event.get("payer_entity")
    if result["payer"] == "subsidiary":
        if not isinstance(entity, str) or not entity.strip():
            raise ValueError("subsidiary payer needs payer_entity to distinguish legal entities")
        result["payer_entity"] = entity.strip()
    else:
        result["payer_entity"] = str(entity).strip() if entity else result["payer"] + ":" + str(manifest.get("code", ""))
    start, end = date(event.get("start"), "start"), date(event.get("end"), "end")
    if start > end:
        raise ValueError("start must not be after end")
    result.update(start=start.isoformat(), end=end.isoformat())
    cash_date = event.get("cash_date")
    if cash_date is not None and not start <= date(cash_date, "cash_date") <= end:
        raise ValueError("cash_date must lie inside the event window")
    result["cash_date"] = cash_date
    earnings_year = event.get("earnings_year")
    if earnings_year is not None and not re.fullmatch(r"20\d{2}", str(earnings_year)):
        raise ValueError("earnings_year must be a four-digit year or null")
    result["earnings_year"] = str(earnings_year) if earnings_year is not None else None
    currency = event.get("currency")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must be an explicit three-letter code; no FX conversion is inferred")
    result["currency"] = currency
    value = event.get("value")
    if value is None:
        if not str(event.get("unknown_reason", "")).strip():
            raise ValueError("null value needs unknown_reason; unknown is not zero")
        result.update(value=None, unknown_reason=str(event["unknown_reason"]))
    else:
        value = decimal(value, "value")
        if value < 0:
            raise ValueError("return value must be nonnegative; direction is encoded by kind")
        result["value"] = format(value, "f")
    precision = decimal(event.get("precision"), "precision")
    if precision < 0 or (precision == 0 and result["value"] is None):
        raise ValueError("precision must be positive, or zero for a confirmed exact value")
    result["precision"] = format(precision, "f")
    sources = event.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("sources: at least one archived PDF reference is required")
    result["sources"] = [source_reference(case, s, manifest, bind) for s in sources]
    result["evidence_status"] = choose(event.get("evidence_status", "supported"), {"supported", "conflicting", "incomplete"}, "evidence_status")
    result["explanation"] = str(event.get("explanation", "")).strip()
    if result["evidence_status"] != "supported" and not result["explanation"]:
        raise ValueError("conflicting/incomplete evidence needs an explanation")
    alternatives = event.get("alternatives", [])
    if not isinstance(alternatives, list):
        raise ValueError("alternatives must be a list")
    if alternatives:
        if not str(event.get("adoption_reason", "")).strip():
            raise ValueError("alternative evidence needs adoption_reason, including unresolved conflicts")
        result["adoption_reason"] = str(event["adoption_reason"])
        result["alternatives"] = []
        for item in alternatives:
            if not isinstance(item, dict) or not str(item.get("reason", "")).strip():
                raise ValueError("each alternative needs a reason and source")
            av = None if item.get("value") is None else format(decimal(item["value"], "alternative value"), "f")
            result["alternatives"].append({"value": av, "source": source_reference(case, item.get("source"), manifest, bind), "reason": str(item["reason"])})
    if event.get("plan_id") is not None:
        result["plan_id"] = identifier(event["plan_id"], "plan_id")
    if result["stage"] == "cash_paid":
        if result["period_basis"] != "cash_payment" or result["kind"] not in CASH_KINDS or result["aggregation"] == "plan_total":
            raise ValueError("cash_paid requires an actual payment window and a cash-return kind, not a plan/bridge/transfer")
        if not any(s["role"] in {"financial_statement", "implementation_notice"} for s in result["sources"]):
            raise ValueError("cash_paid needs direct payment evidence; plan notices and liability bridges cannot establish it")
        cutoff = manifest.get("requested_period", {}).get("end")
        if cutoff and end > date(cutoff, "case cutoff"):
            raise ValueError("cash_paid cannot extend beyond the case cutoff")
        if result["aggregation"] == "single" and (start != end or cash_date != start.isoformat()):
            raise ValueError("a single cash payment requires start=end=cash_date")
    if result["aggregation"] == "plan_total" and result["period_basis"] != "plan_execution":
        raise ValueError("plan_total belongs to plan_execution, never annual cash payments")
    if result["kind"] == "liability_movement" and result["period_basis"] != "liability_movement":
        raise ValueError("liability_movement keeps its own basis")
    if result["kind"] == "mixed_financing" and result["stage"] == "cash_paid":
        raise ValueError("a mixed dividend/profit/interest financing row is not pure shareholder cash")
    return result


def empty_ledger(manifest):
    return {"schema_version": SCHEMA, "company": {"name": manifest["company"], "code": str(manifest.get("code", ""))}, "events": [], "revisions": []}


def load_ledger(case):
    case = Path(case).resolve()
    manifest = require_case(case)
    path = case / LEDGER
    return read_json(path) if path.exists() else empty_ledger(manifest)


def _overlap(a, b):
    return a["start"] <= b["end"] and b["start"] <= a["end"]


def validate_ledger(case, data=None):
    """Return errors without changing files; semantic classification remains AI work."""
    case = Path(case).resolve()
    manifest = require_case(case)
    data = load_ledger(case) if data is None else data
    errors, normalized = [], []
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA:
        return {"status": "invalid", "errors": ["unsupported ledger schema"], "events": 0}
    company = data.get("company", {})
    if not isinstance(company, dict):
        errors.append("ledger company must be an object")
        company = {}
    if str(company.get("code")) != str(manifest.get("code")):
        errors.append("ledger company code does not match case")
    if not isinstance(data.get("events"), list):
        return {"status": "invalid", "errors": errors + ["events must be a list"], "events": 0}
    seen_ids, seen_economic = set(), set()
    for i, event in enumerate(data["events"]):
        try:
            item = normalize_event(case, event, manifest, bind=False)
            for field, seen in [("event_id", seen_ids), ("economic_id", seen_economic)]:
                if item[field] in seen:
                    errors.append(f"event {i}: duplicate {field} {item[field]}; attach evidence to one adopted event")
                seen.add(item[field])
            normalized.append(item)
        except (ValueError, KeyError, TypeError) as error:
            errors.append(f"event {i}: {error}")
    # A calendar total plus individual payments is evidence of the same flow twice.
    cash = [x for x in normalized if x["stage"] == "cash_paid" and x["evidence_status"] == "supported"]
    for i, first in enumerate(cash):
        for second in cash[i + 1:]:
            key = ("kind", "payer", "payer_entity", "recipient", "currency")
            if all(first[k] == second[k] for k in key) and _overlap(first, second) and "period_total" in {first["aggregation"], second["aggregation"]}:
                errors.append(f"overlapping cash coverage: {first['event_id']} / {second['event_id']}; adopt a total or its components, not both")
    return {"status": "invalid" if errors else "valid", "errors": errors, "events": len(data["events"]), "unknown_values": sum(x.get("value") is None for x in normalized), "conflicting_events": sum(x["evidence_status"] == "conflicting" for x in normalized)}


def put_events(case, events, replace=False):
    """Atomic adoption; identical puts are idempotent and changed puts need --replace."""
    case = Path(case).resolve()
    manifest = require_case(case)
    if isinstance(events, dict):
        events = events.get("events", [events])
    if not isinstance(events, list) or not events:
        raise ValueError("put expects one event or a nonempty list")
    adopted = [normalize_event(case, event, manifest, bind=True) for event in events]
    if len({x["event_id"] for x in adopted}) != len(adopted):
        raise ValueError("duplicate event_id in input batch")
    with manifest_lock(case):
        data = load_ledger(case)
        items = {x["event_id"]: x for x in data["events"]}
        changed, unchanged = [], []
        for event in adopted:
            old = items.get(event["event_id"])
            if old == event:
                unchanged.append(event["event_id"])
                continue
            if old and not replace:
                raise ValueError(f"{event['event_id']} already differs; use --replace after reviewing evidence")
            if old:
                data.setdefault("revisions", []).append({"replaced_at": now(), "event": old})
            items[event["event_id"]] = event
            changed.append(event["event_id"])
        data["events"] = sorted(items.values(), key=lambda x: (x["start"], x["event_id"]))
        check = validate_ledger(case, data)
        if check["errors"]:
            raise ValueError("; ".join(check["errors"]))
        if changed:
            data["updated_at"] = now()
            atomic_json(case / LEDGER, data)
    return {"path": str(case / LEDGER), "changed": changed, "unchanged": unchanged, "validation": check}


def summarize(case, start=None, end=None, measure="cash", payer=None, recipient=None):
    """Never emit a cross-scope/fee/currency grand total or prorate partial periods."""
    case = Path(case).resolve()
    manifest, data = require_case(case), load_ledger(case)
    check = validate_ledger(case, data)
    if check["errors"]:
        return {"status": "invalid", "errors": check["errors"], "groups": [], "excluded": []}
    if (start is None) != (end is None):
        raise ValueError("provide both --start and --end")
    start = start or manifest["requested_period"]["start"]
    end = end or manifest["requested_period"]["end"]
    if date(start, "summary start") > date(end, "summary end"):
        raise ValueError("summary start must not follow end")
    choose(measure, {"cash", "execution"}, "measure")
    groups, excluded = {}, []
    for raw_event in data["events"]:
        event = normalize_event(case, raw_event, manifest, bind=False)
        reason = None
        if event["end"] < start or event["start"] > end:
            reason = "outside_window"
        elif not (start <= event["start"] and event["end"] <= end):
            reason = "partial_overlap_not_prorated"
        elif payer and event["payer"] != payer or recipient and event["recipient"] != recipient:
            reason = "scope_filter"
        elif event["evidence_status"] != "supported":
            reason = "evidence_" + event["evidence_status"]
        elif event["kind"] not in CASH_KINDS:
            reason = "not_a_pure_cash_return"
        elif "unknown" in (event["payer"], event["recipient"]) or event["recipient"] == "mixed":
            reason = "unresolved_payment_scope"
        elif measure == "cash" and not (event["stage"] == "cash_paid" and event["period_basis"] == "cash_payment"):
            reason = "not_actual_cash_payment"
        elif measure == "execution" and not (event["stage"] == "executed" and event["period_basis"] == "plan_execution"):
            reason = "not_execution_measure"
        if reason:
            excluded.append({"event_id": event["event_id"], "reason": reason})
            continue
        key = tuple(event[k] for k in ("kind", "payer", "payer_entity", "recipient", "currency", "fee_basis"))
        bucket = groups.setdefault(key, {**dict(zip(("kind", "payer", "payer_entity", "recipient", "currency", "fee_basis"), key)), "known_subtotal": Decimal(0), "event_ids": [], "unknown_event_ids": [], "source_refs": []})
        bucket["event_ids"].append(event["event_id"])
        bucket["source_refs"].extend(event["sources"])
        if event["value"] is None:
            bucket["unknown_event_ids"].append(event["event_id"])
        else:
            bucket["known_subtotal"] += Decimal(event["value"])
    result = []
    for key, bucket in sorted(groups.items()):
        bucket["known_subtotal"] = format(bucket["known_subtotal"], "f")
        bucket["complete"] = not bucket["unknown_event_ids"]
        bucket["total"] = bucket["known_subtotal"] if bucket["complete"] else None
        bucket["fee_comparable"] = bucket["fee_basis"] != "unknown"
        result.append(bucket)
    eligible = sum(len(bucket["event_ids"]) for bucket in result)
    aggregation = "no_eligible_events" if not eligible else "amount_incomplete" if any(not bucket["complete"] for bucket in result) else "recorded_events_aggregated"
    return {"schema_version": "shareholder-return-summary/v1", "status": "ready" if data["events"] else "empty", "aggregation_status": aggregation, "eligible_events": eligible, "company": data["company"], "window": {"start": start, "end": end}, "measure": measure, "groups": result, "excluded": excluded,
            "notice": "Empty groups or status=ready do not establish zero payments or that all corporate payments were found. No grand total across event kinds, legal scopes, currencies or fee bases. A complete bucket covers only its recorded events. Sources and semantic classification still require AI review."}


def markdown(summary):
    if summary.get("status") == "invalid":
        return "# 股东回报核验失败\n\n" + "\n".join("- " + x for x in summary["errors"]) + "\n"
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    lines = ["# 股东回报事件汇总", "", f"期间：{summary['window']['start']} — {summary['window']['end']}；口径：{summary['measure']}；金额为原币元。", "", "|类别|主体范围|付款实体|收款对象|货币|费用口径|已知小计|记录完整合计|事件|", "|---|---|---|---|---|---|---:|---:|---|"]
    for x in summary["groups"]:
        lines.append("|" + "|".join(cell(y) for y in [x["kind"], x["payer"], x["payer_entity"], x["recipient"], x["currency"], x["fee_basis"], x["known_subtotal"], x["total"] if x["total"] is not None else "未知", ", ".join(x["event_ids"])]) + "|")
    lines += ["", summary["notice"], "", "未纳入本口径："]
    lines.extend(f"- {x['event_id']}: {x['reason']}" for x in summary["excluded"])
    return "\n".join(lines) + "\n"


def template(kind="dividend"):
    choose(kind, KINDS, "kind")
    stage = "cash_paid" if kind in CASH_KINDS else "observed"
    basis = "liability_movement" if kind == "liability_movement" else "cash_payment" if kind in CASH_KINDS else "noncash_change"
    return {"event_id": "replace-event-id", "economic_id": "replace-economic-payment-id", "kind": kind, "stage": stage, "period_basis": basis,
            "aggregation": "single", "start": None, "end": None, "cash_date": None, "earnings_year": None,
            "payer": "listed_parent", "recipient": "listed_shareholders" if kind == "dividend" else "selling_shareholders" if kind == "market_repurchase" else "employees" if kind in {"restricted_share_repurchase", "treasury_transfer"} else "unknown",
            "value": None, "unknown_reason": "填写未取得金额的原因；已确认时填写金额字符串并删除本字段", "currency": "CNY", "precision": "0.01", "fee_basis": "not_applicable" if kind == "dividend" else "unknown",
            "evidence_status": "supported", "sources": [{"source_id": "", "pages": [], "role": "implementation_notice" if kind in CASH_KINDS else "other"}], "explanation": ""}


def derived_destination(case, path):
    path = Path(path).resolve()
    case = Path(case).resolve()
    manifest = require_case(case)
    protected = {(case / LEDGER).resolve(), case / "manifest.json"}
    protected.update((case / s["path"]).resolve() for s in manifest["sources"])
    if path in protected or path.is_relative_to(case / "raw"):
        raise ValueError("derived output cannot overwrite originals, manifest, or the authoritative return ledger")
    return path


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    t = sub.add_parser("template", help="Print an event template; no guessed payment values")
    t.add_argument("--kind", choices=sorted(KINDS), default="dividend")
    put = sub.add_parser("put", help="Validate/adopt one event or a batch atomically")
    put.add_argument("--case", type=Path, required=True)
    put.add_argument("--file", type=Path, required=True)
    put.add_argument("--replace", action="store_true", help="Replace reviewed event IDs, retaining revisions")
    valid = sub.add_parser("validate", help="Verify identity, source hashes/pages, scopes and duplicate cash coverage")
    valid.add_argument("--case", type=Path, required=True)
    for name in ("summary", "render"):
        cmd = sub.add_parser(name, help="Group compatible actual payments or execution amounts without a grand total")
        cmd.add_argument("--case", type=Path, required=True)
        cmd.add_argument("--start")
        cmd.add_argument("--end")
        cmd.add_argument("--measure", choices=["cash", "execution"], default="cash")
        cmd.add_argument("--payer", choices=sorted(PAYERS))
        cmd.add_argument("--recipient", choices=sorted(RECIPIENTS))
        cmd.add_argument("--output", type=Path)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "template":
            result = template(args.kind)
        elif args.command == "put":
            result = put_events(args.case, read_json(args.file), args.replace)
        elif args.command == "validate":
            result = validate_ledger(args.case)
            if not (args.case / LEDGER).exists():
                result = {"status": "missing", "errors": [f"No event ledger at {LEDGER}"], "events": 0}
        else:
            result = summarize(args.case, args.start, args.end, args.measure, args.payer, args.recipient)
            if args.output:
                args.output = derived_destination(args.case, args.output)
            if args.command == "render":
                content = markdown(result)
                if args.output:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(content, encoding="utf-8")
                else:
                    print(content, end="")
            elif args.output:
                atomic_json(args.output, result)
        if args.command != "render":
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("status") in {"invalid", "missing"} else 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
