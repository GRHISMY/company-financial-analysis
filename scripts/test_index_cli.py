#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Independent CLI checks for searchable, source-bound financial evidence."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import source_archive as archive


CLI = Path(__file__).resolve().parent / "cfa.py"


class IndexCLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.db = self.root / "local cache" / "evidence.sqlite3"

    def command(self, *args, expected=0):
        process = subprocess.run(
            [sys.executable, str(CLI), "index", *map(str, args), "--db", str(self.db)],
            text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(process.returncode, expected, (args, process.stdout, process.stderr))
        # The complete CLI stream must remain one parseable JSON document.
        stream = process.stdout or process.stderr
        result = json.loads(stream)
        return result, stream

    @staticmethod
    def snapshot(directory):
        return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in directory.rglob("*") if p.is_file()}

    def make_case(self, name="格力电器", code="000651", years=(2020,), *, notes=False,
                  text=None, visual=False, folder=None):
        case = self.root / (folder or (name + " 公司资料 with spaces"))
        case.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 1, "company": name, "code": code,
            "requested_period": {"start": f"{min(years)}-01-01", "end": "2026-11-07"},
            "sources": [],
        }
        for year in years:
            raw = case / "raw/pdf" / f"{year}.pdf"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_bytes(f"controlled synthetic PDF bytes {name}:{code}:{year}".encode())
            sha = archive.sha256(raw)
            sid = "pdf-" + sha[:16]
            page_text = text or (f"{name} {year}年度报告。\n关联交易：财务公司存款余额待结合主体核准。\n"
                                 "现金流量净额为 -1,234.50 元；本期分红金额和付款日期分别核对。\n")
            pages = [
                {"pdf_page": 1, "text": page_text, "characters": len(page_text), "needs_visual_or_ocr": False},
                {"pdf_page": 2, "text": "合并与母公司列示范围不同，受限资金和股利支付需要独立阅读附注。", "characters": 32,
                 "needs_visual_or_ocr": visual},
            ]
            source = {"source_id": sid, "company": name, "code": code, "period": str(year),
                      "kind": "pdf", "report_type": "annual", "title": f"{name}{year}年度报告",
                      "path": str(raw.relative_to(case)), "sha256": sha, "validation": {"pages": 2}}
            manifest["sources"].append(source)
            directory = case / "workpapers/pdf" / sid
            archive.atomic_json(directory / "pages.json", {
                "schema_version": 1, "source_id": sid, "source_sha256": sha,
                "extracted_at": "2026-11-07T00:00:00Z", "method": "controlled fixture",
                "pages": pages,
            })
            index = {"source_id": sid, "topics": {
                key: {"label": label, "keywords": list(words), "pages": [1], "matches": []}
                for key, (label, words) in archive.TOPICS.items()}}
            archive.atomic_json(directory / "index.json", index)
            note = archive.reading_template(source, 2, index)
            if notes:
                note.update(reader="controlled test", reviewed_at="2026-11-07T00:00:00Z")
                note["identity_check"].update(status="read", pages=[1],
                    finding="Controlled fixture identity checked against source metadata.",
                    checked_company=name, checked_code=code, checked_period=str(year))
                for section in note["sections"].values():
                    section.update(status="read", pages=[1], finding="Controlled fixture topic review.",
                                   year_over_year_change="Controlled fixture has no inferred comparison.",
                                   financial_impact="Keep entity and payment basis explicit.", evidence_gaps=[])
                note["whole_report_review"].update(status="read", reviewed_page_ranges=[[1, 2]],
                    unreviewed_page_ranges=[], finding="Both controlled fixture pages reviewed.")
                note["material_findings"] = [{"pages": [2], "finding": "凤凰线索仅存在于研究笔记，不能冒充PDF原文。",
                                               "financial_impact": "回到原页核对，不能自动认定该推断成立。"}]
                self.assertTrue(archive.check_reading(source, note)["record_complete"])
            archive.atomic_json(case / "reading" / (sid + ".json"), note)
        archive.atomic_json(case / "manifest.json", manifest)
        return case

    def sync(self, *cases):
        args = ["sync"]
        for case in cases:
            args.extend(["--case", case])
        return self.command(*args)[0]

    @staticmethod
    def source(case, year):
        manifest = archive.read_json(case / "manifest.json")
        return next(item for item in manifest["sources"] if item["period"] == str(year))

    @staticmethod
    def original_hits(result):
        return [hit for hit in result["hits"] if hit["kind"] == "original"]

    def assert_source_slice(self, case, hit):
        source = next(s for s in archive.read_json(case / "manifest.json")["sources"]
                      if s["source_id"] == hit["source_id"])
        self.assertEqual(hit["source_sha256"], source["sha256"])
        self.assertEqual(Path(hit["pdf_path"]), (case / source["path"]).resolve())
        text = archive.read_json(case / "workpapers/pdf" / source["source_id"] / "pages.json")["pages"][hit["pages"][0] - 1]["text"]
        self.assertEqual(hit["text"], text[hit["start_offset"]:hit["end_offset"]])

    def test_missing_database_queries_create_no_cache_or_case_files(self):
        case = self.make_case()
        before = self.snapshot(case)
        for args in (("status",), ("coverage",), ("search", "--query", "分红"),
                     ("fetch", "--hit-id", "not-a-real-hit")):
            with self.subTest(command=args[0]):
                result, _ = self.command(*args, expected=2)
                self.assertEqual(result["status"], "missing")
                self.assertFalse(self.db.parent.exists())
        self.assertEqual(self.snapshot(case), before)

    def test_chinese_short_terms_exact_amount_and_filtering_return_real_source_slices(self):
        gree = self.make_case(years=(2020, 2021))
        moutai = self.make_case("贵州茅台", "600519", (2020,))
        self.sync(gree, moutai)
        for query in ("分红", "财务公司", "-1,234.50"):
            with self.subTest(query=query):
                result, _ = self.command("search", "--query", query, "--code", "000651",
                    "--start-year", 2020, "--end-year", 2020)
                hits = self.original_hits(result)
                self.assertTrue(hits)
                for hit in result["hits"]:
                    self.assertEqual(hit["code"], "000651")
                    self.assertEqual(str(hit["year"]), "2020")
                for hit in hits:
                    self.assert_source_slice(gree, hit)

    def test_fetch_expands_the_cited_page_without_claiming_reading(self):
        case = self.make_case()
        before = self.snapshot(case)
        self.sync(case)
        result, _ = self.command("search", "--query", "财务公司")
        hit = self.original_hits(result)[0]
        fetched, _ = self.command("fetch", "--hit-id", hit["hit_id"])
        self.assertTrue(fetched["segments"])
        source = self.source(case, 2020)
        texts = archive.read_json(case / "workpapers/pdf" / source["source_id"] / "pages.json")["pages"]
        for segment in fetched["segments"]:
            original = texts[segment["pdf_page"] - 1]["text"]
            self.assertEqual(segment["text"], original[segment["start_offset"]:segment["end_offset"]])
        self.assertEqual(self.snapshot(case), before)

    def test_signed_amount_query_keeps_minus_sign_and_decimal_identity(self):
        negative = self.make_case(text="本期现金流量净额 -1,234.50 元。金额符号和精度必须保留，主体范围仍须核准。")
        positive = self.make_case("贵州茅台", "600519", (2020,),
            text="本期现金流量净额 1,234.50 元。金额符号和精度必须保留，主体范围仍须核准。")
        self.sync(negative, positive)
        result, _ = self.command("search", "--query", "-1,234.50")
        hits = self.original_hits(result)
        self.assertTrue(hits)
        self.assertEqual({hit["code"] for hit in hits}, {"000651"})
        self.assertTrue(all("-1,234.50" in hit["text"] for hit in hits))

    def test_generated_notes_remain_distinct_and_unread_templates_do_not_fake_findings(self):
        read_case = self.make_case(notes=True)
        unread_case = self.make_case("贵州茅台", "600519", (2020,))
        note_path = unread_case / "reading" / (self.source(unread_case, 2020)["source_id"] + ".json")
        note = archive.read_json(note_path)
        note["sections"]["business"].update(finding="朱雀线索仍是未读占位，不能被索引为已核准发现。", pages=[1])
        archive.atomic_json(note_path, note)
        self.sync(read_case, unread_case)
        result, _ = self.command("search", "--query", "凤凰线索")
        self.assertTrue(result["hits"])
        self.assertTrue(all(hit["kind"] != "original" for hit in result["hits"]))
        for hit in result["hits"]:
            self.assertIsNone(hit["start_offset"])
            self.assertIsNone(hit["end_offset"])
            self.assertTrue(hit["pages"])
        result, _ = self.command("search", "--query", "朱雀线索")
        self.assertFalse(result["hits"])
        result, _ = self.command("search", "--query", "分红", "--code", "600519")
        self.assertTrue(self.original_hits(result))
        self.assertFalse(result["coverage"]["groups"][0]["reading_complete"])

    def test_cross_year_recall_and_requested_coverage_do_not_hide_missing_year(self):
        gree = self.make_case(years=(2020, 2021), notes=True, visual=True)
        moutai = self.make_case("贵州茅台", "600519", (2020, 2021), notes=True)
        self.sync(gree, moutai)
        result, _ = self.command("search", "--query", "财务公司", "--start-year", 2020,
            "--end-year", 2022, "--limit", 4, "--max-chars", 20000)
        groups = {(hit["code"], str(hit["year"])) for hit in result["hits"]}
        self.assertEqual(groups, {("000651", "2020"), ("000651", "2021"),
                                  ("600519", "2020"), ("600519", "2021")})
        coverage = result["coverage"]["groups"]
        missing = [g for g in coverage if str(g["year"]) == "2022"]
        self.assertEqual(len(missing), 2)
        self.assertTrue(all(g["state"] == "not_archived" for g in missing))
        self.assertTrue(any(g["visual_required_pages"] for g in coverage if g["code"] == "000651" and str(g["year"]) == "2020"))

    def test_idempotent_sync_and_all_read_queries_preserve_case_and_database(self):
        case = self.make_case(notes=True)
        before = self.snapshot(case)
        first = self.sync(case)
        again = self.sync(case)
        self.assertEqual(first["totals"], again["totals"])
        self.assertEqual(again["cases"][0]["status"], "unchanged")
        db_before = self.snapshot(self.db.parent)
        result, _ = self.command("search", "--query", "分红")
        self.command("fetch", "--hit-id", self.original_hits(result)[0]["hit_id"])
        self.command("status")
        self.command("coverage", "--code", "000651", "--start-year", 2020, "--end-year", 2021)
        self.assertEqual(self.snapshot(case), before)
        self.assertEqual(self.snapshot(self.db.parent), db_before)

    def test_extraction_or_note_change_requires_resync_and_removes_old_content(self):
        case = self.make_case(notes=True)
        self.sync(case)
        source = self.source(case, 2020)
        page_path = case / "workpapers/pdf" / source["source_id"] / "pages.json"
        pages = archive.read_json(page_path)
        pages["pages"][0]["text"] = "更新版本的独有玄武线索，需要当前提取版本才能正确查询。"
        archive.atomic_json(page_path, pages)
        result, _ = self.command("search", "--query", "财务公司", expected=2)
        self.assertEqual(result["status"], "stale")
        self.assertFalse(result.get("hits"))
        self.sync(case)
        result, _ = self.command("search", "--query", "玄武线索")
        self.assertTrue(self.original_hits(result))
        result, _ = self.command("search", "--query", "财务公司")
        self.assertFalse(self.original_hits(result))
        note_path = case / "reading" / (source["source_id"] + ".json")
        note = archive.read_json(note_path)
        note["material_findings"][0]["finding"] = "白虎线索取代旧研究笔记，仍需原页核准。"
        archive.atomic_json(note_path, note)
        result, _ = self.command("search", "--query", "凤凰线索", expected=2)
        self.assertEqual(result["status"], "stale")
        self.sync(case)
        result, _ = self.command("search", "--query", "凤凰线索")
        self.assertFalse(result["hits"])
        result, _ = self.command("search", "--query", "白虎线索")
        self.assertTrue(result["hits"])

    def test_repeated_extraction_metadata_does_not_invalidate_unchanged_text(self):
        case = self.make_case()
        self.sync(case)
        source = self.source(case, 2020)
        path = case / "workpapers/pdf" / source["source_id"] / "pages.json"
        pages = archive.read_json(path)
        pages["extracted_at"] = "2026-11-08T10:00:00Z"
        path.write_text(json.dumps(pages, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        result, _ = self.command("search", "--query", "分红")
        self.assertTrue(self.original_hits(result))
        result = self.sync(case)
        self.assertEqual(result["cases"][0]["status"], "unchanged")

    def test_corrupt_original_fails_sync_atomically_and_does_not_lose_prior_index(self):
        first = self.make_case()
        self.sync(first)
        before = self.snapshot(self.db.parent)
        second = self.make_case("贵州茅台", "600519", (2021,))
        source = self.source(second, 2021)
        (second / source["path"]).write_bytes(b"corrupted after manifest was recorded")
        self.command("sync", "--case", first, "--case", second, expected=1)
        self.assertEqual(self.snapshot(self.db.parent), before)
        result, _ = self.command("search", "--query", "分红", "--code", "000651")
        self.assertTrue(self.original_hits(result))

    def test_original_mutation_with_unchanged_manifest_never_returns_cached_evidence(self):
        case = self.make_case()
        self.sync(case)
        source = self.source(case, 2020)
        original = case / source["path"]
        before = original.read_bytes()
        original.write_bytes(b"external original replacement")
        result, _ = self.command("search", "--query", "分红", expected=2)
        self.assertEqual(result["status"], "stale")
        self.assertFalse(result.get("hits"))
        self.command("sync", "--case", case, expected=1)
        original.write_bytes(before)
        result, _ = self.command("search", "--query", "分红")
        self.assertTrue(self.original_hits(result))

    def test_removed_manifest_source_is_retired_after_sync(self):
        case = self.make_case(years=(2020, 2021))
        self.sync(case)
        manifest = archive.read_json(case / "manifest.json")
        manifest["sources"] = [s for s in manifest["sources"] if s["period"] == "2021"]
        archive.atomic_json(case / "manifest.json", manifest)
        result, _ = self.command("search", "--query", "分红", expected=2)
        self.assertEqual(result["status"], "stale")
        self.sync(case)
        result, _ = self.command("search", "--query", "分红", "--start-year", 2020, "--end-year", 2020)
        self.assertFalse(result["hits"])
        self.assertEqual(result["coverage"]["groups"][0]["state"], "not_archived")

    def test_unknown_database_schema_is_rejected_without_mutation(self):
        case = self.make_case()
        self.sync(case)
        connection = sqlite3.connect(self.db)
        try:
            connection.execute("PRAGMA user_version=999")
            connection.commit()
        finally:
            connection.close()
        before = self.snapshot(self.db.parent)
        self.command("search", "--query", "分红", expected=1)
        self.assertEqual(self.snapshot(self.db.parent), before)

    def test_bounded_json_and_fetch_continuation_cover_exact_original_offsets(self):
        long_text = "分红记录：受控文本保留原始偏移与连续阅读边界。\n" * 1200
        case = self.make_case(text=long_text)
        self.sync(case)
        result, stream = self.command("search", "--query", "分红", "--max-chars", 4000)
        self.assertLessEqual(len(stream), 4000)
        self.assertTrue(result["truncated"])
        hit = self.original_hits(result)[0]
        self.assert_source_slice(case, hit)
        first, stream = self.command("fetch", "--hit-id", hit["hit_id"], "--max-chars", 4000)
        self.assertLessEqual(len(stream), 4000)
        self.assertTrue(first["truncated"])
        next_offset = first["next_offset"]
        self.assertIs(type(next_offset), int)
        second, stream = self.command("fetch", "--hit-id", hit["hit_id"], "--offset", next_offset, "--max-chars", 4000)
        self.assertLessEqual(len(stream), 4000)
        self.assertEqual(first["segments"][0]["start_offset"], 0)
        self.assertEqual(first["segments"][-1]["end_offset"], second["segments"][0]["start_offset"])
        delivered = "".join(segment["text"] for segment in first["segments"] + second["segments"])
        self.assertEqual(delivered, long_text[:second["segments"][-1]["end_offset"]])


if __name__ == "__main__":
    unittest.main()
