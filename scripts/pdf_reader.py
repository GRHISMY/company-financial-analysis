#!/usr/bin/env python3
"""Bounded, source-bound PDF text delivery. A receipt records acknowledgment, not understanding."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import source_archive as archive

STATE_VERSION = "pdf-reader/v2"
CHUNK_VERSION = "pdf-reader-chunk/v2"
DEFAULT_BUDGET = 8000


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


def snapshot(case, source_id):
    case = Path(case).resolve()
    if not isinstance(source_id, str) or Path(source_id).name != source_id or source_id in {".", ".."}:
        raise ValueError("Invalid source_id")
    manifest = archive.require_case(case)
    selected = [s for s in manifest["sources"] if s.get("source_id") == source_id and s.get("kind") == "pdf"]
    if len(selected) != 1:
        raise ValueError("Expected one archived PDF source_id")
    source = selected[0]
    original = (case / source["path"]).resolve()
    if not original.is_relative_to(case) or not original.is_file() or archive.sha256(original) != source["sha256"]:
        raise ValueError("Archived source missing, outside case, or SHA256 changed")
    directory = case / "workpapers/pdf" / source_id
    if not directory.resolve().is_relative_to(case):
        raise ValueError("Extracted-page directory is outside case")
    extracted_path = directory / "pages.json"
    extracted = archive.read_json(extracted_path)
    if extracted.get("source_id") != source_id or extracted.get("source_sha256") != source["sha256"]:
        raise ValueError("Extracted pages do not match archived source identity")
    pages = extracted.get("pages", [])
    if len(pages) != source["validation"]["pages"] or any(p.get("pdf_page") != i or not isinstance(p.get("text"), str) for i, p in enumerate(pages, 1)):
        raise ValueError("Extracted pages have invalid count, ordering, or text")
    # Identity follows operative content, not JSON formatting or extraction timestamps.
    content = {"source_id": source_id, "source_sha256": source["sha256"], "pages": [{"pdf_page": p["pdf_page"], "text": p["text"], "needs_visual_or_ocr": bool(p.get("needs_visual_or_ocr")) or len(p["text"].strip()) < 30} for p in pages]}
    return {"case": case, "source": source, "pages": pages, "pages_sha256": digest(content), "visual_required_pages": [p["pdf_page"] for p in content["pages"] if p["needs_visual_or_ocr"]], "state_path": directory / "reader-state.json"}


def new_state(snap):
    return {"schema_version": STATE_VERSION, "source_id": snap["source"]["source_id"], "source_sha256": snap["source"]["sha256"], "pages_sha256": snap["pages_sha256"], "acknowledged": [], "pending": None}


def advance(snap, cursor, segments):
    """Validate a contiguous delivery range; zero-text pages need an explicit segment."""
    cursor = dict(cursor)
    for segment in segments:
        page, start, end = segment["pdf_page"], segment["start_offset"], segment["end_offset"]
        if cursor != {"pdf_page": page, "offset": start} or page > len(snap["pages"]):
            raise ValueError("Reader progress is noncontiguous or invalid")
        length = len(snap["pages"][page - 1]["text"])
        if type(start) is not int or type(end) is not int or not (0 <= start <= end <= length) or (start == end and length != 0):
            raise ValueError("Reader progress offsets are invalid")
        cursor = {"pdf_page": page + 1, "offset": 0} if end == length else {"pdf_page": page, "offset": end}
    return cursor


def validate_segments(snap, segments):
    """Reconstruct delivered text from original offsets and exact omitted lines."""
    if not isinstance(segments, list) or not segments:
        raise ValueError("Receipt requires nonempty delivered segments")
    for segment in segments:
        page = segment.get("pdf_page")
        if type(page) is not int or not 1 <= page <= len(snap["pages"]):
            raise ValueError("Receipt page is invalid")
        original = snap["pages"][page - 1]["text"]
        start, end = segment.get("start_offset"), segment.get("end_offset")
        if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(original):
            raise ValueError("Receipt offsets are invalid")
        if segment.get("page_characters") != len(original) or segment.get("page_complete") is not (end == len(original)):
            raise ValueError("Receipt page completeness metadata changed")
        omissions = segment.get("omissions")
        if not isinstance(omissions, list):
            raise ValueError("Receipt omission evidence is missing")
        visible, cursor = [], start
        for omission in omissions:
            left, right = omission.get("start_offset"), omission.get("end_offset")
            if type(left) is not int or type(right) is not int or not cursor <= left < right <= end:
                raise ValueError("Receipt omissions overlap or have invalid offsets")
            line = original[left:right]
            if (left and original[left - 1] != "\n") or (right != len(original) and original[right - 1] != "\n"):
                raise ValueError("Receipt can omit only complete lines")
            if len(line.rstrip("\r\n")) < 40 or any(ch.isnumeric() for ch in line):
                raise ValueError("Receipt omitted short or numeric text")
            if omission.get("line_sha256") != hashlib.sha256(line.encode()).hexdigest() or omission.get("target_line_number") != original.count("\n", 0, left) + 1:
                raise ValueError("Receipt omission no longer matches original text")
            visible.append(original[cursor:left])
            cursor = right
        visible.append(original[cursor:end])
        if segment.get("text") != "".join(visible):
            raise ValueError("Receipt delivered text does not match original offsets")


def load_state(snap):
    state = archive.read_json(snap["state_path"]) if snap["state_path"].exists() else new_state(snap)
    for key in ("schema_version", "source_id", "source_sha256", "pages_sha256"):
        if state.get(key) != new_state(snap)[key]:
            raise ValueError("Reader progress is stale: source/extracted pages changed; preserve it and start a new source/version")
    cursor = {"pdf_page": 1, "offset": 0}
    seen = set()
    for receipt in state.get("acknowledged", []):
        if receipt.get("chunk_id") in seen or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("receipt_sha256", ""))):
            raise ValueError("Reader acknowledgment record is malformed")
        seen.add(receipt["chunk_id"])
        validate_segments(snap, receipt["segments"])
        expected = payload(snap, receipt["segments"])
        if receipt["chunk_id"] != expected["chunk_id"] or receipt["receipt_sha256"] != expected["receipt_sha256"]:
            raise ValueError("Acknowledged receipt content checksum changed")
        dependencies = [x for s in receipt["segments"] for x in s["omissions"]]
        if receipt.get("dedup_dependencies") != dependencies:
            raise ValueError("Acknowledged receipt dependencies changed")
        cursor = advance(snap, cursor, receipt["segments"])
    if state.get("pending"):
        pending = state["pending"]
        if pending.get("source_sha256") != snap["source"]["sha256"] or pending.get("pages_sha256") != snap["pages_sha256"] or pending.get("source_id") != snap["source"]["source_id"]:
            raise ValueError("Pending delivery is stale")
        if seal({k: v for k, v in pending.items() if k not in {"chunk_id", "receipt_sha256"}}) != pending:
            raise ValueError("Pending receipt content checksum changed")
        validate_segments(snap, pending["segments"])
        if payload(snap, pending["segments"]) != pending:
            raise ValueError("Pending delivery no longer matches original text")
        next_cursor = advance(snap, cursor, pending["segments"])
        if next_cursor != pending["next_cursor"]:
            raise ValueError("Pending receipt cursor is invalid")
    return state, cursor


def verify_dependencies(case, dependencies, stack=(), verified=None):
    verified = set() if verified is None else verified
    for dependency in dependencies:
        identity = (dependency["source_id"], dependency["chunk_id"])
        key = identity + (dependency["receipt_sha256"], dependency["source_sha256"], dependency["pages_sha256"])
        if key in verified:
            continue
        if identity in stack:
            raise ValueError("Cyclic deduplication receipt dependency")
        snap = snapshot(case, dependency["source_id"])
        state, _ = load_state(snap)
        if snap["source"]["sha256"] != dependency["source_sha256"] or snap["pages_sha256"] != dependency["pages_sha256"]:
            raise ValueError("Deduplication evidence source changed")
        matching = next((x for x in state["acknowledged"] if x.get("chunk_id") == dependency["chunk_id"] and x.get("receipt_sha256") == dependency["receipt_sha256"]), None)
        if not matching:
            raise ValueError("Deduplication base is no longer acknowledged")
        page, line_number = dependency.get("pdf_page"), dependency.get("line_number")
        if type(page) is not int or not 1 <= page <= len(snap["pages"]) or type(line_number) is not int or line_number < 1:
            raise ValueError("Deduplication base page/line is invalid")
        lines = snap["pages"][page - 1]["text"].splitlines(keepends=True)
        if line_number > len(lines):
            raise ValueError("Deduplication base line is absent")
        line = lines[line_number - 1]
        start = sum(map(len, lines[:line_number - 1]))
        end = start + len(line)
        if dependency.get("line_sha256") != hashlib.sha256(line.encode()).hexdigest() or not any(s["pdf_page"] == page and s["start_offset"] <= start and end <= s["end_offset"] for s in matching["segments"]):
            raise ValueError("Deduplication line was not fully covered by its acknowledged receipt")
        verify_dependencies(case, matching.get("dedup_dependencies", []), stack + (identity,), verified)
        verified.add(key)


def acknowledged_lines(case, source_ids):
    """Only complete, previously acknowledged exact lines without digits are reusable."""
    result = {}
    for source_id in source_ids:
        snap = snapshot(case, source_id)
        state, _ = load_state(snap)
        if not state["acknowledged"]:
            raise ValueError(f"Deduplication base has no acknowledged text: {source_id}")
        for receipt in state["acknowledged"]:
            verify_dependencies(case, receipt.get("dedup_dependencies", []))
            dependency = {"source_id": source_id, "source_sha256": snap["source"]["sha256"], "pages_sha256": snap["pages_sha256"], "chunk_id": receipt["chunk_id"], "receipt_sha256": receipt["receipt_sha256"]}
            for segment in receipt["segments"]:
                text = snap["pages"][segment["pdf_page"] - 1]["text"]
                offset = 0
                for line_number, line in enumerate(text.splitlines(keepends=True), 1):
                    end = offset + len(line)
                    if segment["start_offset"] <= offset and end <= segment["end_offset"] and len(line.rstrip("\r\n")) >= 40 and not any(ch.isnumeric() for ch in line):
                        result.setdefault(line, {**dependency, "pdf_page": segment["pdf_page"], "line_number": line_number, "line_sha256": hashlib.sha256(line.encode()).hexdigest()})
                    offset = end
    return result


def seal(core):
    content_hash = digest(core)
    return {**core, "chunk_id": "chunk-" + content_hash[:24], "receipt_sha256": content_hash}


def payload(snap, segments):
    included = {s["pdf_page"] for s in segments}
    core = {"schema_version": CHUNK_VERSION, "source_id": snap["source"]["source_id"], "source_sha256": snap["source"]["sha256"], "pages_sha256": snap["pages_sha256"], "segments": segments, "visual_required_pages": [p for p in snap["visual_required_pages"] if p in included], "next_cursor": advance(snap, {"pdf_page": segments[0]["pdf_page"], "offset": segments[0]["start_offset"]}, segments)}
    return seal(core)


def add_text(segments, snap, page, start, end, text, omission=None):
    candidate = json.loads(encoded(segments))
    if not candidate or candidate[-1]["pdf_page"] != page:
        candidate.append({"pdf_page": page, "start_offset": start, "end_offset": end, "page_characters": len(snap["pages"][page - 1]["text"]), "page_complete": end == len(snap["pages"][page - 1]["text"]), "text": text, "omissions": []})
    else:
        candidate[-1]["end_offset"] = end
        candidate[-1]["page_complete"] = end == candidate[-1]["page_characters"]
        candidate[-1]["text"] += text
    if omission:
        candidate[-1]["omissions"].append(omission)
    return candidate


def next_chunk(case, source_id, max_chars=DEFAULT_BUDGET, dedup_sources=None, rechunk_pending=False, ack_chunk_id=None, ack_receipt_sha256=None):
    case = Path(case).resolve()
    if max_chars < 700:
        raise ValueError("max_chars must be at least 700 for the receipt envelope")
    if bool(ack_chunk_id) != bool(ack_receipt_sha256):
        raise ValueError("--ack-chunk-id and --ack-receipt-sha256 must be supplied together")
    if ack_chunk_id:
        # The caller explicitly acknowledges only the previous, fully observed output.
        acknowledge(case, source_id, ack_chunk_id, ack_receipt_sha256)
    with archive.manifest_lock(case):
        snap = snapshot(case, source_id)
        state, cursor = load_state(snap)
        if state["pending"]:
            verify_dependencies(case, [x for s in state["pending"]["segments"] for x in s.get("omissions", [])])
            if not rechunk_pending:
                if len(encoded(state["pending"])) + 1 > max_chars:
                    raise ValueError("Unacknowledged chunk needs its original/larger budget; use --rechunk-pending to split again from the same unacknowledged cursor")
                return state["pending"]
            state.setdefault("superseded_pending", []).append({"chunk_id": state["pending"]["chunk_id"], "receipt_sha256": state["pending"]["receipt_sha256"], "segments": [{k: s[k] for k in ("pdf_page", "start_offset", "end_offset")} for s in state["pending"]["segments"]]})
            state["pending"] = None
        if cursor["pdf_page"] > len(snap["pages"]):
            return {"source_id": source_id, "delivery_complete": True, "visual_required_pages": snap["visual_required_pages"], "visual_verification": "separate_from_text_receipts", "notice": "All text acknowledged; semantic reading and original-page visual/OCR checks remain separate."}
        bases = list(dict.fromkeys(dedup_sources or []))
        if source_id in bases:
            raise ValueError("Deduplication requires a different acknowledged source")
        reusable = acknowledged_lines(case, bases)
        segments = []
        page, offset = cursor["pdf_page"], cursor["offset"]
        while page <= len(snap["pages"]):
            text = snap["pages"][page - 1]["text"]
            if not text:
                candidate = add_text(segments, snap, page, 0, 0, "")
                if len(encoded(payload(snap, candidate))) + 1 > max_chars:
                    break
                segments = candidate
                page, offset = page + 1, 0
                continue
            end = text.find("\n", offset)
            end = len(text) if end < 0 else end + 1
            line = text[offset:end]
            # Never omit a partial line or any numeric content.
            complete_line = offset == 0 or text[offset - 1] == "\n"
            omission = reusable.get(line) if complete_line else None
            if omission:
                omission = {**omission, "target_line_number": text.count("\n", 0, offset) + 1, "start_offset": offset, "end_offset": end}
                candidate = add_text(segments, snap, page, offset, end, "", omission)
                if len(encoded(payload(snap, candidate))) + 1 <= max_chars:
                    segments = candidate
                    page, offset = (page + 1, 0) if end == len(text) else (page, end)
                    continue
            # Raw fallback also covers lines whose omission metadata costs more than the text.
            candidate = add_text(segments, snap, page, offset, end, line)
            if len(encoded(payload(snap, candidate))) + 1 <= max_chars:
                segments = candidate
                page, offset = (page + 1, 0) if end == len(text) else (page, end)
                continue
            low, high, best = 1, len(line), 0
            while low <= high:
                middle = (low + high) // 2
                candidate = add_text(segments, snap, page, offset, offset + middle, line[:middle])
                if len(encoded(payload(snap, candidate))) + 1 <= max_chars:
                    best, low = middle, middle + 1
                else:
                    high = middle - 1
            if best:
                segments = add_text(segments, snap, page, offset, offset + best, line[:best])
            break
        if not segments:
            raise ValueError("Budget cannot fit a text fragment and receipt; increase max_chars")
        result = payload(snap, segments)
        assert len(encoded(result)) + 1 <= max_chars
        state["pending"] = result
        archive.atomic_json(snap["state_path"], state)
        return result


def acknowledge(case, source_id, chunk_id, receipt_sha256):
    case = Path(case).resolve()
    with archive.manifest_lock(case):
        snap = snapshot(case, source_id)
        state, _ = load_state(snap)
        if any(x["chunk_id"] == chunk_id and x["receipt_sha256"] == receipt_sha256 for x in state["acknowledged"]):
            return {"source_id": source_id, "chunk_id": chunk_id, "status": "already_acknowledged"}
        pending = state.get("pending")
        if not pending:
            raise ValueError("No matching pending receipt")
        if pending["chunk_id"] != chunk_id or pending["receipt_sha256"] != receipt_sha256:
            raise ValueError("Chunk ID or receipt SHA256 does not match the complete pending output")
        dependencies = [x for s in pending["segments"] for x in s.get("omissions", [])]
        verify_dependencies(case, dependencies)
        state["acknowledged"].append({"chunk_id": chunk_id, "receipt_sha256": receipt_sha256, "acknowledged_at": archive.now(), "segments": pending["segments"], "dedup_dependencies": dependencies})
        state["pending"] = None
        archive.atomic_json(snap["state_path"], state)
        return {"source_id": source_id, "chunk_id": chunk_id, "status": "acknowledged", "next_cursor": pending["next_cursor"]}


def ranges(numbers):
    result = []
    for n in sorted(numbers):
        if result and n == result[-1][1] + 1:
            result[-1][1] = n
        else:
            result.append([n, n])
    return result


def source_status(case, source_id):
    snap = snapshot(case, source_id)
    state, cursor = load_state(snap)
    dependencies = [d for x in state["acknowledged"] for d in x.get("dedup_dependencies", [])]
    verify_dependencies(case, dependencies)
    completed = list(range(1, min(cursor["pdf_page"], len(snap["pages"]) + 1)))
    return {"source_id": source_id, "tracking": "receipts" if snap["state_path"].exists() else "not_started", "pages": len(snap["pages"]), "completed_page_ranges": ranges(completed), "next_cursor": cursor, "pending_chunk_id": (state.get("pending") or {}).get("chunk_id"), "acknowledged_chunks": len(state["acknowledged"]), "delivery_complete": cursor["pdf_page"] > len(snap["pages"]), "visual_required_pages": snap["visual_required_pages"], "visual_verification": "separate_from_text_receipts", "notice": "Acknowledged text delivery does not certify comprehension or replace visual/OCR checks."}


def reset_progress(case, source_id, reason):
    """Restart extraction-bound delivery while retaining the previous receipt file."""
    case = Path(case).resolve()
    if not isinstance(reason, str) or len(reason.strip()) < 8:
        raise ValueError("Reset requires a substantive reason (at least eight characters)")
    with archive.manifest_lock(case):
        snap = snapshot(case, source_id)
        if not snap["state_path"].exists():
            return {"source_id": source_id, "status": "not_started"}
        old_hash = archive.sha256(snap["state_path"])
        history = snap["state_path"].parent / "reader-history" / (old_hash + ".json")
        history.parent.mkdir(exist_ok=True)
        if not history.exists():
            shutil.copyfile(snap["state_path"], history)
        state = {**new_state(snap), "reset_from": str(history.relative_to(case)), "reset_reason": reason, "reset_at": archive.now()}
        archive.atomic_json(snap["state_path"], state)
        return {"source_id": source_id, "status": "restarted_at_page_1", "previous_progress": str(history.relative_to(case)), "reason": reason}


def status_report(case, source_id=None):
    case = Path(case).resolve()
    data = archive.require_case(case)
    sources = archive.select_pdfs(data, source_id) if source_id else [s for s in data["sources"] if s.get("kind") == "pdf"]
    reports = []
    for source in sources:
        try:
            reports.append(source_status(case, source["source_id"]))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            reports.append({"source_id": source["source_id"], "tracking": "stale_or_unavailable", "delivery_complete": False, "error": str(exc)})
    return {"sources": reports, "delivery_complete": bool(reports) and all(x.get("delivery_complete") for x in reports)}


def search(case, source_id, query, limit=20, context=120, max_chars=DEFAULT_BUDGET):
    if not query or limit < 1 or context < 0 or max_chars < 500:
        raise ValueError("Search requires nonempty query and positive limits/budget")
    snap = snapshot(case, source_id)
    result = {"source_id": source_id, "query": query, "hits": [], "truncated": False, "notice": "Navigation only; search does not acknowledge reading."}
    if len(encoded(result)) + 1 > max_chars:
        raise ValueError("Search envelope exceeds budget; shorten query or increase budget")
    for page in snap["pages"]:
        start = 0
        while True:
            position = page["text"].find(query, start)
            if position < 0:
                break
            hit = {"pdf_page": page["pdf_page"], "offset": position, "excerpt": page["text"][max(0, position-context):position+len(query)+context]}
            candidate = {**result, "hits": result["hits"] + [hit]}
            if len(result["hits"]) >= limit or len(encoded(candidate)) + 1 > max_chars:
                result["truncated"] = True
                return result
            result["hits"].append(hit)
            start = position + len(query)
    if len(encoded(result)) + 1 > max_chars:
        raise ValueError("Search envelope exceeds budget; shorten query or increase budget")
    return result


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    for name in ("next", "ack", "status", "search", "reset"):
        p = sub.add_parser(name)
        p.add_argument("--case", type=Path, required=True)
        p.add_argument("--source-id", required=name != "status")
        if name in {"next", "search"}:
            p.add_argument("--max-chars", type=int, default=DEFAULT_BUDGET, help="Full compact JSON + newline character ceiling, not token count")
        if name == "next":
            p.add_argument("--dedup-source", action="append", default=[], help="Archived PDF with acknowledged receipts; exact nonnumeric full lines only")
            p.add_argument("--rechunk-pending", action="store_true", help="Replace unacknowledged delivery with a smaller chunk at the same cursor; retain superseded receipt identity")
            p.add_argument("--ack-chunk-id", help="Explicitly acknowledge a previously delivered chunk before requesting the next")
            p.add_argument("--ack-receipt-sha256", help="Previous complete chunk footer; required together with --ack-chunk-id")
        elif name == "ack":
            p.add_argument("--chunk-id", required=True)
            p.add_argument("--receipt-sha256", required=True, help="Footer SHA256 from complete delivered JSON")
        elif name == "search":
            p.add_argument("--query", required=True)
            p.add_argument("--limit", type=int, default=20)
            p.add_argument("--context", type=int, default=120)
        elif name == "reset":
            p.add_argument("--reason", required=True, help="Preserve old progress file and restart after extraction changes; never changes source originals")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "next": result = next_chunk(args.case, args.source_id, args.max_chars, args.dedup_source, args.rechunk_pending, args.ack_chunk_id, args.ack_receipt_sha256)
        elif args.command == "ack": result = acknowledge(args.case, args.source_id, args.chunk_id, args.receipt_sha256)
        elif args.command == "status": result = status_report(args.case, args.source_id)
        elif args.command == "reset": result = reset_progress(args.case, args.source_id, args.reason)
        else: result = search(args.case, args.source_id, args.query, args.limit, args.context, args.max_chars)
        print(encoded(result))
        return 2 if args.command == "status" and any(x.get("error") for x in result["sources"]) else 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(encoded({"error": str(exc)}), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
