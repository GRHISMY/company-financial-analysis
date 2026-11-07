#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sqlite3
import unittest
from unittest.mock import patch

import case_state
import evidence_index as index
import findings
import source_archive as archive
from test_case_state import CaseFixture


class EvidenceIndexTests(CaseFixture):
    def setUp(self):
        super().setUp()
        self.db = self.case / "cache/evidence.sqlite3"
        archive.atomic_json(self.case / "workpapers/pdf" / self.sid / "pages.json", {
            "schema_version": 1, "source_id": self.sid, "source_sha256": archive.sha256(self.pdf),
            "pages": [{"pdf_page": n, "text": "现金质量：财务公司受限资金须回原件核准，不能只凭一个现金比率判断。" * n,
                       "needs_visual_or_ocr": False} for n in (1, 2)]})

    def calculation_finding(self):
        csv = self.write("raw/tables/original.csv", "code,value\n000651,60\n")
        manifest = archive.require_case(self.case)
        self.csv_id = "csv-" + archive.sha256(csv)[:16]
        manifest["sources"].append({"source_id": self.csv_id, "kind": "csv", "path": "raw/tables/original.csv",
            "sha256": archive.sha256(csv), "code": "000651", "company": "Example", "period": "2025", "report_type": "statement-export"})
        archive.atomic_json(self.case / "manifest.json", manifest)
        rule = self.write("calculation-rule.py", "stable rule")
        calc = self.case / "analysis/calc.json"
        archive.atomic_json(calc, {"value": "60"})
        case_state.register_artifact(self.case, "calc", calc, source_ids=[self.csv_id], producer=rule)
        findings.put(self.case, {"id": "F001", "title": "Calculation-only cash finding", "claim_type": "calculation",
            "status": "supported", "materiality": "material", "statement": "Calculation shows 60 units of cash.",
            "financial_impact": "The calculation is separate from original-page verification.", "sources": [],
            "calculations": [{"artifact": "calc", "pointer": "/value"}], "gaps": [], "alternatives": []})
        return csv, rule

    def test_calculation_only_findings_keep_refs_without_inventing_pdf_pages(self):
        self.calculation_finding()
        index.sync([self.case], self.db)
        result = index.search("Calculation-only", self.db)
        hit = next(h for h in result["hits"] if h["kind"] == "finding")
        self.assertEqual(hit["pages"], [])
        self.assertIsNone(hit["source_id"])
        self.assertIsNone(hit["year"])
        expanded = index.fetch(hit["hit_id"], self.db)
        self.assertEqual(expanded["segments"], [])
        self.assertEqual(expanded["hit"]["extra"]["calculations"][0]["value"], "60")
        self.assertIn("no_bound_pdf", expanded["notice"])

    def test_source_csv_and_calculation_producer_mutations_invalidate_cached_finding(self):
        csv, rule = self.calculation_finding()
        index.sync([self.case], self.db)
        original_csv, original_rule = csv.read_bytes(), rule.read_bytes()
        for target in (csv, rule):
            with self.subTest(target=target.name):
                target.write_bytes(b"changed outside the ledger")
                result = index.search("Calculation-only", self.db)
                self.assertEqual(result["status"], "stale")
                self.assertEqual(result["hits"], [])
                with self.assertRaisesRegex(ValueError, "stale/invalid"):
                    index.sync([self.case], self.db)
                csv.write_bytes(original_csv)
                rule.write_bytes(original_rule)
                self.assertEqual(index.status_report(self.db)["status"], "ready")

    def test_unindexed_requested_scope_never_silently_disappears(self):
        index.sync([self.case], self.db)
        for requested in (["999999"], ["000651", "999999"]):
            with self.subTest(codes=requested):
                result = index.search("受限", self.db, codes=requested)
                self.assertEqual(result["status"], "not_indexed")
                self.assertEqual(result["hits"], [])
                self.assertIn("999999", result["unmatched_scope"]["codes"])
                report = index.coverage(self.db, codes=requested, start_year=2020, end_year=2025)
                self.assertEqual(report["status"], "not_indexed")

    def test_change_after_capture_rolls_back_without_replacing_previous_cache(self):
        index.sync([self.case], self.db)
        before = self.db.read_bytes()
        original_connect = index._connect
        def changed(db, write=False):
            conn = original_connect(db, write)
            if write:
                self.pdf.write_bytes(b"original changed after capture")
            return conn
        with patch.object(index, "_connect", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "transaction rolled back"):
                index.sync([self.case], self.db)
        self.assertEqual(before, self.db.read_bytes())
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute("SELECT count(*) FROM documents").fetchone()[0], 1)
        finally:
            conn.close()

    def test_keyword_lists_do_not_displace_original_matches_and_kind_is_explicit(self):
        archive.atomic_json(self.case / "workpapers/pdf" / self.sid / "index.json", {
            "source_id": self.sid, "topics": {"cash_debt": {
                "label": "财务公司 受限资金 核心利润 回购",
                "keywords": ["财务公司", "受限资金", "核心利润", "回购", "保证金", "冻结", "使用权受到限制"],
                "pages": [1, 2]}}})
        index.sync([self.case], self.db)
        query = "财务公司 受限资金 核心利润 回购"
        result = index.search(query, self.db, limit=1)
        self.assertEqual(result["hits"][0]["kind"], "original")
        originals = index.search(query, self.db, kinds=["original"])
        self.assertTrue(originals["hits"])
        self.assertTrue(all(h["kind"] == "original" for h in originals["hits"]))
        navigation = index.search(query, self.db, kinds=["keyword_navigation"])
        self.assertTrue(navigation["hits"])
        self.assertTrue(all(h["kind"] == "keyword_navigation" for h in navigation["hits"]))

    def test_coverage_reports_actual_report_type_counts(self):
        manifest = archive.require_case(self.case)
        for suffix, report_type, period in (("h1", "half-year", "2025H1"), ("q3", "q3", "2025Q3"), ("notice", "other", "2025")):
            pdf = self.write("raw/pdf/" + suffix + ".pdf", "distinct original " + suffix)
            manifest["sources"].append({"source_id": "pdf-" + archive.sha256(pdf)[:16], "kind": "pdf",
                "path": str(pdf.relative_to(self.case)), "sha256": archive.sha256(pdf), "code": "000651",
                "company": "Example", "period": period, "title": suffix, "report_type": report_type,
                "validation": {"pages": 1}})
        archive.atomic_json(self.case / "manifest.json", manifest)
        index.sync([self.case], self.db)
        report = index.coverage(self.db, codes=["000651"], start_year=2025, end_year=2026)
        groups = report["coverage"]["groups"]
        self.assertEqual(groups[0]["report_types"], {"annual": 1, "half-year": 1, "other": 1, "q3": 1})
        self.assertEqual(groups[1]["report_types"], {})
        filtered = index.coverage(self.db, codes=["000651"], start_year=2025, end_year=2025, report_type="annual")
        self.assertEqual(filtered["coverage"]["groups"][0]["report_types"], {"annual": 1})


if __name__ == "__main__":
    unittest.main()
