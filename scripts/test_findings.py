#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

import case_state as cases
import findings
import source_archive as archive
from test_case_state import CaseFixture


class FindingTests(CaseFixture):
    def sample(self, **changes):
        item = {"id": "F001", "title": "Cash source", "claim_type": "inference", "status": "limited", "materiality": "material",
                "statement": "Current release is not a sustainable annual inflow.", "financial_impact": "Keep recurring cash assessment limited.",
                "sources": [{"source_id": self.sid, "pages": [1]}], "alternatives": ["Settlement timing"], "gaps": ["No complete subsidiary cash flow"]}
        return {**item, **changes}

    def test_one_current_finding_generates_report_and_tracks_revisions(self):
        self.assertEqual(findings.put(self.case, self.sample())["revision"], 1)
        before = archive.sha256(self.case / findings.LEDGER)
        self.assertEqual(findings.put(self.case, self.sample())["status"], "unchanged")
        self.assertEqual(before, archive.sha256(self.case / findings.LEDGER))
        template = self.write("analysis/report.md.in", "# Company\n\n{{finding:F001}}")
        out = self.case / "analysis/report.md"
        result = findings.render(self.case, out, template)
        self.assertIn("sustainable annual", out.read_text())
        self.assertEqual(cases.artifact_states(self.case)[result["artifact"]]["status"], "fresh")
        findings.put(self.case, self.sample(statement="The source remains unknown."))
        self.assertEqual(cases.artifact_states(self.case)[result["artifact"]]["status"], "stale")
        ledger = findings.load_ledger(self.case)
        self.assertEqual(len(ledger["findings"]), 1)
        self.assertEqual(len(ledger["findings"][0]["history"]), 1)
        findings.render(self.case, out, template)
        self.assertIn("source remains unknown", out.read_text())
        self.assertNotIn("sustainable annual", out.read_text())
        self.assertIn("类型：inference；状态：limited；重要性：material", out.read_text())

    def test_calculation_pointers_pin_adopted_version_and_current_value(self):
        p = self.case / "analysis/calc.json"
        archive.atomic_json(p, {"metric": {"value": "60"}})
        cases.register_artifact(self.case, "calc", p, source_ids=[self.sid])
        findings.put(self.case, self.sample(claim_type="calculation", sources=[], calculations=[{"artifact": "calc", "pointer": "/metric/value"}]))
        item = findings.load_ledger(self.case)["findings"][0]
        self.assertEqual(item["calculations"][0]["value"], "60")
        archive.atomic_json(p, {"metric": {"value": "70"}})
        cases.register_artifact(self.case, "calc", p, source_ids=[self.sid])
        self.assertFalse(findings.validate_ledger(self.case)["valid"])
        with self.assertRaises(ValueError):
            findings.render(self.case, "analysis/current.md")

    def test_gaps_and_real_citations_required_and_source_mutation_rejected(self):
        with self.assertRaisesRegex(ValueError, "gaps"):
            findings.put(self.case, self.sample(gaps=[]))
        with self.assertRaisesRegex(ValueError, "source or registered"):
            findings.put(self.case, self.sample(sources=[]))
        findings.put(self.case, self.sample())
        self.pdf.write_bytes(b"source replaced")
        self.assertFalse(findings.validate_ledger(self.case)["valid"])

    def test_template_unknown_id_and_raw_output_do_not_mutate_files(self):
        findings.put(self.case, self.sample())
        template = self.write("analysis/report.in", "{{finding:missing}}")
        with self.assertRaisesRegex(ValueError, "Unknown"):
            findings.render(self.case, "analysis/final.md", template)
        self.assertFalse((self.case / "analysis/final.md").exists())
        before = self.pdf.read_bytes()
        with self.assertRaises(ValueError):
            findings.render(self.case, self.pdf)
        self.assertEqual(before, self.pdf.read_bytes())

    def test_render_protects_canonical_inputs_even_without_registration(self):
        findings.put(self.case, self.sample())
        paths = [self.case / "manifest.json", self.case / cases.STATE_FILE]
        paths += [self.write(name, "legacy input") for name in cases.CANDIDATES.values()
                  if name not in {findings.LEDGER, "analysis/shareholder-returns.json"}]
        paths.append(self.write("workpapers/numbers/calculations.md", "numeric view"))
        for path in paths:
            before = path.read_bytes()
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "protected"):
                    findings.render(self.case, path)
                self.assertEqual(path.read_bytes(), before)

    def test_render_refuses_concurrent_evidence_template_or_rule_change(self):
        findings.put(self.case, self.sample())
        output = self.write("analysis/report.md", "previous report")
        template = self.write("analysis/report.md.in", "{{finding:F001}}")
        rule = self.write("synthetic-render-rule.py", "v1")
        original_render = findings.render_finding
        for target in (template, rule, self.case / findings.LEDGER):
            with self.subTest(target=target.name):
                # Restore valid inputs before each independent mutation.
                template.write_text("{{finding:F001}}", encoding="utf-8")
                findings.put(self.case, self.sample())
                def changed(case, finding):
                    result = original_render(case, finding)
                    if target == self.case / findings.LEDGER:
                        data = archive.read_json(target)
                        data["findings"][0]["statement"] = "new unreviewed statement"
                        archive.atomic_json(target, data)
                    else:
                        target.write_text("changed while rendering", encoding="utf-8")
                    return result
                with patch.object(findings, "__file__", str(rule)), patch.object(findings, "render_finding", side_effect=changed):
                    with self.assertRaises(ValueError):
                        findings.render(self.case, output, template)
                self.assertEqual(output.read_text(), "previous report")

    def test_stale_unrelated_calculation_pin_cannot_be_silently_refreshed(self):
        calc = self.case / "analysis/calc.json"
        archive.atomic_json(calc, {"value": "60"})
        cases.register_artifact(self.case, "calc", calc)
        first = self.sample(calculations=[{"artifact": "calc", "pointer": "/value"}])
        findings.put(self.case, first)
        before = archive.sha256(self.case / findings.LEDGER)
        archive.atomic_json(calc, {"value": "70"})
        cases.register_artifact(self.case, "calc", calc)
        second = self.sample(id="F002", title="Separate finding")
        with self.assertRaisesRegex(ValueError, "older artifact"):
            findings.put(self.case, second)
        self.assertEqual(before, archive.sha256(self.case / findings.LEDGER))
        findings.put(self.case, [first, second])
        self.assertTrue(findings.validate_ledger(self.case)["valid"])
        self.assertEqual(cases.artifact_states(self.case)["findings"]["status"], "fresh")

    def test_reports_bind_scope_and_source_inventory_and_escape_source_links(self):
        manifest = archive.require_case(self.case)
        manifest["sources"][0]["title"] = "Original [revised]"
        archive.atomic_json(self.case / "manifest.json", manifest)
        findings.put(self.case, self.sample())
        template = self.write("analysis/report.in", "{{finding:F001}}")
        result = findings.render(self.case, "analysis/report.md", template)
        report = (self.case / "analysis/report.md").read_text()
        self.assertIn("[Original \\[revised\\]](<", report)
        for mutation in ("scope", "source"):
            with self.subTest(mutation=mutation):
                findings.render(self.case, "analysis/report.md", template)
                manifest = archive.require_case(self.case)
                if mutation == "scope":
                    manifest["requested_period"]["end"] = "2026-10-01"
                else:
                    source = dict(manifest["sources"][0])
                    source["source_id"] += "-second"
                    manifest["sources"].append(source)
                archive.atomic_json(self.case / "manifest.json", manifest)
                self.assertEqual(cases.artifact_states(self.case)[result["artifact"]]["status"], "stale")


if __name__ == "__main__":
    unittest.main()
