#!/usr/bin/env python3
"""Lossless Lixinger CSV ingestion and explicit, Decimal-based financial analysis.

Run --help, import --help, or calculate --help. No network or third-party packages.
Source files are read only; mappings are explicit and ambiguous matches fail.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import datetime as dt
import fnmatch
import hashlib
import json
import re
import sys
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation, getcontext
from pathlib import Path

getcontext().prec = 50
D = Decimal
SCOPES = {"合并报表": "consolidated", "合并": "consolidated", "母公司报表": "parent", "母公司": "parent"}
MISSING = {"", "-", "--", "—", "－", "不适用", "N/A", "NA", "null"}
NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
DATE = re.compile(r"^(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?$")
METADATA = {"日期", "货币", "币种", "审计意见", "会计师事务所", "财报类型", "股票代码", "股票名称", "证券代码", "证券名称", "报表口径"}
INSTANT_FIELDS = {"assets", "liabilities", "equity", "cash_begin", "cash_end", "monetary_cash", "inventory", "receivables", "contract_liabilities", "debt", "restricted_cash", "fixed_assets", "construction_in_progress", "long_term_equity_investments"}


def dec(value):
    x = D(str(value))
    if not x.is_finite():
        raise ValueError(f"Nonfinite number: {value!r}")
    return x


def number(x):
    return format(x, "f") if isinstance(x, D) else x


def dump(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=number) + "\n", encoding="utf-8")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def literal(raw):
    """Decode Excel CSV literals, never evaluate formulae."""
    s = raw.strip().lstrip("\ufeff")
    if s.startswith('="') and s.endswith('"'):
        return s[2:-1].replace('""', '"')
    if s.startswith("=") and NUMBER.fullmatch(s[1:].replace(",", "")):
        return s[1:]
    return s


def clean_label(label):
    return re.sub(r"[（(](?:亿|亿元|万|万元|元|%|％|人|股|元/股)[）)]$", "", label).strip()


def row_unit(label, currency):
    # Row-level units override metadata: Lixinger's currency row may say 元
    # while amount labels explicitly carry 亿.
    if re.search(r"[（(](?:亿|亿元)[）)]$", label):
        return "amount", "CNY" if currency in {"元", "人民币", "人民币元", "CNY", ""} else currency, D("100000000"), "explicit_label"
    if re.search(r"[（(](?:万|万元)[）)]$", label):
        return "amount", "CNY" if currency in {"元", "人民币", "人民币元", "CNY", ""} else currency, D("10000"), "explicit_label"
    if re.search(r"[（(]元[）)]$", label):
        return "amount", "CNY", D(1), "explicit_label"
    if re.search(r"元[/／]股|每股", label):
        return "per_share", "CNY/share", D(1), "label"
    if re.search(r"^(?:PE|PB|PS|PCF|P/E|P/B)(?:[-（(]|$)", label, re.I):
        return "multiple", "multiple", D(1), "label_inferred"
    if "涨跌幅" in label:
        return "ratio", "ratio", D(1), "label_inferred"
    # 汇率 is a word within an amount line, not a percentage/unit of that line.
    if re.search(r"汇率变动对现金(?:及现金等价物)?的影响", label):
        scale = D("10000") if currency == "万元" else D("100000000") if currency == "亿元" else D(1)
        return "amount", "CNY" if currency in {"元", "人民币", "人民币元", "CNY", "万元", "亿元"} else currency or None, scale, "cashflow_amount_label"
    if re.search(r"[率比]|ROE|ROA|ROIC|GM|EBITDA率|[（(][%％][）)]", label, re.I):
        return "ratio", "ratio", D(1), "label_inferred"
    if re.search(r"人数|员工数量|职工数量|[（(]人[）)]$", label):
        return "count", "person", D(1), "label"
    if re.search(r"股数|股本.*[（(]股[）)]$", label):
        return "count", "share", D(1), "label"
    if label in METADATA:
        return "metadata", None, D(1), "metadata"
    if re.search(r"天数|[（(]天[）)]$", label):
        return "duration", "day", D(1), "label"
    if currency in {"元", "人民币", "人民币元", "CNY", "万元", "亿元"}:
        scale = D("10000") if currency == "万元" else D("100000000") if currency == "亿元" else D(1)
        return "amount", "CNY", scale, "source_currency_metadata"
    currency_codes = {"美元":"USD", "港元":"HKD", "欧元":"EUR", "日元":"JPY", "USD":"USD", "HKD":"HKD", "EUR":"EUR", "JPY":"JPY"}
    if currency in currency_codes:
        return "amount", currency_codes[currency], D(1), "source_currency_metadata"
    # Without a source amount unit, retain unresolved rather than assume yuan.
    return "unknown", None, D(1), "unresolved"


def cell_value(raw, scale, zero_quantum=None):
    s = literal(raw)
    if s in MISSING:
        return {"value": None, "status": "missing", "precision": None}
    percent = s.endswith(("%", "％"))
    t = s[:-1] if percent else s
    t = t.replace(",", "").replace("，", "")
    negative = t.startswith("(") and t.endswith(")")
    if negative:
        t = "-" + t[1:-1]
    if not NUMBER.fullmatch(t):
        return {"value": None, "status": "formula_unexecuted" if s.startswith("=") else "text", "precision": None, "text": s}
    x = dec(t)
    effective_scale = scale / 100 if percent else scale
    plain_zero = x == 0 and "." not in t and "e" not in t.lower()
    # CSV numeric zero is still a displayed value, not proof of an exact
    # unrounded PDF zero. Lixinger drops trailing decimals for structural zeros.
    quantum = zero_quantum if plain_zero and zero_quantum is not None else D(1).scaleb(x.as_tuple().exponent) * effective_scale
    return {"value": number(x * effective_scale), "status": "number", "precision": number(abs(quantum)), "percent_literal": percent, "zero_literal":plain_zero,
        "precision_basis":"finest_same_unit_source_grid_for_plain_zero" if plain_zero and zero_quantum is not None else "displayed_numeric_precision"}


def statement_type(path):
    name = str(path)
    if "资产负债" in name:
        return "balance"
    if "现金流" in name:
        return "cashflow"
    if "利润" in name or "损益" in name:
        return "income"
    return "unknown"


def resolve_scope(raw, path, override=None):
    if raw in SCOPES:
        result = SCOPES[raw]
        if override and result != override:
            raise ValueError(f"Scope override conflicts with source column: {path}/{raw}")
        return result, "source_column"
    if override:
        return override, "explicit_override"
    named = [scope for text,scope in (("合并报表","consolidated"),("母公司报表","parent")) if text in Path(path).name]
    if len(named) == 1:
        return named[0], "source_filename"
    return "unspecified", "unresolved"


def import_csv(path, scope_override=None):
    path = Path(path).resolve()
    data = path.read_bytes()
    data.decode("utf-8-sig")
    digest = hashlib.sha256(data).hexdigest()
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        previous = 0
        for r in reader:
            rows.append((previous + 1, reader.line_num, r))
            previous = reader.line_num
    if not rows:
        raise ValueError(f"Empty CSV: {path}")
    source_header = [literal(c) for c in rows[0][2]]
    date_rows = [r for r in rows if r[2] and literal(r[2][0]) == "日期"]
    if not date_rows:
        raise ValueError(f"No 日期 metadata row, inspect export: {path}")
    date_columns = [i for i,c in enumerate(date_rows[0][2]) if DATE.fullmatch(literal(c))]
    if not date_columns:
        raise ValueError(f"No valid report-date columns: {path}")
    data_start = min(date_columns)
    source_column_shift, header_row = 0, rows[0][0]
    layout = "web-three-identity-columns"
    if data_start == 1:
        # Standard export has metadata in rows; adapt internally to the same
        # long-row representation while retaining original physical positions.
        # No fields from the first two years are consumed as identity columns.
        layout = "standard-metadata-rows"
        metadata = {literal(r[2][0]):r for r in rows if r[2] and literal(r[2][0]) in METADATA}
        code_row = metadata.get("股票代码", metadata.get("证券代码"))
        name_row = metadata.get("股票名称", metadata.get("证券名称"))
        if not code_row:
            raise ValueError(f"Standard export lacks stock-code metadata: {path}")
        codes = {literal(c) for c in code_row[2][1:] if literal(c)}
        if len(codes) != 1:
            raise ValueError(f"Export one company at a time; multiple/absent stock codes: {codes}")
        code = next(iter(codes))
        names = {literal(c) for c in name_row[2][1:] if literal(c)} if name_row else set()
        if len(names) > 1:
            raise ValueError("Export one company at a time; multiple company names")
        name = next(iter(names),"")
        report_row = metadata.get("财报类型")
        report_labels = report_row[2][1:] if report_row else [""]*len(date_columns)
        header_row = report_row[0] if report_row else None
        header = ["财报类型","股票代码","股票名称",*report_labels]
        rows = [(0,0,header)] + [(start,end,[row[0] if row else "",code,name,*row[1:]]) for start,end,row in rows]
        source_column_shift = 2
    elif data_start == 3:
        header = source_header
        if not any("股票代码" in x or "证券代码" in x for x in header[:3]):
            raise ValueError(f"Unsupported source identities: {header[:6]}")
    else:
        raise ValueError(f"Unsupported data start column {data_start+1}; inspect export: {path}")
    dates, currencies = {}, {}
    for _, _, row in rows[1:]:
        if len(row) < 3:
            continue
        label, _, scope_label = map(literal, row[:3])
        scope, _ = resolve_scope(scope_label, path, scope_override)
        if label == "日期":
            dates[scope] = [literal(c) for c in row[3:]]
        elif label in {"货币", "币种"}:
            currencies[scope] = [literal(c) for c in row[3:]]
    zero_grids = {}
    for _, _, row in rows[1:]:
        if len(row) < 4:
            continue
        label, _, raw_scope = map(literal,row[:3])
        scope, _ = resolve_scope(raw_scope,path,scope_override)
        for index,raw in enumerate(row[3:]):
            currency = currencies.get(scope,[])[index] if index < len(currencies.get(scope,[])) else ""
            kind,unit,scale,_ = row_unit(label,currency)
            parsed = cell_value(raw,scale)
            if parsed["status"] == "number" and not parsed["zero_literal"]:
                key=(kind,unit,number(scale))
                quantum=dec(parsed["precision"])
                zero_grids[key]=min(zero_grids.get(key,quantum),quantum)
    records, section, anchor, occurrences = [], {}, {}, defaultdict(int)
    global_section = None
    for start_line, end_line, row in rows[1:]:
        row = row + [""] * max(0, len(header) - len(row))
        label, code, scope_label = map(literal, row[:3])
        scope, scope_basis = resolve_scope(scope_label, path, scope_override)
        has_identity = bool(code or scope_label)
        if not has_identity and label:
            global_section = label
            continue
        if not label:
            continue
        if re.match(r"^[一二三四五六七八九十]+[、．.]", label):
            section[scope] = label
            anchor.pop(scope, None)
        parent = anchor.get(scope) if re.match(r"^[（(]其中[）)]|^其中[：:]", label) else None
        parts = [p for p in (global_section, section.get(scope), parent, label) if p]
        parts = [p for i, p in enumerate(parts) if i == 0 or p != parts[i-1]]
        occurrences[(scope, label)] += 1
        occurrence = occurrences[(scope, label)]
        for index, raw in enumerate(row[3:], 3):
            scope_dates = dates.get(scope, dates.get("unspecified", []))
            date = scope_dates[index-3] if index-3 < len(scope_dates) else None
            if date and DATE.fullmatch(date):
                date = "-".join(f"{int(x):02d}" if i else x for i, x in enumerate(DATE.fullmatch(date).groups()))
            scope_currencies = currencies.get(scope, [])
            currency = scope_currencies[index-3] if index-3 < len(scope_currencies) else ""
            kind, unit, scale, unit_basis = row_unit(label, currency)
            parsed = cell_value(raw, scale,zero_grids.get((kind,unit,number(scale))))
            if label in {"股票代码", "证券代码"}:
                # Identifiers remain text even when a standard export writes
                # bare digits. Decimal conversion would erase leading zeroes.
                parsed = {"value":None, "status":"identifier", "precision":None, "text":literal(raw)}
            if parsed.get("percent_literal"):
                kind, unit, unit_basis = "ratio", "ratio", "percent_literal"
            records.append({
                "record_id": f"{path.name}:{start_line}:{index+1-source_column_shift}",
                "source": {"file": str(path), "sha256": digest, "row": start_line, "row_end": end_line, "column": index+1-source_column_shift, "column_header": header[index] if index < len(header) else None, "column_header_row":header_row},
                "stock_code": code, "scope": scope, "scope_raw": scope_label, "scope_basis":scope_basis,
                "company_name":scope_label if scope_label not in SCOPES else None,
                "statement": statement_type(path), "date": date,
                "label": label, "occurrence": occurrence,
                "source_path": parts, "path_status": "inferred_from_visible_labels; select_source_row_for_mapping",
                "raw": raw, "decoded": literal(raw), "kind": kind,
                "unit": unit, "scale": number(scale), "unit_basis": unit_basis,
                **parsed,
            })
        # Ratios/platform metrics are not dependable parents for 其中 items.
        if label not in METADATA and not re.match(r"^[（(](?:其中|备注)[）)]", label) and row_unit(label, "")[0] not in {"ratio", "metadata"}:
            anchor[scope] = label
    return {"file": str(path), "sha256": digest, "bytes": len(data), "header": source_header, "layout":layout, "data_start_column":data_start+1, "records": records}


def matches(record, selector):
    allowed = {"file", "row", "column", "scope", "label", "occurrence", "date", "statement", "record_id", "source_path"}
    unknown = set(selector) - allowed
    if unknown:
        raise ValueError(f"Unknown selector keys: {unknown}")
    for key, expected in selector.items():
        if key == "file":
            actual = record["source"]["file"]
            if not (fnmatch.fnmatch(actual, expected) or fnmatch.fnmatch(Path(actual).name, expected)):
                return False
        else:
            actual = record["source"].get(key) if key in {"row", "column"} else record.get(key)
            if actual != expected:
                return False
    return True


def mapped_facts(imported, config):
    records = [r for source in imported["sources"] for r in source["records"]]
    periods = {}
    period_overrides = config.get("periods", [])
    for item in config.get("mappings", []):
        field = item["field"]
        candidates = [r for r in records if matches(r, item["selector"])]
        if not candidates:
            raise ValueError(f"Selector matched nothing for {field}: {item['selector']}")
        by_period = defaultdict(list)
        for r in candidates:
            by_period[(r["date"], r["scope"])].append(r)
        for (date, scope), selected in by_period.items():
            if len(selected) != 1:
                raise ValueError(f"Ambiguous selector for {field}/{date}/{scope}: {[r['record_id'] for r in selected]}")
            r = selected[0]
            if not date or not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", date):
                raise ValueError(f"No valid report date for {r['record_id']}")
            period = {"id": f"{date}/{scope}", "start": date[:4]+"-01-01", "end": date, "scope": scope, "currency": config.get("currency", "CNY"), "basis": config.get("basis", "unverified-export"), "period_type": "annual" if date.endswith("12-31") else "ytd"}
            overrides = [p for p in period_overrides if p.get("date", p.get("end")) == date and p.get("scope", scope) == scope]
            if len(overrides) > 1:
                raise ValueError(f"Duplicate period override for {date}/{scope}")
            if overrides:
                period.update({k:v for k,v in overrides[0].items() if k != "date"})
            target = periods.setdefault(period["id"], {**period, "fields": {}})
            if field in target["fields"]:
                raise ValueError(f"Duplicate field {field} in {period['id']}")
            sign = dec(item.get("sign", "1"))
            if sign not in {D(-1), D(1)}:
                raise ValueError("Mapping sign must be 1 or -1; unit transformations use multiplier")
            multiplier = dec(item.get("multiplier", "1"))
            unit = item.get("unit", r["unit"])
            if r["kind"] == "unknown" and "unit" not in item:
                raise ValueError(f"Unresolved unit for {r['record_id']}; specify mapping unit and multiplier")
            target["fields"][field] = {
                "value": number(dec(r["value"]) * sign * multiplier) if r["value"] is not None else None,
                "precision": number(dec(r["precision"]) * abs(multiplier)) if r["precision"] is not None else None,
                "unit": unit, "status": r["status"],
                "kind": item.get("kind", "instant" if field in INSTANT_FIELDS else "duration"),
                "source": {**r["source"], "record_id": r["record_id"], "label": r["label"], "source_path": r["source_path"], "raw": r["raw"], "original_value": r["value"], "sign": number(sign), "multiplier": number(multiplier), "mapping_reason": item.get("reason")},
            }
    return {"schema_version": "financial-facts/v1", "company": config.get("company", {}), "periods": list(periods.values()), "comparisons": config.get("comparisons", []), "differences": config.get("differences", [])}


def linear(fields, coefficients, formula):
    coefficients = {k: dec(v) for k, v in coefficients.items() if dec(v) != 0}
    missing = [k for k in coefficients if fields.get(k, {}).get("value") is None]
    if missing:
        return {"value": None, "status": "missing_inputs", "missing": missing, "formula": formula}
    value = sum((c * dec(fields[k]["value"]) for k,c in coefficients.items()), D(0))
    tolerance = sum((abs(c) * dec(fields[k]["precision"]) / 2 for k,c in coefficients.items()), D(0))
    return {"value": number(value), "rounding_bound": number(tolerance), "status": "calculated", "formula": formula, "coefficients": {k:number(v) for k,v in coefficients.items()}, "sources": {k: fields[k].get("source") for k in coefficients}}


def reconciliation(fields, coefficients, reported, formula):
    computed = linear(fields, coefficients, formula)
    report = fields.get(reported, {})
    if computed["value"] is None or report.get("value") is None:
        return {**computed, "status": "missing_inputs", "missing": sorted(set(computed.get("missing", []) + ([] if report.get("value") is not None else [reported]))), "reported_field": reported}
    difference = dec(computed["value"]) - dec(report["value"])
    bound = dec(computed["rounding_bound"]) + dec(report["precision"]) / 2
    return {**computed, "reported_field": reported, "reported": report["value"], "reported_source": report.get("source"), "difference": number(difference), "tolerance": number(bound), "status": "pass" if abs(difference) <= bound else "unexplained_difference"}


def ratio(a, b, formula):
    if a.get("value") is None or b.get("value") is None:
        return {"value": None, "status": "missing_inputs", "formula": formula}
    if dec(b["value"]) <= 0:
        return {"value": None, "status": "nonpositive_denominator", "formula": formula}
    return {"value": number(dec(a["value"])/dec(b["value"])), "status": "calculated", "formula": formula}


CORE = {"revenue":1, "cost":-1, "surtax":-1, "selling":-1, "admin":-1, "rd":-1, "finance_expense":-1, "finance_interest_income":-1}
FINANCIAL = {"financial_interest_income":1, "financial_interest_expense":-1, "financial_fee_income":1, "financial_fee_expense":-1}
MISC = {"finance_interest_income":1, **FINANCIAL, "investment_income":1, "fair_value_gain":1, "asset_disposal_gain":1}


def combine_coefficients(*groups):
    result = {}
    for group in groups:
        for key, coefficient in group.items():
            result[key] = result.get(key, 0) + coefficient
    return {key:value for key,value in result.items() if value != 0}


OPERATING = combine_coefficients(CORE, MISC, {"other_income":1, "credit_impairment_impact":1, "asset_impairment_impact":1, "other_operating_impact":1})

# The same declarations drive Decimal calculation and spreadsheet formula
# export. Formula text is explanatory; code never evaluates it.
DERIVED_FORMULAS = {
    "core_profit": {"kind":"linear", "coefficients":CORE, "formula":"revenue-cost-surtax-selling-admin-rd-(finance_expense+finance_interest_income)"},
    "financial_net": {"kind":"linear", "coefficients":FINANCIAL, "formula":"financial_interest_income-financial_interest_expense+financial_fee_income-financial_fee_expense"},
    "miscellaneous_income": {"kind":"linear", "coefficients":MISC, "formula":"finance_interest_income+financial_net+investment_income+fair_value_gain+asset_disposal_gain"},
    "simplified_fcf": {"kind":"linear", "coefficients":{"cfo":1,"capex":-1}, "formula":"CFO-capex (simplified FCF; not distributable shareholder cash)"},
    "gross_profit": {"kind":"linear", "coefficients":{"revenue":1,"cost":-1}, "formula":"revenue-cost"},
    "core_margin": {"kind":"ratio", "numerator":"core_profit", "denominator":"revenue", "denominator_policy":"positive", "formula":"core_profit/revenue"},
    "gross_margin": {"kind":"ratio", "numerator":"gross_profit", "denominator":"revenue", "denominator_policy":"positive", "formula":"gross_profit/revenue"},
    "cfo_to_net_profit": {"kind":"ratio", "numerator":"cfo", "denominator":"net_profit", "denominator_policy":"positive", "formula":"CFO/consolidated net profit (financial subsidiary effects require separate analysis)"},
}
CHECK_FORMULAS = {
    "operating_profit": {"kind":"linear", "coefficients":OPERATING, "reported":"operating_profit", "formula":"core_profit+other_income+miscellaneous_income+signed_impairment_impacts+other_operating_impact"},
    "profit_before_tax": {"kind":"linear", "coefficients":{"operating_profit":1,"nonoperating_income":1,"nonoperating_expense":-1}, "reported":"profit_before_tax", "formula":"operating_profit+nonoperating_income-nonoperating_expense"},
    "net_profit": {"kind":"linear", "coefficients":{"profit_before_tax":1,"income_tax":-1}, "reported":"net_profit", "formula":"profit_before_tax-income_tax"},
    "net_attribution": {"kind":"linear", "coefficients":{"attributable_profit":1,"minority_profit":1}, "reported":"net_profit", "formula":"attributable_profit+minority_profit", "not_applicable_scopes":["parent"]},
    "balance": {"kind":"linear", "coefficients":{"liabilities":1,"equity":1}, "reported":"assets", "formula":"liabilities+equity"},
    "cash_flow": {"kind":"linear", "coefficients":{"cfo":1,"cfi":1,"cff":1,"fx_effect":1}, "reported":"cash_change", "formula":"CFO+CFI+CFF+FX"},
    "cash_rollforward": {"kind":"linear", "coefficients":{"cash_end":1,"cash_begin":-1}, "reported":"cash_change", "formula":"cash_end-cash_begin"},
}


def evaluate_derived(fields):
    result = {}
    for name, definition in DERIVED_FORMULAS.items():
        if definition["kind"] == "linear":
            result[name] = linear(fields, definition["coefficients"], definition["formula"])
        else:
            numerator = definition["numerator"]
            a = result[numerator] if numerator in DERIVED_FORMULAS else fields.get(numerator,{})
            result[name] = ratio(a, fields.get(definition["denominator"],{}), definition["formula"])
    return result


def _required_inputs(definition):
    if definition["kind"] == "linear":
        return list(definition["coefficients"])
    numerator = definition["numerator"]
    items = _required_inputs(DERIVED_FORMULAS[numerator]) if numerator in DERIVED_FORMULAS else [numerator]
    return list(dict.fromkeys([*items,definition["denominator"]]))


def formula_catalog(only=None):
    """Return an independent, JSON-safe description of implemented formulae."""
    all_definitions = {**DERIVED_FORMULAS, **{"check."+k:v for k,v in CHECK_FORMULAS.items()}}
    selected = list(all_definitions) if only is None else list(dict.fromkeys(only))
    unknown = set(selected) - set(all_definitions)
    if unknown:
        raise ValueError(f"Unknown formula names: {sorted(unknown)}")
    definitions = {}
    for name in selected:
        item = deepcopy(all_definitions[name])
        item["required_inputs"] = _required_inputs(item)
        if "reported" in item:
            item["required_inputs"] = list(dict.fromkeys([*item["required_inputs"],item["reported"]]))
        definitions[name] = item
    canonical = json.dumps(all_definitions,sort_keys=True,separators=(",",":"),ensure_ascii=False)
    return {"schema_version":"financial-formulas/v1", "catalog_sha256":hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "input_unit":"one common currency unit", "ratio_unit":"fraction", "definitions":definitions,
        "missing_policy":"Missing is not zero. Ratios require a strictly positive denominator. Reconciliation pass/fail also requires source precision; Excel exports differences, not a guessed tolerance."}


def excel_cell_reference(binding):
    """Validate a scalar cell reference and quote its optional sheet name."""
    if isinstance(binding,dict):
        if set(binding) - {"sheet","cell"} or "cell" not in binding:
            raise ValueError("Excel binding object accepts only sheet and cell")
        sheet, cell = binding.get("sheet"), binding["cell"]
    elif isinstance(binding,str):
        match = re.fullmatch(r"(?:(?:'((?:[^']|'')+)'|([^!']+))!)?(\$?[A-Za-z]{1,3}\$?[1-9]\d{0,6})",binding)
        if not match:
            raise ValueError(f"Expected one Excel cell, not an expression/range: {binding!r}")
        sheet = match[1].replace("''", "'") if match[1] is not None else match[2]
        cell = match[3]
    else:
        raise ValueError("Excel binding must be a cell string, {sheet,cell}, or null")
    if not isinstance(cell,str) or not re.fullmatch(r"\$?[A-Za-z]{1,3}\$?[1-9]\d{0,6}",cell):
        raise ValueError(f"Invalid Excel cell: {cell!r}")
    letters, row = re.fullmatch(r"\$?([A-Za-z]+)\$?(\d+)",cell).groups()
    column = 0
    for char in letters.upper():
        column = column*26 + ord(char)-ord("A")+1
    if column > 16384 or int(row) > 1048576:
        raise ValueError(f"Excel cell out of bounds: {cell}")
    if sheet is not None:
        if (not isinstance(sheet,str) or not sheet or len(sheet)>31
                or re.search(r"[\[\]:*?/\\\x00-\x1f]",sheet) or sheet.startswith("'") or sheet.endswith("'")):
            raise ValueError(f"Invalid Excel sheet name: {sheet!r}")
        return "'"+sheet.replace("'","''")+"'!"+cell.upper()
    return cell.upper()


def _excel_linear(coefficients, bindings):
    terms = []
    for key, coefficient in coefficients.items():
        coefficient = dec(coefficient)
        if coefficient == 0:
            continue
        magnitude = abs(coefficient)
        term = bindings[key] if magnitude == 1 else number(magnitude)+"*"+bindings[key]
        terms.append(("-" if coefficient<0 else "+")+term)
    return "".join(terms).lstrip("+") or "0"


def _excel_guard(expression, required, bindings):
    numeric = ",".join("ISNUMBER("+bindings[key]+")" for key in required)
    return "=IF(AND("+numeric+"),"+expression+",NA())"


def excel_formulas(bindings, only=None):
    """Export guarded Excel formulae from the declarations used by calculate.

    Bindings are scalar raw-input cells; null/omitted bindings leave a formula
    explicitly unavailable. A blank/text/error input at runtime yields #N/A.
    """
    if not isinstance(bindings,dict):
        raise ValueError("Excel bindings must be an object mapping input field to cell")
    catalog = formula_catalog(only)
    input_names = set().union(*(set(_required_inputs(d)) | ({d["reported"]} if "reported" in d else set()) for d in [*DERIVED_FORMULAS.values(),*CHECK_FORMULAS.values()]))
    unknown = set(bindings)-input_names
    if unknown:
        raise ValueError(f"Unknown canonical input bindings: {sorted(unknown)}")
    cells = {key:excel_cell_reference(value) for key,value in bindings.items() if value is not None}
    output = {}
    for name, definition in catalog["definitions"].items():
        required = definition["required_inputs"]
        missing = [key for key in required if key not in cells]
        item = {"status":"missing_bindings" if missing else "ready", "required_inputs":required,
            "missing":missing, "formula":None, "definition":definition["formula"]}
        if not missing:
            if definition["kind"] == "linear":
                expression = _excel_linear(definition["coefficients"],cells)
            else:
                numerator = definition["numerator"]
                expression = _excel_linear(DERIVED_FORMULAS[numerator]["coefficients"],cells) if numerator in DERIVED_FORMULAS else cells[numerator]
                denominator = cells[definition["denominator"]]
                expression = "IF("+denominator+">0,("+expression+")/"+denominator+",NA())"
            if "reported" in definition:
                item["computed_formula"] = _excel_guard(expression,required,cells)
                expression = "("+expression+")-"+cells[definition["reported"]]
                item["result_kind"] = "difference, not precision-aware pass/fail"
            item["formula"] = _excel_guard(expression,required,cells)
            if "not_applicable_scopes" in definition:
                item["not_applicable_scopes"] = definition["not_applicable_scopes"]
        output[name] = item
    return {"schema_version":"financial-excel-formulas/v1", "catalog_sha256":catalog["catalog_sha256"],
        "bindings":cells, "formulas":output, "missing_policy":catalog["missing_policy"]}


def validate_period(p):
    for key in ("id", "start", "end", "scope", "currency", "basis", "period_type", "fields"):
        if key not in p:
            raise ValueError(f"Period missing {key}: {p.get('id')}")
    start, end = dt.date.fromisoformat(p["start"]), dt.date.fromisoformat(p["end"])
    if start > end:
        raise ValueError(f"Start after end: {p['id']}")
    if p["period_type"] not in {"annual", "ytd", "quarter", "instant"}:
        raise ValueError(f"Unknown period_type: {p['period_type']}")
    if p["scope"] in {"",None,"unspecified"} or not p["basis"]:
        raise ValueError(f"Explicit verified entity scope and reporting basis required: {p['id']}")
    for key, field in p["fields"].items():
        if not isinstance(field, dict):
            raise ValueError(f"Field must include value, precision, unit and source: {p['id']}/{key}")
        if field.get("value") is None:
            continue
        dec(field["value"])
        if field.get("unit") != p["currency"]:
            raise ValueError(f"Monetary calculations require common currency unit: {p['id']}/{key} {field.get('unit')}")
        if field.get("precision") is None or dec(field["precision"]) < 0:
            raise ValueError(f"Missing/invalid source precision: {p['id']}/{key}")
        if not field.get("source"):
            raise ValueError(f"Missing source: {p['id']}/{key}")


def calc_period(p):
    f = p["fields"]
    derived = evaluate_derived(f)
    checks = {name:reconciliation(f, definition["coefficients"], definition["reported"], definition["formula"])
        for name,definition in CHECK_FORMULAS.items()}
    if p["scope"] == "parent":
        checks["net_attribution"] = {"value":None,"status":"not_applicable","reason":"Parent single-entity profit has no consolidated attribution split; platform parent columns labelled attributable profit are not group attribution facts."}
    if p["period_type"] == "instant":
        derived = {}
        checks = {"balance":checks["balance"]}
    return {"id":p["id"], "scope":p["scope"], "start":p["start"], "end":p["end"], "basis":p["basis"], "currency":p["currency"], "period_type":p["period_type"], "derived":derived, "checks":checks}


def compatible(current, base, mode):
    reasons = [k+" differs" for k in ("scope", "currency", "basis") if current[k] != base[k]]
    if mode == "difference":
        if current["start"] != base["start"] or current["end"] <= base["end"]:
            reasons.append("YTD intervals must share start and later end must follow earlier end")
        if current["period_type"] not in {"annual", "ytd"} or base["period_type"] != "ytd":
            reasons.append("difference requires cumulative annual/YTD and earlier YTD")
        later, earlier = dt.date.fromisoformat(current["end"]), dt.date.fromisoformat(base["end"])
        if (later.year != earlier.year or later.month-earlier.month != 3
                or later.month not in {6,9,12}
                or later.day != calendar.monthrange(later.year,later.month)[1]
                or earlier.day != calendar.monthrange(earlier.year,earlier.month)[1]):
            reasons.append("single-quarter difference requires adjacent calendar quarter ends")
    else:
        if current["period_type"] != base["period_type"] or current["start"][5:] != base["start"][5:] or current["end"][5:] != base["end"][5:]:
            reasons.append("reporting windows differ")
        if int(current["end"][:4])-int(base["end"][:4]) != 1:
            reasons.append("comparison requires previous-year matching period")
    return reasons


def difference_period(later, earlier, spec):
    reasons = compatible(later, earlier, "difference")
    if reasons:
        return None, {"id":spec["id"], "status":"incomparable", "reasons":reasons}
    fields = {}
    for key in sorted(set(later["fields"]) | set(earlier["fields"])):
        a, b = later["fields"].get(key, {}), earlier["fields"].get(key, {})
        kind_a, kind_b = a.get("kind", "instant" if key in INSTANT_FIELDS else "duration"), b.get("kind", "instant" if key in INSTANT_FIELDS else "duration")
        if key in INSTANT_FIELDS or "instant" in {kind_a, kind_b}:
            continue
        fields[key] = {"value": number(dec(a["value"])-dec(b["value"])) if a.get("value") is not None and b.get("value") is not None else None,
            "precision":number(dec(a["precision"])+dec(b["precision"])) if a.get("precision") is not None and b.get("precision") is not None else None,
            "unit":later["currency"], "kind":"duration", "source":{"formula":f"{later['id']} - {earlier['id']}", "later":a.get("source"), "earlier":b.get("source")}}
    # Cash endpoints are selected snapshots, never subtracted balances.
    for output_key,original,p in (("cash_begin","cash_end",earlier),("cash_end","cash_end",later)):
        if original in p["fields"]:
            original_field=p["fields"][original]
            fields[output_key]={**original_field,"kind":"instant","source":{"formula":f"{output_key} = {p['id']}.{original} (endpoint snapshot)","original_source":original_field.get("source")}}
    start = (dt.date.fromisoformat(earlier["end"])+dt.timedelta(days=1)).isoformat()
    p = {**later, "id":spec["id"], "start":start, "period_type":"quarter", "fields":fields}
    return p, {"id":spec["id"], "status":"calculated", "later":later["id"], "earlier":earlier["id"], "excluded_fields":"instant balances"}


def comparison(current, base, derived_current, derived_base):
    reasons = compatible(current, base, "comparison")
    result = {"current":current["id"], "base":base["id"], "status":"incomparable" if reasons else "calculated", "reasons":reasons}
    if reasons:
        return result
    changes = {}
    a_fields = {**current["fields"], **derived_current["derived"]}
    b_fields = {**base["fields"], **derived_base["derived"]}
    for key in sorted(set(a_fields) & set(b_fields)):
        a, b = a_fields[key], b_fields[key]
        if a.get("value") is None or b.get("value") is None:
            changes[key] = {"absolute_change":None,"growth":None,"status":"missing_inputs"}
        else:
            delta = dec(a["value"])-dec(b["value"])
            growth = delta/dec(b["value"]) if dec(b["value"]) > 0 else None
            changes[key] = {"absolute_change":number(delta), "growth":number(growth), "status":"calculated" if growth is not None else "nonpositive_base"}
    result["changes"] = changes
    return result


def calculate(data):
    if data.get("schema_version") != "financial-facts/v1":
        raise ValueError("Expected schema_version financial-facts/v1")
    periods = {}
    for p in data["periods"]:
        validate_period(p)
        if p["id"] in periods:
            raise ValueError(f"Duplicate period id: {p['id']}")
        periods[p["id"]] = p
    differences = []
    for spec in data.get("differences", []):
        if spec["id"] in periods:
            raise ValueError(f"Difference id already exists: {spec['id']}")
        p, record = difference_period(periods[spec["later"]], periods[spec["earlier"]], spec)
        differences.append(record)
        if p:
            validate_period(p)
            periods[p["id"]] = p
    results = {key:calc_period(p) for key,p in periods.items()}
    comparisons = [comparison(periods[s["current"]], periods[s["base"]], results[s["current"]], results[s["base"]]) for s in data.get("comparisons", [])]
    check_counts = defaultdict(int)
    for p in results.values():
        for c in p["checks"].values():
            check_counts[c["status"]] += 1
    return {"schema_version":"financial-calculations/v1", "company":data.get("company",{}), "periods":list(results.values()), "derived_period_facts":[p for key,p in periods.items() if key not in {x['id'] for x in data['periods']}], "differences":differences, "comparisons":comparisons, "check_counts":dict(check_counts), "interpretation":"A numeric pass proves arithmetic compatibility at source precision only. PDF reading, related-party and financial-subsidiary analysis remain separate completion requirements."}


def reconcile(csv_data, pdf_data, config=None):
    """Compare selected raw canonical inputs with PDF values, retaining both."""
    def index_periods(data):
        if data.get("schema_version") != "financial-facts/v1":
            raise ValueError("Expected schema_version financial-facts/v1")
        result={}
        for p in data["periods"]:
            validate_period(p)
            if p["id"] in result:
                raise ValueError(f"Duplicate period id: {p['id']}")
            result[p["id"]]=p
        return result
    csv_periods, pdf_periods = index_periods(csv_data), index_periods(pdf_data)
    if config is None:
        pairs = [{"csv_period":key,"pdf_period":key} for key in csv_periods]
    else:
        pairs = config["pairs"]
    results, counts = [], defaultdict(int)
    for pair in pairs:
        csv_id, pdf_id = pair["csv_period"], pair["pdf_period"]
        if csv_id not in csv_periods:
            raise ValueError(f"CSV period not found: {csv_id}")
        a, b = csv_periods[csv_id], pdf_periods.get(pdf_id)
        fields = pair.get("fields", list(a["fields"]))
        for key in fields:
            av, bv = a["fields"].get(key,{}), b["fields"].get(key,{}) if b else {}
            item = {"csv_period":csv_id,"pdf_period":pdf_id,"field":key,
                "csv_value":av.get("value"),"pdf_value":bv.get("value"),
                "csv_precision":av.get("precision"),"pdf_precision":bv.get("precision"),
                "csv_source":av.get("source"),"pdf_source":bv.get("source"),
                "basis_note":pair.get("basis_note")}
            differences = [k for k in ("scope","currency","start","end") if b and a[k] != b[k]]
            basis_difference = b and a["basis"] != b["basis"]
            if av.get("value") is None:
                item["status"]="missing_csv"
            elif bv.get("value") is None:
                item["status"]="missing_pdf"
            elif av.get("unit") != bv.get("unit") or differences:
                item["status"]="incomparable"
                item["reasons"] = differences + ([] if av.get("unit")==bv.get("unit") else ["unit"])
            else:
                delta = dec(av["value"])-dec(bv["value"])
                tolerance = (dec(av["precision"])+dec(bv["precision"]))/2
                item.update({"difference":number(delta),"tolerance":number(tolerance),"numeric_status":"within_precision" if abs(delta)<=tolerance else "outside_precision"})
                if basis_difference and not (pair.get("allow_basis_difference") is True and pair.get("basis_note")):
                    item["status"]="basis_requires_review"
                    item["reasons"]=[f"CSV basis {a['basis']} differs from PDF basis {b['basis']}"]
                else:
                    item["status"] = item["numeric_status"]
            counts[item["status"]] += 1
            results.append(item)
    return {"schema_version":"financial-source-reconciliation/v1","company":csv_data.get("company",{}),"results":results,"counts":dict(counts),
        "coverage":{"selected_input_count":len(results),"verified_input_count":counts.get("within_precision",0)},
        "interpretation":"Checks source numeric inputs at their reported precision. Outside-precision, missing and basis differences require PDF investigation and explicit adopted-value provenance. This does not replace comprehensive PDF reading."}


def reconciliation_markdown(data):
    lines=["# CSV 与 PDF 关键输入核对","","金额单位为原币元。差额=CSV-PDF；容差=两份来源量化步长之和的一半。","","| CSV期间 | PDF期间 | 科目 | CSV | PDF | 差额 | 容差 | 状态 |","| --- | --- | --- | ---: | ---: | ---: | ---: | --- |"]
    for r in data["results"]:
        lines.append(f"| {r['csv_period']} | {r['pdf_period']} | {r['field']} | {r.get('csv_value')} | {r.get('pdf_value')} | {r.get('difference','—')} | {r.get('tolerance','—')} | {r['status']} |")
    lines += ["",data["interpretation"],""]
    return "\n".join(lines)


def markdown(data):
    lines = ["# 财务计算与勾稽", "", "金额单位：原币元。比率为小数。精度容差按各输入量化步长的一半累计；数值勾稽通过不等于 PDF 阅读完成。", ""]
    for p in data["periods"]:
        lines += [f"## {p['id']}", "", f"{p['scope']}｜{p['start']}—{p['end']}｜{p['basis']}", "", "| 指标 | 数值 | 状态 |", "| --- | ---: | --- |"]
        lines += [f"| {key} | {r.get('value') if r.get('value') is not None else '—'} | {r['status']} |" for key,r in p["derived"].items()]
        lines += ["", "| 校验 | 复算 | 报表 | 差额 | 容差 | 状态 |", "| --- | ---: | ---: | ---: | ---: | --- |"]
        lines += [f"| {key} | {r.get('value', '—')} | {r.get('reported', '—')} | {r.get('difference', '—')} | {r.get('tolerance', '—')} | {r['status']} |" for key,r in p["checks"].items()]
        lines += [""]
    lines += ["## 解释边界", "", data["interpretation"], ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import", help="Read Lixinger CSV; preserve source identities and precision")
    imp.add_argument("csv", nargs="+")
    imp.add_argument("--output", required=True)
    imp.add_argument("--scope", choices=["consolidated","parent"], help="Explicit fallback when CSV third column contains company name; source scope columns must agree")
    imp.add_argument("--file-scope", action="append", default=[], metavar="GLOB=SCOPE", help="Per-file explicit fallback, repeatable; scope consolidated or parent")
    imp.add_argument("--mapping", help="Explicit field selectors, sign conversions and period basis")
    imp.add_argument("--facts-output", help="Required with --mapping")
    calc = sub.add_parser("calculate", help="Compute explicit canonical financial-facts/v1 input")
    calc.add_argument("--input", required=True)
    calc.add_argument("--output", required=True)
    calc.add_argument("--markdown")
    calc.add_argument("--strict", action="store_true", help="Write results, then exit 2 if any check fails or lacks inputs")
    rec = sub.add_parser("reconcile", help="Verify all mapped key CSV inputs against separately sourced PDF facts")
    rec.add_argument("--csv-facts", required=True)
    rec.add_argument("--pdf-facts", required=True)
    rec.add_argument("--pairs", help="Optional explicit CSV/PDF period pairs and selected fields")
    rec.add_argument("--output", required=True)
    rec.add_argument("--markdown")
    rec.add_argument("--strict", action="store_true", help="Write results, then exit 2 unless every mapped input is within source precision")
    formulas = sub.add_parser("formulas", help="Export the same declarations used by calculate, or guarded scalar-cell Excel formulas")
    formulas.add_argument("--format", choices=("json","excel"), default="json")
    formulas.add_argument("--bindings", help="JSON object mapping canonical input names to a scalar cell string, {sheet,cell}, or null; required for Excel")
    formulas.add_argument("--only", action="append", help="Formula name, repeatable; checks use check.NAME; omitted exports all")
    formulas.add_argument("--output", help="Write JSON output here; omitted prints the export itself")
    args = parser.parse_args(argv)
    try:
        if args.command == "import":
            if bool(args.mapping) != bool(args.facts_output):
                raise ValueError("--mapping and --facts-output must be supplied together")
            for destination in (args.output,args.facts_output):
                if destination and Path(destination).resolve() in {Path(p).resolve() for p in args.csv}:
                    raise ValueError("Output must not overwrite a raw source CSV")
            sources = []
            for path in args.csv:
                overrides = []
                for assignment in args.file_scope:
                    pattern, sep, scope = assignment.rpartition("=")
                    if not sep or scope not in {"consolidated","parent"}:
                        raise ValueError("--file-scope must be GLOB=consolidated or GLOB=parent")
                    if fnmatch.fnmatch(str(path),pattern) or fnmatch.fnmatch(Path(path).name,pattern):
                        overrides.append(scope)
                if len(set(overrides)) > 1:
                    raise ValueError(f"Conflicting file scope overrides: {path}")
                sources.append(import_csv(path,overrides[0] if overrides else args.scope))
            data = {"schema_version":"financial-csv/v1", "sources":sources}
            dump(args.output, data)
            if args.mapping:
                facts = mapped_facts(data, read_json(args.mapping))
                dump(args.facts_output, facts)
            print(json.dumps({"output":args.output, "files":len(data["sources"]), "cells":sum(len(s["records"]) for s in data["sources"]), "facts_output":args.facts_output}, ensure_ascii=False))
        elif args.command == "calculate":
            data = calculate(read_json(args.input))
            dump(args.output, data)
            if args.markdown:
                Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
                Path(args.markdown).write_text(markdown(data), encoding="utf-8")
            print(json.dumps({"output":args.output, "periods":len(data["periods"]), "check_counts":data["check_counts"]}, ensure_ascii=False))
            if args.strict and (not data["periods"] or any(k not in {"pass", "not_applicable"} and v for k,v in data["check_counts"].items())):
                return 2
        elif args.command == "reconcile":
            data = reconcile(read_json(args.csv_facts),read_json(args.pdf_facts),read_json(args.pairs) if args.pairs else None)
            dump(args.output,data)
            if args.markdown:
                Path(args.markdown).parent.mkdir(parents=True,exist_ok=True)
                Path(args.markdown).write_text(reconciliation_markdown(data),encoding="utf-8")
            print(json.dumps({"output":args.output,"counts":data["counts"],"coverage":data["coverage"]},ensure_ascii=False))
            if args.strict and (not data["results"] or any(k != "within_precision" and v for k,v in data["counts"].items())):
                return 2
        else:
            if args.format == "excel" and not args.bindings:
                raise ValueError("--format excel requires --bindings")
            if args.format == "json" and args.bindings:
                raise ValueError("--bindings applies only to --format excel")
            if args.output and args.bindings and Path(args.output).resolve() == Path(args.bindings).resolve():
                raise ValueError("Formula output must not overwrite input bindings")
            data = excel_formulas(read_json(args.bindings),args.only) if args.format == "excel" else formula_catalog(args.only)
            if args.output:
                dump(args.output,data)
                print(json.dumps({"output":args.output,"catalog_sha256":data["catalog_sha256"],
                    "formulas":len(data.get("definitions",data.get("formulas",{})))},ensure_ascii=False))
            else:
                print(json.dumps(data,ensure_ascii=False,indent=2))
    except (ValueError, KeyError, OSError, csv.Error, InvalidOperation) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
