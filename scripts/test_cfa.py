#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI export transactions preserve the input/producer version actually computed."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cfa
import case_state as cases
import returns_ledger as returns
import source_archive as archive


class ReturnExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.case = self.root / 'case'
        (self.case / 'raw/pdf').mkdir(parents=True)
        sources = []
        for name in ['direct', 'alternative']:
            file = self.case / 'raw/pdf' / (name + '.pdf')
            file.write_bytes(('%PDF fixture ' + name).encode())
            sources.append({'source_id': 'pdf-' + name, 'kind': 'pdf', 'company': 'Example', 'code': '000651',
                            'path': str(file.relative_to(self.case)), 'sha256': archive.sha256(file), 'validation': {'pages': 2},
                            'period': '2024', 'report_type': 'other'})
        archive.atomic_json(self.case / 'manifest.json', {'schema_version': 1, 'company': 'Example', 'code': '000651',
                            'requested_period': {'start': '2024-01-01', 'end': '2026-11-06'}, 'sources': sources})
        self.event = {'event_id': 'dividend-2024', 'economic_id': '2023-final-dividend', 'kind': 'dividend', 'stage': 'cash_paid',
                      'period_basis': 'cash_payment', 'aggregation': 'single', 'start': '2024-08-28', 'end': '2024-08-28',
                      'cash_date': '2024-08-28', 'earnings_year': '2023', 'payer': 'listed_parent', 'recipient': 'listed_shareholders',
                      'currency': 'CNY', 'fee_basis': 'not_applicable', 'value': '13142225877.48', 'precision': '0.01',
                      'sources': [{'source_id': 'pdf-direct', 'pages': [1], 'role': 'implementation_notice'}],
                      'alternatives': [{'value': '13143744877.48', 'source': {'source_id': 'pdf-alternative', 'pages': [2], 'role': 'liability_bridge'}, 'reason': 'different payable coverage'}],
                      'adoption_reason': 'Use direct payment evidence; payable bridge is a different measure'}
        returns.put_events(self.case, self.event)
        self.producer = self.root / 'rule-version.py'
        self.producer.write_text('# isolated producer fingerprint version one\n')
        self.patch_file = patch.object(returns, '__file__', str(self.producer))
        self.patch_file.start()
        self.addCleanup(self.patch_file.stop)
        self.output = self.case / 'workpapers/returns-summary.json'

    def call(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cfa.main(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()

    def export(self, command='summary', output=None):
        return self.call('returns', command, '--case', str(self.case), '--output', str(output or self.output))

    def snapshots(self):
        state = self.case / cases.STATE_FILE
        return self.output.read_bytes() if self.output.exists() else None, state.read_bytes() if state.exists() else None

    def test_export_registers_exact_sources_producer_manifest_and_repeats(self):
        self.assertEqual(self.export()[0], 0)
        summary = json.loads(self.output.read_text())
        self.assertEqual(summary['groups'][0]['total'], '13142225877.48')
        state = cases.load_state(self.case)
        key = next(k for k in state['artifacts'] if k.startswith('returns_summary.'))
        deps = state['artifacts'][key]['dependencies']
        self.assertIn({'kind': 'artifact', 'key': 'returns', 'sha256': archive.sha256(self.case / returns.LEDGER)}, deps)
        self.assertIn({'kind': 'file', 'path': 'manifest.json', 'sha256': archive.sha256(self.case / 'manifest.json')}, deps)
        self.assertIn({'kind': 'tool', 'path': str(self.producer), 'sha256': archive.sha256(self.producer)}, deps)
        self.assertEqual({d['source_id'] for d in state['artifacts']['returns']['dependencies']}, {'pdf-direct', 'pdf-alternative'})
        self.assertTrue(all(x['status'] == 'fresh' for x in cases.artifact_states(self.case).values()))
        self.assertEqual(self.export()[0], 0)
        self.assertEqual(len(cases.load_state(self.case)['artifacts']), 2)

    def test_ledger_change_during_generation_aborts_before_overwriting(self):
        self.assertEqual(self.export()[0], 0)
        before = self.snapshots()
        original = returns.summarize
        def changing(*args, **kwargs):
            result = original(*args, **kwargs)
            data = archive.read_json(self.case / returns.LEDGER)
            data['events'][0]['value'] = '1'
            archive.atomic_json(self.case / returns.LEDGER, data)
            return result
        with patch.object(returns, 'summarize', side_effect=changing):
            code, _, err = self.export()
        self.assertEqual(code, 1)
        self.assertIn('changed during generation', err)
        self.assertEqual(before, self.snapshots())
        self.assertEqual(archive.read_json(self.case / returns.LEDGER)['events'][0]['value'], '1')

    def test_producer_or_manifest_change_during_generation_preserves_output_and_index(self):
        self.assertEqual(self.export()[0], 0)
        for kind in ['producer', 'manifest']:
            with self.subTest(kind=kind):
                before = self.snapshots()
                original = returns.summarize
                def changing(*args, **kwargs):
                    result = original(*args, **kwargs)
                    if kind == 'producer':
                        self.producer.write_text('# version changed while generation was running\n')
                    else:
                        data = archive.read_json(self.case / 'manifest.json')
                        data['requested_period']['end'] = '2026-11-05'
                        archive.atomic_json(self.case / 'manifest.json', data)
                    return result
                with patch.object(returns, 'summarize', side_effect=changing):
                    code, _, err = self.export()
                self.assertEqual(code, 1)
                self.assertIn('changed during generation', err)
                self.assertEqual(before, self.snapshots())

    def test_late_producer_change_or_registration_failure_rolls_back(self):
        self.assertEqual(self.export()[0], 0)
        for failure in ['late-producer', 'registry-failure']:
            with self.subTest(failure=failure):
                before = self.snapshots()
                original = cases.register_artifact
                def registering(case, key, *args, **kwargs):
                    if key.startswith('returns_summary.') and failure == 'registry-failure':
                        raise ValueError('injected registration failure')
                    result = original(case, key, *args, **kwargs)
                    if key.startswith('returns_summary.') and failure == 'late-producer':
                        self.producer.write_text('# changed after output and registry write\n')
                    return result
                with patch.object(cases, 'register_artifact', side_effect=registering):
                    self.assertEqual(self.export()[0], 1)
                self.assertEqual(before, self.snapshots())

    def test_failed_first_export_removes_new_output_and_new_index(self):
        original = cases.register_artifact
        def failing(case, key, *args, **kwargs):
            if key.startswith('returns_summary.'):
                raise ValueError('registration failed')
            return original(case, key, *args, **kwargs)
        with patch.object(cases, 'register_artifact', side_effect=failing):
            self.assertEqual(self.export()[0], 1)
        self.assertFalse(self.output.exists())
        self.assertFalse((self.case / cases.STATE_FILE).exists())
        self.assertTrue((self.case / returns.LEDGER).is_file())

    def test_queries_without_output_are_read_only(self):
        (self.case / '.archive.lock').unlink()
        before = {str(p.relative_to(self.case)): p.read_bytes() for p in self.case.rglob('*') if p.is_file()}
        self.assertEqual(self.call('returns', 'summary', '--case', str(self.case))[0], 0)
        self.assertEqual(self.call('returns', 'render', '--case', str(self.case))[0], 0)
        after = {str(p.relative_to(self.case)): p.read_bytes() for p in self.case.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse((self.case / cases.STATE_FILE).exists())
        self.assertFalse((self.case / '.archive.lock').exists())

    def test_put_compatibility_registers_ledger_and_invalidates_old_summary(self):
        self.assertEqual(self.export()[0], 0)
        event_file = self.root / 'updated.json'
        event_file.write_text(json.dumps({**self.event, 'value': '2'}))
        code, out, _ = self.call('returns', 'put', '--case', str(self.case), '--file', str(event_file), '--replace')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['changed'], ['dividend-2024'])
        states = cases.artifact_states(self.case)
        self.assertEqual(states['returns']['status'], 'fresh')
        self.assertEqual(next(v for k,v in states.items() if k.startswith('returns_summary.'))['status'], 'stale')
        self.assertEqual(self.export()[0], 0)
        self.assertEqual(json.loads(self.output.read_text())['groups'][0]['total'], '2')

    def test_producer_manifest_and_alternative_source_changes_propagate_stale(self):
        self.assertEqual(self.export()[0], 0)
        self.producer.write_text('# newer rule\n')
        states = cases.artifact_states(self.case)
        self.assertEqual(next(v for k,v in states.items() if k.startswith('returns_summary.'))['status'], 'stale')
        self.assertEqual(self.export()[0], 0)
        manifest = archive.read_json(self.case / 'manifest.json')
        manifest['requested_period']['end'] = '2026-11-05'
        archive.atomic_json(self.case / 'manifest.json', manifest)
        states = cases.artifact_states(self.case)
        self.assertEqual(next(v for k,v in states.items() if k.startswith('returns_summary.'))['status'], 'stale')
        self.assertEqual(self.export()[0], 0)
        (self.case / 'raw/pdf/alternative.pdf').write_bytes(b'changed alternative original')
        states = cases.artifact_states(self.case)
        self.assertEqual(states['returns']['status'], 'stale')
        self.assertEqual(next(v for k,v in states.items() if k.startswith('returns_summary.'))['status'], 'stale')

    def test_protected_and_registered_inputs_are_checked_before_generation(self):
        input_file = self.case / 'workpapers/custom-input.json'
        archive.atomic_json(input_file, {'value': 1})
        cases.register_artifact(self.case, 'custom_input', input_file)
        targets = [self.case / 'manifest.json', self.case / cases.STATE_FILE,
                   self.case / returns.LEDGER, self.case / 'raw/pdf/direct.pdf', input_file]
        for target in targets:
            with self.subTest(target=target):
                before = target.read_bytes()
                with patch.object(returns, 'summarize', side_effect=AssertionError('preflight must happen first')):
                    code, _, _ = self.export(output=target)
                self.assertEqual(code, 1)
                self.assertEqual(before, target.read_bytes())

    def test_unregistered_canonical_inputs_and_outputs_are_protected(self):
        for relative in [cases.CANDIDATES['adopted_facts'], cases.CANDIDATES['csv_facts'], cases.CANDIDATES['pdf_facts'],
                         cases.CANDIDATES['calculations'], cases.CANDIDATES['input_checks'], 'workpapers/numbers/calculations.md']:
            with self.subTest(path=relative):
                target = self.case / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'canonical artifact remains unchanged')
                with patch.object(returns, 'summarize', side_effect=AssertionError('preflight must protect canonical inputs')):
                    code, _, _ = self.export(output=target)
                self.assertEqual(code, 1)
                self.assertEqual(target.read_bytes(), b'canonical artifact remains unchanged')

    def test_markdown_export_and_missing_ledger_output_behavior(self):
        path = self.case / 'analysis/return-report.md'
        self.assertEqual(self.export('render', path)[0], 0)
        self.assertIn('13142225877.48', path.read_text())
        (self.case / returns.LEDGER).unlink()
        before = path.read_bytes()
        self.assertEqual(self.export('render', path)[0], 1)
        self.assertEqual(before, path.read_bytes())
        # An empty read-only query remains valid, without manufacturing payment zero.
        code, output, _ = self.call('returns', 'summary', '--case', str(self.case))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)['status'], 'empty')


if __name__ == '__main__':
    unittest.main()
