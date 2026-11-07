#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression cases for payment identity and evidence-bound return summaries."""
import copy
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import returns_ledger as ledger


class ReturnLedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.case = Path(self.tmp.name)
        (self.case / 'raw/pdf').mkdir(parents=True)
        self.source = self.case / 'raw/pdf/annual.pdf'
        self.source.write_bytes(b'%PDF-1.7\nfixture identity, content integrity only\n')
        self.digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.sid = 'pdf-' + self.digest[:16]
        manifest = {'schema_version': 1, 'company': 'Gree fixture', 'code': '000651',
                    'requested_period': {'start': '2020-01-01', 'end': '2026-11-06'},
                    'sources': [{'source_id': self.sid, 'company': 'Gree fixture', 'code': '000651', 'kind': 'pdf',
                                 'path': 'raw/pdf/annual.pdf', 'sha256': self.digest, 'validation': {'pages': 250}}]}
        (self.case / 'manifest.json').write_text(json.dumps(manifest))

    def event(self, event_id='paid', value='13142225877.48', **changes):
        item = {'event_id': event_id, 'economic_id': event_id, 'kind': 'dividend', 'stage': 'cash_paid',
                'period_basis': 'cash_payment', 'aggregation': 'single', 'start': '2024-08-28', 'end': '2024-08-28',
                'cash_date': '2024-08-28', 'earnings_year': '2023', 'payer': 'listed_parent',
                'recipient': 'listed_shareholders', 'currency': 'CNY', 'fee_basis': 'not_applicable',
                'value': value, 'precision': '0.01', 'sources': [{'source_id': self.sid, 'pages': [59], 'role': 'implementation_notice'}]}
        item.update(changes)
        return item

    def summary(self, **kwargs):
        return ledger.summarize(self.case, '2020-01-01', '2026-11-06', **kwargs)

    def test_actual_listed_dividend_not_payable_bridge_minus_minority(self):
        bridge = self.event('bridge', '13143744877.48', kind='liability_movement', stage='observed',
                            period_basis='liability_movement', payer='group', aggregation='period_total',
                            start='2024-01-01', end='2024-12-31', cash_date=None,
                            sources=[{'source_id': self.sid, 'pages': [204], 'role': 'liability_bridge'}])
        minority = self.event('minority', '35819000.00', payer='group', recipient='minority_shareholders')
        ledger.put_events(self.case, [bridge, self.event(), minority])
        summary = self.summary(recipient='listed_shareholders')
        self.assertEqual(len(summary['groups']), 1)
        self.assertEqual(summary['groups'][0]['total'], '13142225877.48')
        self.assertNotIn('grand_total', summary)
        self.assertEqual({x['event_id'] for x in summary['excluded']}, {'bridge', 'minority'})

    def test_plan_or_liability_evidence_cannot_establish_cash_paid(self):
        for role in ['plan_notice', 'liability_bridge']:
            with self.subTest(role=role), self.assertRaises(ValueError):
                ledger.put_events(self.case, self.event(sources=[{'source_id': self.sid, 'pages': [2], 'role': role}]))
        self.assertFalse((self.case / ledger.LEDGER).exists())

    def test_cross_year_plan_cumulative_is_not_current_year_payment(self):
        plan = self.event('plan', '2999933749.57', kind='market_repurchase', recipient='selling_shareholders',
                          stage='executed', period_basis='plan_execution', aggregation='plan_total', fee_basis='excluded',
                          start='2025-12-31', end='2026-05-27', cash_date=None)
        payment = self.event('cash2026', '2880061147.37', kind='market_repurchase', recipient='selling_shareholders',
                             aggregation='period_total', fee_basis='included', start='2026-01-01', end='2026-06-30', cash_date=None)
        ledger.put_events(self.case, [plan, payment])
        cash = ledger.summarize(self.case, '2026-01-01', '2026-11-06')
        self.assertEqual(cash['groups'][0]['total'], '2880061147.37')
        execution = self.summary(measure='execution')
        self.assertEqual(execution['groups'][0]['total'], '2999933749.57')
        self.assertEqual(execution['groups'][0]['fee_basis'], 'excluded')
        with self.assertRaises(ValueError):
            ledger.put_events(self.case, {**plan, 'stage': 'cash_paid', 'period_basis': 'cash_payment'})

    def test_scope_currency_and_fee_are_separate_groups(self):
        items = [self.event('cny'), self.event('usd', '10', currency='USD'),
                 self.event('minority', '50', payer='subsidiary', payer_entity='Example subsidiary', recipient='minority_shareholders'),
                 self.event('buy-inc', '100', kind='market_repurchase', recipient='selling_shareholders', fee_basis='included'),
                 self.event('buy-exc', '101', kind='market_repurchase', recipient='selling_shareholders', fee_basis='excluded')]
        ledger.put_events(self.case, items)
        summary = self.summary()
        self.assertEqual(len(summary['groups']), 5)
        self.assertNotIn('total', summary)
        self.assertEqual({x['currency'] for x in summary['groups']}, {'CNY', 'USD'})

    def test_distinct_subsidiary_entities_do_not_merge_or_conflict(self):
        events = [self.event('sub-a', '10', payer='subsidiary', payer_entity='subsidiary-a', recipient='minority_shareholders', aggregation='period_total', start='2024-01-01', end='2024-12-31', cash_date=None),
                  self.event('sub-b', '20', payer='subsidiary', payer_entity='subsidiary-b', recipient='minority_shareholders', aggregation='period_total', start='2024-01-01', end='2024-12-31', cash_date=None)]
        ledger.put_events(self.case, events)
        self.assertEqual({x['total'] for x in self.summary()['groups']}, {'10', '20'})
        with self.assertRaises(ValueError):
            ledger.put_events(self.case, self.event('missing-sub-name', payer='subsidiary'))

    def test_unknown_is_not_zero_and_known_subtotal_is_not_complete(self):
        first = self.event('unknown', None, unknown_reason='No note disclosing this payment')
        second = self.event('known-zero', '0', start='2025-01-01', end='2025-01-01', cash_date='2025-01-01')
        ledger.put_events(self.case, [first, second])
        bucket = self.summary()['groups'][0]
        self.assertEqual(bucket['known_subtotal'], '0')
        self.assertIsNone(bucket['total'])
        self.assertFalse(bucket['complete'])
        self.assertEqual(bucket['unknown_event_ids'], ['unknown'])

    def test_empty_ineligible_and_unknown_amount_aggregation_states(self):
        empty = self.summary()
        self.assertEqual(empty['status'], 'empty')
        self.assertEqual(empty['aggregation_status'], 'no_eligible_events')
        self.assertEqual(empty['eligible_events'], 0)
        declared = self.event('declaration', stage='declared', period_basis='earnings_year')
        ledger.put_events(self.case, declared)
        ineligible = self.summary()
        self.assertEqual(ineligible['status'], 'ready')
        self.assertEqual(ineligible['groups'], [])
        self.assertEqual(ineligible['aggregation_status'], 'no_eligible_events')
        self.assertEqual(ineligible['eligible_events'], 0)
        ledger.put_events(self.case, self.event('unknown-payment', None, unknown_reason='Amount not disclosed'))
        incomplete = self.summary()
        self.assertEqual(incomplete['aggregation_status'], 'amount_incomplete')
        self.assertEqual(incomplete['eligible_events'], 1)
        self.assertIsNone(incomplete['groups'][0]['total'])
        ledger.put_events(self.case, self.event('unknown-payment', '10'), replace=True)
        recorded = self.summary()
        self.assertEqual(recorded['aggregation_status'], 'recorded_events_aggregated')
        self.assertEqual(recorded['eligible_events'], 1)

    def test_duplicate_economic_events_rejected_atomically(self):
        first = self.event()
        second = self.event('other-id', economic_id='paid')
        with self.assertRaises(ValueError):
            ledger.put_events(self.case, [first, second])
        self.assertFalse((self.case / ledger.LEDGER).exists())

    def test_period_total_and_components_cannot_double_count(self):
        total = self.event('annual-total', aggregation='period_total', start='2024-01-01', end='2024-12-31', cash_date=None)
        with self.assertRaises(ValueError):
            ledger.put_events(self.case, [total, self.event('component')])
        self.assertFalse((self.case / ledger.LEDGER).exists())

    def test_idempotent_put_and_reviewed_replacement_retains_history(self):
        event = self.event()
        ledger.put_events(self.case, event)
        before = (self.case / ledger.LEDGER).read_bytes()
        result = ledger.put_events(self.case, event)
        self.assertEqual(result['unchanged'], ['paid'])
        self.assertEqual(before, (self.case / ledger.LEDGER).read_bytes())
        updated = {**event, 'value': '13142225877.49', 'explanation': 'reconciled 1 cent difference'}
        with self.assertRaises(ValueError):
            ledger.put_events(self.case, updated)
        ledger.put_events(self.case, updated, replace=True)
        self.assertEqual(ledger.load_ledger(self.case)['revisions'][0]['event']['value'], '13142225877.48')
        self.assertEqual(self.summary()['groups'][0]['total'], '13142225877.49')

    def test_hash_binding_then_source_mutation_is_invalid(self):
        ledger.put_events(self.case, self.event())
        source = ledger.load_ledger(self.case)['events'][0]['sources'][0]
        self.assertEqual(source['sha256'], self.digest)
        self.source.write_bytes(b'changed original')
        self.assertEqual(ledger.validate_ledger(self.case)['status'], 'invalid')
        self.assertEqual(self.summary()['status'], 'invalid')

    def test_wrong_hash_and_out_of_range_page_fail(self):
        for source in [{'source_id': self.sid, 'sha256': '0' * 64, 'pages': [1], 'role': 'implementation_notice'},
                       {'source_id': self.sid, 'pages': [251], 'role': 'implementation_notice'},
                       {'source_id': self.sid, 'pages': [True], 'role': 'implementation_notice'}]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                ledger.put_events(self.case, self.event(sources=[source]))

    def test_date_window_is_not_prorated_and_future_cash_rejected(self):
        annual = self.event('annual', aggregation='period_total', start='2024-01-01', end='2024-12-31', cash_date=None)
        ledger.put_events(self.case, annual)
        result = ledger.summarize(self.case, '2024-07-01', '2024-12-31')
        self.assertEqual(result['groups'], [])
        self.assertEqual(result['excluded'][0]['reason'], 'partial_overlap_not_prorated')
        future = self.event('future', start='2027-01-01', end='2027-01-01', cash_date='2027-01-01')
        with self.assertRaises(ValueError):
            ledger.put_events(self.case, future)

    def test_uncertain_evidence_excluded_and_alternative_is_traceable(self):
        event = self.event(evidence_status='conflicting', explanation='implementation vs CF difference not bridged',
                           alternatives=[{'value': '13107925877.48', 'source': {'source_id': self.sid, 'pages': [204], 'role': 'liability_bridge'}, 'reason': 'incomplete payable bridge'}],
                           adoption_reason='Keep selected direct implementation amount with unresolved scope difference')
        ledger.put_events(self.case, event)
        self.assertEqual(self.summary()['groups'], [])
        self.assertEqual(self.summary()['excluded'][0]['reason'], 'evidence_conflicting')
        self.assertEqual(ledger.load_ledger(self.case)['events'][0]['alternatives'][0]['source']['sha256'], self.digest)

    def test_mixed_financing_and_treasury_changes_are_not_cash_returns(self):
        events = [self.event('mixed', '500', kind='mixed_financing', stage='observed', period_basis='cash_payment'),
                  self.event('transfer', '100', kind='treasury_transfer', stage='executed', period_basis='noncash_change'),
                  self.event('cancel', '100', kind='cancellation', stage='executed', period_basis='noncash_change')]
        ledger.put_events(self.case, events)
        self.assertEqual(self.summary()['groups'], [])
        with self.assertRaises(ValueError):
            ledger.put_events(self.case, {**events[0], 'stage': 'cash_paid'})

    def test_unknown_scope_and_fee_do_not_become_unqualified_total(self):
        unknown = self.event('who', payer='unknown')
        fee = self.event('fee', '10', kind='market_repurchase', recipient='selling_shareholders', fee_basis='unknown')
        ledger.put_events(self.case, [unknown, fee])
        self.assertEqual(len(self.summary()['groups']), 1)
        self.assertFalse(self.summary()['groups'][0]['fee_comparable'])

    def test_invalid_exact_decimal_and_currency_are_rejected(self):
        for changes in [{'value': 1.23}, {'value': '-1'}, {'value': 'NaN'}, {'precision': '-0.01'}, {'currency': '人民币'}, {'value': None}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ledger.put_events(self.case, self.event(**changes))

    def test_derived_export_cannot_overwrite_authority_or_raw_source(self):
        ledger.put_events(self.case, self.event())
        before = (self.case / ledger.LEDGER).read_bytes()
        for target in [self.case / ledger.LEDGER, self.case / 'manifest.json', self.source, self.case / 'raw/tables/new.json']:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                result = ledger.main(['summary', '--case', str(self.case), '--output', str(target)])
            self.assertEqual(result, 1)
        self.assertEqual(before, (self.case / ledger.LEDGER).read_bytes())

    def test_cli_and_markdown_preserve_code_leading_zero(self):
        event_file = self.case / 'event.json'
        event_file.write_text(json.dumps(self.event()))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(ledger.main(['put', '--case', str(self.case), '--file', str(event_file)]), 0)
        self.assertEqual(ledger.load_ledger(self.case)['company']['code'], '000651')
        rendered = ledger.markdown(self.summary())
        self.assertIn('13142225877.48', rendered)
        self.assertIn('CNY', rendered)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(ledger.main(['summary', '--case', str(self.case), '--start', '2024-01-01', '--end', '2024-12-31']), 0)
        self.assertEqual(json.loads(out.getvalue())['groups'][0]['total'], '13142225877.48')


if __name__ == '__main__':
    unittest.main()
