#!/usr/bin/env python3
"""Offline archive invariants. Run: python3 scripts/test_source_archive.py"""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("source_archive.py")
spec = importlib.util.spec_from_file_location("source_archive", SCRIPT)
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


def fixture_pdf(path):
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 7 0 R >> >> /Contents 4 0 R >>",
        None,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>",
        None,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for index, text in ((3, "Example Company 2025 Annual Report. This is the cover."), (5, "Related party balance disclosure. Cash is restricted for guarantees.")):
        stream = f"BT /F1 12 Tf 50 750 Td ({text}) Tj ET".encode()
        objects[index] = b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(str(number).encode() + b" 0 obj\n" + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(data)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.case = self.root / "case"
        self.call("init", "--case", self.case, "--company", "Example", "--code", "600000", "--start", "2020-01-01", "--end", "2026-11-05")

    def tearDown(self):
        self.temporary.cleanup()

    def call(self, *arguments, expected=0):
        result = subprocess.run([sys.executable, str(SCRIPT), *map(str, arguments)], capture_output=True, text=True)
        self.assertEqual(result.returncode, expected, result.stderr + result.stdout)
        return json.loads(result.stdout if result.stdout else result.stderr)

    def ingest(self, file, kind="csv"):
        return self.call("ingest", "--case", self.case, "--file", file, "--source-url", "https://example.com/report", "--title", "Example annual", "--period", "2025", "--report-type", "annual" if kind == "pdf" else "statement-export", "--kind", kind)

    def test_repeat_and_changed_export_preserve_versions(self):
        csv = self.root / "table.csv"
        csv.write_text("metric,2025\nrevenue,10\n", encoding="utf-8")
        first = self.ingest(csv)
        second = self.ingest(csv)
        self.assertEqual(first["source_id"], second["source_id"])
        self.assertEqual(second["status"], "existing")
        csv.write_text("metric,2025\nrevenue,11\n", encoding="utf-8")
        third = self.ingest(csv)
        self.assertNotEqual(first["source_id"], third["source_id"])
        self.assertIn("revenue,10", Path(first["path"]).read_text())
        self.assertEqual(len(archive.read_json(self.case / "manifest.json")["sources"]), 2)
        Path(first["path"]).write_text("corrupted")
        csv.write_text("metric,2025\nrevenue,10\n", encoding="utf-8")
        result = self.call("ingest", "--case", self.case, "--file", csv, "--source-url", "https://example.com/report", "--title", "Example annual", "--period", "2025", "--report-type", "statement-export", "--kind", "csv", expected=1)
        self.assertIn("changed", result["error"])

    def test_html_is_not_a_pdf_or_csv(self):
        file = self.root / "login.pdf"
        file.write_text("<!DOCTYPE html><html>Log in</html>")
        for kind in ("pdf", "csv"):
            with self.assertRaises(ValueError):
                archive.validate_file(file, kind, "utf-8-sig")

    def test_extraction_never_marks_read_and_coverage_requires_all_pages(self):
        original = self.root / "report.pdf"
        fixture_pdf(original)
        entry = self.ingest(original, "pdf")
        extracted = self.call("extract", "--case", self.case)[0]
        self.assertEqual(extracted["pages"], 2)
        pages = archive.read_json(Path(extracted["directory"]) / "pages.json")["pages"]
        self.assertEqual([p["pdf_page"] for p in pages], [1, 2])
        self.assertIn("cover", pages[0]["text"])
        self.assertIn("restricted", pages[1]["text"])
        self.call("coverage", "--case", self.case, expected=2)
        note_path = Path(extracted["reading_note"])
        note = archive.read_json(note_path)
        note["reader"] = "test-reader"
        note["reviewed_at"] = "2026-11-05T00:00:00Z"
        note["identity_check"] = {"status": "read", "pages": [1], "finding": "Cover identifies Example and its 2025 annual report.", "checked_company": "Example", "checked_code": "600000", "checked_period": "2025"}
        for section in note["sections"].values():
            section.update(status="not_disclosed", pages=[1, 2], finding="This two-page fixture contains no detailed section on this topic.")
        note["sections"]["cash_debt"].update(status="evidence_gap", finding="Page two mentions restrictions but the amount is not disclosed.", evidence_gaps=["Restricted cash amount is absent, so usable cash cannot be quantified."])
        note["whole_report_review"] = {"status": "read", "reviewed_page_ranges": [[1, 1]], "unreviewed_page_ranges": [[2, 2]], "finding": "The cover and main disclosure have been examined."}
        archive.atomic_json(note_path, note)
        self.assertFalse(self.call("coverage", "--case", self.case, expected=2)["record_complete"])
        note["whole_report_review"].update(reviewed_page_ranges=[[1, 2]], unreviewed_page_ranges=[])
        archive.atomic_json(note_path, note)
        checked = self.call("coverage", "--case", self.case)
        self.assertTrue(checked["record_complete"])
        self.assertEqual(checked["reports"][0]["text_delivery"]["tracking"], "not_started")
        self.assertFalse(checked["reports"][0]["text_delivery"]["delivery_complete"])
        self.assertEqual(len(checked["reports"][0]["evidence_gaps"]), 1)
        note["sections"]["cash_debt"]["status"] = "read"
        archive.atomic_json(note_path, note)
        self.assertEqual(len(self.call("coverage", "--case", self.case)["reports"][0]["evidence_gaps"]), 1)
        note["sections"]["audit"]["pages"] = [3]
        archive.atomic_json(note_path, note)
        self.call("coverage", "--case", self.case, expected=2)

    def test_report_type_profiles_code_identity_and_legacy_notes(self):
        source = {"source_id": "pdf-fixture", "sha256": "0" * 64, "company": "Example", "code": "000651", "period": "2026Q3", "report_type": "q3", "validation": {"pages": 2}}
        index = {"topics": {key: {"pages": []} for key in archive.TOPICS}}
        note = archive.reading_template(source, 2, index)
        self.assertEqual(set(note["sections"]), {"business", "statements", "significant_matters", "governance"})
        note.update(reader="Reader", reviewed_at="2026-11-06")
        note["identity_check"].update(status="read", pages=[1], finding="Cover confirms the complete legal company and period.", checked_company="Example Incorporated (legal name)", checked_code="000651", checked_period="2026Q3")
        for section in note["sections"].values():
            section.update(status="not_disclosed", pages=[1, 2], finding="The complete short report lacks a detailed disclosure on this topic.")
        note["whole_report_review"].update(status="read", reviewed_page_ranges=[[1, 2]], unreviewed_page_ranges=[], finding="Both physical pages were reviewed in order and relevant limits retained.")
        self.assertTrue(archive.check_reading(source, note)["record_complete"])
        invalid = copy.deepcopy(note)
        invalid["required_topics"] = []
        self.assertFalse(archive.check_reading(source, invalid)["record_complete"])
        invalid = copy.deepcopy(note)
        invalid["identity_check"]["checked_code"] = "651"
        self.assertFalse(archive.check_reading(source, invalid)["record_complete"])
        source["report_type"] = "annual"
        annual = archive.reading_template(source, 2, index)
        self.assertEqual(len(annual["sections"]), 13)
        legacy = copy.deepcopy(note)
        legacy.pop("reading_schema_version")
        legacy.pop("required_topics")
        legacy["identity_check"]["checked_company"] = "Example"
        result = archive.check_reading(source, legacy)
        self.assertEqual(result["record_profile"], "legacy_13_topics")
        self.assertFalse(result["record_complete"])
        self.assertTrue(any("related_parties" in error for error in result["errors"]))
        # An explicit unknown version must not silently downgrade to legacy checks.
        complete_legacy = archive.reading_template(source, 2, index)
        complete_legacy.update(reader="Reader", reviewed_at="2026-11-06")
        complete_legacy["identity_check"] = copy.deepcopy(legacy["identity_check"])
        for section in complete_legacy["sections"].values():
            section.update(status="not_disclosed", pages=[1, 2], finding="The complete fixture does not disclose detailed evidence on this topic.")
        complete_legacy["whole_report_review"] = copy.deepcopy(legacy["whole_report_review"])
        complete_legacy.pop("reading_schema_version")
        complete_legacy.pop("required_topics")
        self.assertTrue(archive.check_reading(source, complete_legacy)["record_complete"])
        for unknown in (1, 999, None, "2", True):
            complete_legacy["reading_schema_version"] = unknown
            invalid = archive.check_reading(source, complete_legacy)
            self.assertFalse(invalid["record_complete"])
            self.assertTrue(any("Unsupported reading_schema_version" in error for error in invalid["errors"]))

    def test_credentials_and_private_services_are_not_download_inputs(self):
        for value in ("https://u:p@example.com/file.pdf", "http://127.0.0.1/report", "https://example.com/report?access_token=secret"):
            with self.assertRaises(ValueError):
                archive.public_url(value)
        with self.assertRaises(ValueError):
            archive.validate_settings({"cookies": "private"})

    def test_source_bound_reading_template_exposes_contract_without_marking_read(self):
        original = self.root / "report.pdf"
        fixture_pdf(original)
        source = self.ingest(original, "pdf")
        extracted = self.call("extract", "--case", self.case)[0]
        note_path = Path(extracted["reading_note"])
        before = archive.sha256(note_path)
        result = self.call("reading-template", "--case", self.case, "--source-id", source["source_id"])
        self.assertEqual(result["template"]["source_sha256"], source["sha256"])
        self.assertEqual(result["template"]["identity_check"]["status"], "unread")
        self.assertEqual(result["template"]["identity_check"]["checked_code"], "")
        self.assertEqual(result["template"]["whole_report_review"]["reviewed_page_ranges"], [])
        self.assertEqual(result["template"]["whole_report_review"]["unreviewed_page_ranges"], [[1, 2]])
        self.assertIn("read", result["contract"]["section_status_enum"])
        self.assertEqual(result["contract"]["identity_check"]["complete_status"], "read")
        self.assertEqual(result["contract"]["material_findings"]["each_item_requires"], ["pages", "finding", "financial_impact"])
        self.assertFalse(result["contract"]["examples_are_observed_facts"])
        self.assertEqual(archive.sha256(note_path), before)
        output = self.root / "unread-template.json"
        exported = self.call("reading-template", "--case", self.case, "--source-id", source["source_id"], "--out", output)
        self.assertEqual(archive.read_json(output), exported["template"])
        self.call("reading-template", "--case", self.case, "--source-id", source["source_id"], "--out", output, expected=1)
        self.assertEqual(archive.sha256(note_path), before)


if __name__ == "__main__":
    unittest.main()
