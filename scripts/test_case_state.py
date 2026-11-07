#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import case_state as cases
import source_archive as archive


class CaseFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.case = Path(self.temp.name)
        (self.case / "raw/pdf").mkdir(parents=True)
        self.pdf = self.case / "raw/pdf/original.pdf"
        self.pdf.write_bytes(b"controlled fixture, not a real PDF parser input")
        self.sid = "pdf-" + archive.sha256(self.pdf)[:16]
        archive.atomic_json(self.case / "manifest.json", {
            "schema_version": 1, "company": "Example", "code": "000651",
            "requested_period": {"start": "2020-01-01", "end": "2026-11-06"},
            "sources": [{"source_id": self.sid, "company": "Example", "code": "000651", "period": "2025",
                         "kind": "pdf", "report_type": "annual", "title": "Original", "path": "raw/pdf/original.pdf",
                         "sha256": archive.sha256(self.pdf), "validation": {"pages": 2}}]})

    def write(self, name, text):
        path = self.case / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def financial_facts(self):
        import financial_pipeline as pipeline
        fields = {key: "0" for key in set(pipeline.CORE) | set(pipeline.OPERATING) | {"operating_profit", "nonoperating_income", "nonoperating_expense", "profit_before_tax", "income_tax", "net_profit", "attributable_profit", "minority_profit", "assets", "liabilities", "equity", "cfo", "cfi", "cff", "fx_effect", "cash_change", "cash_begin", "cash_end", "capex"}}
        fields.update(revenue="100", cost="40", operating_profit="60", profit_before_tax="60", net_profit="60", attributable_profit="60", assets="100", equity="100", cfo="60", cfi="-10", cash_change="50", cash_begin="10", cash_end="60", capex="10")
        return {"schema_version": "financial-facts/v1", "company": {"name": "Example", "code": "000651"}, "periods": [{"id": "2025/consolidated", "scope": "consolidated", "currency": "CNY", "basis": "controlled-fixture", "start": "2025-01-01", "end": "2025-12-31", "period_type": "annual", "fields": {k: {"value": v, "precision": "0.01", "unit": "CNY", "source": {"fixture": "synthetic test"}} for k,v in fields.items()}}]}


class CaseStateTests(CaseFixture):
    def test_legacy_status_is_read_only_and_does_not_adopt_guessed_final(self):
        self.write("workpapers/numbers/adopted-facts.json", "{}")
        before = {str(p.relative_to(self.case)): archive.sha256(p) for p in self.case.rglob("*") if p.is_file()}
        result = cases.status_report(self.case, verify_sources=True)
        after = {str(p.relative_to(self.case)): archive.sha256(p) for p in self.case.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(result["registry"], "unregistered")
        self.assertIn("adopted_facts", result["unregistered_candidates"])
        self.assertNotIn("adopted_facts", result["artifacts"])

    def test_dependency_changes_propagate_and_old_derived_cannot_be_registered(self):
        a = self.write("analysis/a.json", "1")
        b = self.write("analysis/b.json", "2")
        c = self.write("analysis/c.md", "result")
        cases.register_artifact(self.case, "a", a, source_ids=[self.sid])
        cases.register_artifact(self.case, "b", b, depends=["a"])
        cases.register_artifact(self.case, "report", c, depends=["b"])
        a.write_text("3")
        states = cases.artifact_states(self.case)
        self.assertEqual(states["a"]["status"], "stale")
        self.assertEqual(states["b"]["status"], "stale")
        self.assertEqual(states["report"]["status"], "stale")
        with self.assertRaisesRegex(ValueError, "upstream"):
            cases.register_artifact(self.case, "report", c, depends=["b"])
        cases.register_artifact(self.case, "a", a, source_ids=[self.sid])
        self.assertEqual(cases.artifact_states(self.case)["b"]["status"], "stale")

    def test_original_and_rule_mutations_invalidate_results(self):
        artifact = self.write("analysis/result.json", "{}")
        rule = self.write("rules.py", "v1")
        cases.register_artifact(self.case, "result", artifact, source_ids=[self.sid], producer=rule)
        self.assertEqual(cases.artifact_states(self.case)["result"]["status"], "fresh")
        rule.write_text("v2")
        self.assertEqual(cases.artifact_states(self.case)["result"]["status"], "stale")
        rule.write_text("v1")
        self.pdf.write_bytes(b"changed original")
        self.assertEqual(cases.artifact_states(self.case)["result"]["status"], "stale")
        with self.assertRaisesRegex(ValueError, "missing/changed"):
            cases.register_artifact(self.case, "result", artifact, source_ids=[self.sid])

    def test_reject_cycles_and_raw_overwrite_and_path_escape(self):
        a = self.write("analysis/a", "a")
        b = self.write("analysis/b", "b")
        cases.register_artifact(self.case, "a", a)
        cases.register_artifact(self.case, "b", b, depends=["a"])
        before = archive.sha256(self.case / cases.STATE_FILE)
        with self.assertRaisesRegex(ValueError, "cycle"):
            cases.register_artifact(self.case, "a", a, depends=["b"])
        self.assertEqual(before, archive.sha256(self.case / cases.STATE_FILE))
        with self.assertRaises(ValueError):
            cases.register_artifact(self.case, "original", self.pdf)
        with self.assertRaises(ValueError):
            cases.inside(self.case, "../external")

    def test_citation_identity_pages_and_fact_hash_are_checked(self):
        ref = cases.source_ref(self.case, {"source_id": self.sid, "pages": [2, 1, 2]})
        self.assertEqual(ref["pages"], [1, 2])
        for pages in ([], [3], [True]):
            with self.assertRaises(ValueError):
                cases.source_ref(self.case, {"source_id": self.sid, "pages": pages})
        with self.assertRaises(ValueError):
            cases.facts_source_ids(self.case, {"source": {"pdf": str(self.pdf), "sha256": "0" * 64}})

    def test_build_uses_only_registered_adopted_facts_and_shared_calculation(self):
        import financial_pipeline as pipeline
        data = self.financial_facts()
        path = self.case / "workpapers/numbers/adopted-facts.json"
        archive.atomic_json(path, data)
        with self.assertRaisesRegex(ValueError, "Register fresh"):
            cases.build_case(self.case)
        cases.register_artifact(self.case, "adopted_facts", path, source_ids=[self.sid])
        result = cases.build_case(self.case)
        actual = archive.read_json(Path(result["calculations"]))
        self.assertEqual(actual, pipeline.calculate(data))
        self.assertEqual(actual["periods"][0]["derived"]["core_profit"]["value"], "60")
        self.assertTrue(all(x["status"] == "fresh" for x in cases.artifact_states(self.case).values()))
        self.assertIn("not built", result["input_checks"])

    def test_build_refuses_output_collision_without_touching_adopted_input(self):
        path = self.case / "workpapers/numbers/calculations.json"
        archive.atomic_json(path, self.financial_facts())
        cases.register_artifact(self.case, "adopted_facts", path)
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "collides"):
            cases.build_case(self.case)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((path.parent / "calculations.md").exists())

    def test_build_refuses_changed_inputs_or_rules_before_any_output_write(self):
        import financial_pipeline as pipeline
        path = self.case / "workpapers/numbers/adopted-facts.json"
        archive.atomic_json(path, self.financial_facts())
        output = self.write("workpapers/numbers/calculations.json", "previous output")
        rule = self.write("synthetic-rule.py", "v1")
        original_calculate = pipeline.calculate
        for target in (path, rule):
            with self.subTest(target=target.name):
                archive.atomic_json(path, self.financial_facts())
                cases.register_artifact(self.case, "adopted_facts", path)
                def changed(data):
                    target.write_text("changed during calculation", encoding="utf-8")
                    return original_calculate(data)
                with patch.object(pipeline, "__file__", str(rule)), patch.object(pipeline, "calculate", side_effect=changed):
                    with self.assertRaisesRegex(ValueError, "changed during build"):
                        cases.build_case(self.case)
                self.assertEqual(output.read_text(), "previous output")
                self.assertFalse((output.parent / "calculations.md").exists())


if __name__ == "__main__":
    unittest.main()
