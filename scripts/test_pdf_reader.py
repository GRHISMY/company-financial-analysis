#!/usr/bin/env python3
"""Behavioral tests for bounded PDF delivery, not wording snapshots."""
import json
import copy
from pathlib import Path
import tempfile
import unittest
import source_archive as archive
import pdf_reader as reader


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.case = Path(self.temp.name)
        (self.case / "raw/pdf").mkdir(parents=True)
        self.manifest = {"schema_version": 1, "company": "Example", "code": "000651", "sources": []}
        self.save_manifest()

    def tearDown(self):
        self.temp.cleanup()

    def save_manifest(self):
        archive.atomic_json(self.case / "manifest.json", self.manifest)

    def source(self, sid, texts):
        original = self.case / "raw/pdf" / (sid + ".pdf")
        original.write_bytes(("%PDF fixture " + sid).encode())
        item = {"source_id": sid, "kind": "pdf", "company": "Example", "code": "000651", "period": "2025", "report_type": "annual", "path": str(original.relative_to(self.case)), "sha256": archive.sha256(original), "validation": {"pages": len(texts)}}
        self.manifest["sources"].append(item)
        self.save_manifest()
        directory = self.case / "workpapers/pdf" / sid
        archive.atomic_json(directory / "pages.json", {"source_id": sid, "source_sha256": item["sha256"], "pages": [{"pdf_page": i, "text": t} for i, t in enumerate(texts, 1)]})
        return item

    def consume(self, sid, budget=3000, bases=None):
        chunks = []
        for _ in range(200):
            chunk = reader.next_chunk(self.case, sid, budget, bases)
            if chunk.get("delivery_complete"):
                return chunks
            self.assertLessEqual(len(reader.encoded(chunk)) + 1, budget)
            chunks.append(chunk)
            reader.acknowledge(self.case, sid, chunk["chunk_id"], chunk["receipt_sha256"])
        self.fail("Reader did not complete")

    def test_budget_split_replay_ack_and_page_completion(self):
        text = '表格含引号"换行\n' + '中文数据123456\\' * 230
        self.source("pdf-long", [text, "", "last page"])
        first = reader.next_chunk(self.case, "pdf-long", 850)
        self.assertLessEqual(len(reader.encoded(first)) + 1, 850)
        self.assertFalse(first["segments"][-1]["page_complete"])
        status = reader.source_status(self.case, "pdf-long")
        self.assertEqual(status["completed_page_ranges"], [])
        self.assertEqual(status["acknowledged_chunks"], 0)
        self.assertEqual(reader.next_chunk(self.case, "pdf-long", 850), first)
        with self.assertRaises(ValueError):
            reader.next_chunk(self.case, "pdf-long", 700)
        with self.assertRaises(ValueError):
            reader.acknowledge(self.case, "pdf-long", first["chunk_id"], "0" * 64)
        self.assertEqual(reader.next_chunk(self.case, "pdf-long", 850), first)
        reader.acknowledge(self.case, "pdf-long", first["chunk_id"], first["receipt_sha256"])
        self.assertEqual(reader.acknowledge(self.case, "pdf-long", first["chunk_id"], first["receipt_sha256"])["status"], "already_acknowledged")
        chunks = [first] + self.consume("pdf-long", 850)
        self.assertEqual("".join(s["text"] for c in chunks for s in c["segments"]), text + "last page")
        status = reader.source_status(self.case, "pdf-long")
        self.assertTrue(status["delivery_complete"])
        self.assertEqual(status["completed_page_ranges"], [[1, 3]])

    def test_source_or_extraction_change_fails_without_advancing(self):
        source = self.source("pdf-a", ["A" * 2000])
        first = reader.next_chunk(self.case, "pdf-a", 1000)
        path = self.case / "workpapers/pdf/pdf-a/pages.json"
        data = archive.read_json(path)
        data["pages"][0]["text"] += "changed"
        archive.atomic_json(path, data)
        with self.assertRaisesRegex(ValueError, "stale"):
            reader.acknowledge(self.case, "pdf-a", first["chunk_id"], first["receipt_sha256"])
        self.assertFalse(reader.status_report(self.case)["delivery_complete"])
        restarted = reader.reset_progress(self.case, "pdf-a", "The extracted page text was regenerated and must be reread.")
        self.assertTrue((self.case / restarted["previous_progress"]).is_file())
        fresh = reader.next_chunk(self.case, "pdf-a", 1000)
        self.assertEqual(fresh["segments"][0]["start_offset"], 0)
        self.assertNotEqual(fresh["chunk_id"], first["chunk_id"])
        (self.case / source["path"]).write_text("broken original")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            reader.next_chunk(self.case, "pdf-a")

    def test_explicit_rechunk_retries_same_unacknowledged_cursor(self):
        self.source("pdf-a", ["Long text to read. " * 500])
        original = reader.next_chunk(self.case, "pdf-a", 2500)
        smaller = reader.next_chunk(self.case, "pdf-a", 850, rechunk_pending=True)
        self.assertLessEqual(len(reader.encoded(smaller)) + 1, 850)
        self.assertEqual(smaller["segments"][0]["start_offset"], original["segments"][0]["start_offset"])
        self.assertLess(smaller["segments"][-1]["end_offset"], original["segments"][-1]["end_offset"])
        with self.assertRaises(ValueError):
            reader.acknowledge(self.case, "pdf-a", original["chunk_id"], original["receipt_sha256"])
        self.assertEqual(reader.source_status(self.case, "pdf-a")["acknowledged_chunks"], 0)
        reader.acknowledge(self.case, "pdf-a", smaller["chunk_id"], smaller["receipt_sha256"])
        reader.next_chunk(self.case, "pdf-a", 850)
        self.assertEqual(reader.acknowledge(self.case, "pdf-a", smaller["chunk_id"], smaller["receipt_sha256"])["status"], "already_acknowledged")

    def test_empty_case_status_is_read_only(self):
        status = reader.status_report(self.case)
        self.assertEqual(status["sources"], [])
        self.assertFalse(status["delivery_complete"])
        self.assertFalse((self.case / ".archive.lock").exists())

    def test_acknowledged_history_cannot_expand_offsets_without_matching_receipt(self):
        text = "Original long disclosure wording. " * 250
        self.source("pdf-a", [text])
        chunk = reader.next_chunk(self.case, "pdf-a", 850)
        reader.acknowledge(self.case, "pdf-a", chunk["chunk_id"], chunk["receipt_sha256"])
        state_path = self.case / "workpapers/pdf/pdf-a/reader-state.json"
        state = archive.read_json(state_path)
        state["acknowledged"][0]["segments"][0]["end_offset"] = len(text)
        state["acknowledged"][0]["segments"][0]["page_complete"] = True
        archive.atomic_json(state_path, state)
        with self.assertRaisesRegex(ValueError, "Receipt|receipt"):
            reader.load_state(reader.snapshot(self.case, "pdf-a"))
        status = reader.status_report(self.case, "pdf-a")
        self.assertFalse(status["delivery_complete"])
        self.assertIn("error", status["sources"][0])

    def test_acknowledged_dedup_dependencies_are_bound_to_delivered_payload(self):
        policy = "Identical fully read long accounting policy wording. " * 7 + "\n"
        self.source("pdf-base", [policy])
        self.source("pdf-a", [policy])
        self.consume("pdf-base", 5000)
        self.consume("pdf-a", 5000, ["pdf-base"])
        path = self.case / "workpapers/pdf/pdf-a/reader-state.json"
        original = archive.read_json(path)
        altered = copy.deepcopy(original)
        altered["acknowledged"][0]["dedup_dependencies"] = []
        archive.atomic_json(path, altered)
        with self.assertRaisesRegex(ValueError, "dependencies changed"):
            reader.load_state(reader.snapshot(self.case, "pdf-a"))
        altered = copy.deepcopy(original)
        altered["acknowledged"][0]["segments"][0]["omissions"][0]["source_id"] = "pdf-other"
        archive.atomic_json(path, altered)
        with self.assertRaisesRegex(ValueError, "checksum changed"):
            reader.load_state(reader.snapshot(self.case, "pdf-a"))

    def test_metadata_only_reextraction_keeps_progress_but_page_text_change_is_stale(self):
        self.source("pdf-a", ["First page disclosure. " * 100, "Second page disclosure. " * 100])
        chunk = reader.next_chunk(self.case, "pdf-a", 1000)
        reader.acknowledge(self.case, "pdf-a", chunk["chunk_id"], chunk["receipt_sha256"])
        before = reader.source_status(self.case, "pdf-a")
        path = self.case / "workpapers/pdf/pdf-a/pages.json"
        raw_hash = archive.sha256(path)
        extracted = archive.read_json(path)
        extracted["extracted_at"] = "later harmless extraction timestamp"
        extracted["method"] = "same content new extractor version"
        path.write_text(json.dumps(extracted, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        self.assertNotEqual(archive.sha256(path), raw_hash)
        self.assertEqual(reader.source_status(self.case, "pdf-a"), before)
        extracted["pages"][1]["text"] = "X" + extracted["pages"][1]["text"][1:]
        archive.atomic_json(path, extracted)
        with self.assertRaisesRegex(ValueError, "stale"):
            reader.source_status(self.case, "pdf-a")

    def test_visual_required_pages_remain_separate_after_all_text_acknowledged(self):
        self.source("pdf-a", ["", "Long clear text disclosure. " * 20, "short image label"])
        path = self.case / "workpapers/pdf/pdf-a/pages.json"
        extracted = archive.read_json(path)
        extracted["pages"][1]["needs_visual_or_ocr"] = True
        archive.atomic_json(path, extracted)
        chunks = self.consume("pdf-a", 5000)
        self.assertEqual(chunks[0]["visual_required_pages"], [1, 2, 3])
        status = reader.source_status(self.case, "pdf-a")
        self.assertTrue(status["delivery_complete"])
        self.assertEqual(status["visual_required_pages"], [1, 2, 3])
        self.assertEqual(status["visual_verification"], "separate_from_text_receipts")

    def test_next_explicitly_acknowledges_only_previous_chunk(self):
        self.source("pdf-a", ["A" * 5000])
        first = reader.next_chunk(self.case, "pdf-a", 850)
        with self.assertRaisesRegex(ValueError, "supplied together"):
            reader.next_chunk(self.case, "pdf-a", 850, ack_chunk_id=first["chunk_id"])
        self.assertEqual(reader.source_status(self.case, "pdf-a")["acknowledged_chunks"], 0)
        second = reader.next_chunk(self.case, "pdf-a", 850, ack_chunk_id=first["chunk_id"], ack_receipt_sha256=first["receipt_sha256"])
        self.assertEqual(second["segments"][0]["start_offset"], first["segments"][-1]["end_offset"])
        status = reader.source_status(self.case, "pdf-a")
        self.assertEqual(status["acknowledged_chunks"], 1)
        self.assertEqual(status["pending_chunk_id"], second["chunk_id"])
        self.assertEqual(reader.next_chunk(self.case, "pdf-a", 850), second)

    def test_exact_dedup_only_acknowledged_full_nonnumeric_lines(self):
        policy = "Identical accounting policy wording retained as a long exact line. " * 5 + "\n"
        numeric = "Statement line revenue 2025 987654321 exact comparison. " * 3 + "\n"
        chinese_number = "本政策要求一项相同事项按照公司既有原则与具体情况予以解释并完整披露相关依据和估计。\n"
        self.source("pdf-base", [policy + numeric + chinese_number])
        self.source("pdf-new", [policy + numeric + chinese_number + "new changed wording\n"])
        with self.assertRaisesRegex(ValueError, "acknowledged"):
            reader.next_chunk(self.case, "pdf-new", 5000, ["pdf-base"])
        self.consume("pdf-base", 5000)
        chunks = self.consume("pdf-new", 5000, ["pdf-base"])
        shown = "".join(s["text"] for c in chunks for s in c["segments"])
        self.assertNotIn(policy, shown)
        self.assertIn(numeric, shown)
        self.assertIn(chinese_number, shown)
        self.assertIn("new changed wording", shown)
        omitted = [o for c in chunks for s in c["segments"] for o in s["omissions"]]
        self.assertEqual(len(omitted), 1)
        self.assertEqual(omitted[0]["source_id"], "pdf-base")
        self.assertEqual(omitted[0]["line_number"], 1)
        self.assertEqual(omitted[0]["target_line_number"], 1)
        base_state = self.case / "workpapers/pdf/pdf-base/reader-state.json"
        state = archive.read_json(base_state)
        state["acknowledged"] = []
        archive.atomic_json(base_state, state)
        with self.assertRaisesRegex(ValueError, "no longer acknowledged"):
            reader.source_status(self.case, "pdf-new")

    def test_partial_base_line_is_not_reused_and_search_does_not_ack(self):
        long_line = "Policy text without numbers. " * 70 + "\n"
        self.source("pdf-base", [long_line])
        self.source("pdf-new", [long_line])
        part = reader.next_chunk(self.case, "pdf-base", 850)
        reader.acknowledge(self.case, "pdf-base", part["chunk_id"], part["receipt_sha256"])
        chunk = reader.next_chunk(self.case, "pdf-new", 5000, ["pdf-base"])
        self.assertEqual(chunk["segments"][0]["omissions"], [])
        before = reader.source_status(self.case, "pdf-base")
        found = reader.search(self.case, "pdf-base", "Policy", limit=2, max_chars=600)
        self.assertTrue(found["truncated"])
        self.assertLessEqual(len(reader.encoded(found)) + 1, 600)
        self.assertEqual(reader.source_status(self.case, "pdf-base"), before)
        with self.assertRaises(ValueError):
            reader.search(self.case, "pdf-base", "x" * 2000, max_chars=500)

    def test_transitive_dedup_evidence_mutation_is_detected(self):
        policy = "Long identical policy text without any numeric characters. " * 8 + "\n"
        base = self.source("pdf-a", [policy])
        self.source("pdf-b", [policy])
        self.source("pdf-c", [policy])
        self.consume("pdf-a", 5000)
        self.consume("pdf-b", 5000, ["pdf-a"])
        self.consume("pdf-c", 5000, ["pdf-b"])
        (self.case / base["path"]).write_text("altered source")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            reader.source_status(self.case, "pdf-c")


if __name__ == "__main__":
    unittest.main()
