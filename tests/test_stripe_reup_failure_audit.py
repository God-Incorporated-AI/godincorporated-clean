"""A7C5C: signed-but-failed additional Stripe invoice audit simulations.

Uses in-memory transactional cursors only. Never connects to Stripe or Postgres.
"""
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import unittest
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'services/stripe_reup_settlement.py'
spec = importlib.util.spec_from_file_location('a7c5c_failure_audit', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

UID = '24d20b0d-c073-466e-a010-08c3db27b6d1'
SUB_ROW = '52d0a254-a8a0-4ae0-a0dc-bb1e38d264ba'
INTENT = 'e1cdb624-ff3b-429c-95df-4ad279e0f4ef'
RECORD = '7f0e2a80-ad60-4e3a-ac54-95d5b6ea6d40'
EVENT_ROW = '1790e2b1-b07f-4b03-afba-0b7612586c73'
INVOICE = 'in_a7c5c_1'
S_ID = 'sub_a7c5c_1'
CUS = 'cus_a7c5c_1'
T = int(dt.datetime(2026, 10, 22, 20, 0, tzinfo=dt.timezone.utc).timestamp())


def event(event_id='evt_a7c5c_1', inv_id=INVOICE):
    return {
        'id': event_id, 'type': 'invoice.payment_failed', 'created': T,
        'livemode': False, 'api_version': '2024-04-10',
        'data': {'object': {
            'object': 'invoice', 'id': inv_id, 'customer': CUS,
            'subscription': S_ID, 'livemode': False,
            'status': 'open', 'paid': False,
            'billing_reason': 'subscription_update',
            'parent': {'subscription_details': {
                'subscription': S_ID,
                'metadata': {'change_source': 'phase8_paid_reup',
                             'reup_intent_id': INTENT, 'user_id': UID},
            }},
        }},
    }


def intent():
    return {
        'id': UUID(RECORD), 'user_id': UUID(UID), 'subscription_id': UUID(SUB_ROW),
        'provider_invoice_id': None, 'status': 'pending', 'livemode': False,
        'raw_summary_json': {
            'schema': 'stripe_paid_reup_intent.v1', 'reup_intent_id': INTENT,
            'dispatch_status': 'sent', 'user_id': UID,
            'subscription_row_id': SUB_ROW,
            'stripe_subscription_id': S_ID, 'customer_id': CUS,
            'stripe_latest_invoice_id': INVOICE, 'livemode': False,
        },
    }


def receipt():
    d = intent()
    d.update(provider_invoice_id=INVOICE, status='succeeded',
             raw_summary_json={
                 'schema': 'stripe_paid_reup_receipt.v2',
                 'invoice_id': INVOICE, 'intent_id': INTENT,
                 'stripe_subscription_id': S_ID,
                 'customer_id': CUS, 'user_id': UID, 'livemode': False,
             })
    return d


def baseline():
    return {'ledger': [intent()], 'events': {}, 'user': {'paid_period_start': T - 12345},
            'subscription': {'renews_at': T + 86400}}


class DB:
    def __init__(self, state=None, fail_sql=None):
        self.data = copy.deepcopy(state if state is not None else baseline())
        self.fail_sql = fail_sql
        self.conns = []
        self.log = []

    def connect(self):
        conn = Connection(self)
        self.conns.append(conn)
        return conn


class Connection:
    def __init__(self, db):
        self.db = db
        self.work = copy.deepcopy(db.data)
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self): return Cursor(self)

    def commit(self):
        self.commits += 1
        self.db.data = copy.deepcopy(self.work)

    def rollback(self): self.rollbacks += 1

    def close(self): self.closed = True


class Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.results = []
        self.rowcount = 1

    def __enter__(self): return self
    def __exit__(self, *_): return False
    def fetchall(self): return list(self.results)
    def fetchone(self): return self.results[0] if self.results else None

    def execute(self, sql, args=()):
        q = ' '.join(sql.split())
        db = self.conn.db
        db.log.append(q)
        if db.fail_sql and db.fail_sql in q:
            raise RuntimeError('Injected SQL failure at ' + db.fail_sql)
        s = self.conn.work
        self.results, self.rowcount = [], 1
        if 'pg_advisory_xact_lock' in q:
            assert len(args) == 1 and args[0].startswith('godinc:stripe:paid-reup:in_')
        elif q.startswith('SELECT') and 'FROM billing_transactions' in q:
            if 'provider_invoice_id = %s' in q:
                self.results = [r for r in s['ledger'] if r['provider_invoice_id'] == args[0]]
            elif "raw_summary_json ->> 'reup_intent_id'" in q:
                self.results = [r for r in s['ledger'] if r['raw_summary_json'].get('reup_intent_id') == args[0]]
            else:
                raise AssertionError('Unexpected ledger query ' + q)
        elif q.startswith('SELECT') and 'FROM payment_events' in q:
            if 'transaction_id = %s::uuid' in q:
                self.results = [r for r in s['events'].values() if str(r.get('transaction_id')) == args[0]
                                and r['event_type'] == 'invoice.payment_failed']
            elif 'provider_event_id = %s' in q:
                self.results = [s['events'][args[0]]] if args[0] in s['events'] else []
            else:
                raise AssertionError('Unexpected event SELECT ' + q)
        elif q.startswith('INSERT INTO payment_events'):
            key, same, timestamp, invoice_id, user, sub, livemode, api_version, payload = args
            assert key == same and timestamp.tzinfo and invoice_id.startswith('in_')
            if key not in s['events']:
                s['events'][key] = {
                    'id': UUID(EVENT_ROW), 'event_type': 'invoice.payment_failed',
                    'object_id': invoice_id, 'user_id': UUID(user),
                    'subscription_id': UUID(sub), 'livemode': livemode,
                    'transaction_id': None, 'processing_status': 'received',
                    'summary': json.loads(payload),
                }
        elif q.startswith('UPDATE payment_events'):
            rid, sub, helper, event_id = args
            assert event_id in s['events']
            s['events'][event_id].update(transaction_id=UUID(rid), subscription_id=UUID(sub),
                                         helper_name=helper, processing_status='processed')
        else:
            raise AssertionError('Unexpected SQL: ' + q)


class FailedInvoiceAuditTests(unittest.TestCase):
    def setUp(self):
        self.db = DB()
        self.e = event()

    def audit(self, e=None, db=None):
        return module.audit_failed_stripe_reup_invoice(
            e if e is not None else self.e, connect=(db or self.db).connect, expected_livemode=False)

    def assert_rejected_unchanged(self, e=None, db=None):
        db = db or self.db
        before = copy.deepcopy(db.data)
        with self.assertRaises((ValueError, RuntimeError)):
            self.audit(e=e, db=db)
        self.assertEqual(db.data, before)
        for conn in db.conns:
            self.assertTrue(conn.closed)

    def test_failed_additional_payment_one_commit_no_entitlement_change(self):
        previous = copy.deepcopy(self.db.data)
        result = self.audit()
        self.assertEqual(result['status'], 'failed_attempt_audited')
        self.assertEqual(result['purchase_held'], True)
        self.assertEqual(self.db.data['ledger'], previous['ledger'])
        self.assertEqual(self.db.data['user'], previous['user'])
        self.assertEqual(self.db.data['subscription'], previous['subscription'])
        self.assertEqual(self.db.conns[0].commits, 1)
        self.assertTrue(self.db.conns[0].closed)
        self.assertEqual(len(self.db.data['events']), 1)
        a = self.db.data['events'][self.e['id']]
        self.assertEqual(a['event_type'], 'invoice.payment_failed')
        self.assertEqual(a['summary']['schema'], 'stripe_failed_reup_event.v1')
        self.assertEqual(str(a['transaction_id']), RECORD)
        self.assertEqual(a['helper_name'], 'audit_failed_stripe_reup_invoice')
        self.assertFalse(any('UPDATE users' in q or 'UPDATE subscriptions' in q
                             or 'UPDATE billing_transactions' in q for q in self.db.log))

    def test_duplicate_same_event_is_idempotent(self):
        self.audit()
        before = copy.deepcopy(self.db.data)
        self.audit()
        self.assertEqual(self.db.data, before)
        self.assertEqual(len(self.db.data['events']), 1)

    def test_second_failed_event_same_invoice_idempotent(self):
        self.audit()
        second = event(event_id='evt_a7c5c_2')
        self.assertEqual(self.audit(second)['status'], 'failed_attempt_audited')
        self.assertEqual(len(self.db.data['events']), 2)
        self.assertEqual(str(self.db.data['events'][second['id']]['transaction_id']), RECORD)

    def test_paid_later_can_still_use_same_pending_intent(self):
        before = copy.deepcopy(self.db.data['ledger'])
        self.audit()
        self.assertEqual(self.db.data['ledger'], before)
        self.assertEqual(self.db.data['ledger'][0]['raw_summary_json']['dispatch_status'], 'sent')

    def test_late_failure_after_success_receipt_is_audited(self):
        self.db.data['ledger'] = [receipt()]
        before = copy.deepcopy(self.db.data['ledger'])
        result = self.audit()
        self.assertEqual(result['status'], 'historical_failure_audited')
        self.assertEqual(result['purchase_held'], False)
        self.assertEqual(self.db.data['ledger'], before)
        self.assertEqual(self.db.data['user']['paid_period_start'], T - 12345)

    def test_historical_refund_remains_noop(self):
        row = receipt(); row['status'] = 'refunded'
        self.db.data['ledger'] = [row]
        self.assertEqual(self.audit()['status'], 'historical_failure_audited')

    def test_missing_durable_intent_fail_closed(self):
        self.db.data['ledger'] = []
        self.assert_rejected_unchanged()

    def test_multiple_matching_intents_fail_closed(self):
        self.db.data['ledger'].append(intent())
        self.assert_rejected_unchanged()

    def test_prepared_intent_cannot_claim_payment_failure(self):
        self.db.data['ledger'][0]['raw_summary_json']['dispatch_status'] = 'prepared'
        self.assert_rejected_unchanged()

    def test_dispatching_or_uncertain_can_be_audited(self):
        for state in ('dispatching', 'uncertain'):
            db = DB(); db.data['ledger'][0]['raw_summary_json']['dispatch_status'] = state
            self.assertEqual(self.audit(db=db)['status'], 'failed_attempt_audited')

    def test_wrong_customer_fails_closed(self):
        self.e['data']['object']['customer'] = 'cus_wrong'
        self.assert_rejected_unchanged()

    def test_wrong_subscription_fails_closed(self):
        inv=self.e['data']['object']; inv['subscription'] = inv['parent']['subscription_details']['subscription'] = 'sub_wrong'
        self.assert_rejected_unchanged()

    def test_wrong_account_fails_closed(self):
        self.e['data']['object']['parent']['subscription_details']['metadata']['user_id'] = UID[:-1]+'2'
        self.assert_rejected_unchanged()

    def test_wrong_intent_fails_closed(self):
        self.e['data']['object']['parent']['subscription_details']['metadata']['reup_intent_id'] = 'd31382a2-d152-4714-b725-6f7eed7b3be7'
        self.assert_rejected_unchanged()

    def test_wrong_livemode_fails_closed(self):
        self.e['livemode'] = True
        self.assert_rejected_unchanged()

    def test_wrong_invoice_livemode_fails_closed(self):
        self.e['data']['object']['livemode'] = True
        self.assert_rejected_unchanged()

    def test_paid_event_is_not_failed_audit(self):
        self.e['type'] = 'invoice.paid'
        self.assert_rejected_unchanged()

    def test_invoice_marked_paid_not_failed_audit(self):
        self.e['data']['object'].update(paid=True, status='paid')
        self.assert_rejected_unchanged()

    def test_bad_event_id_fail_closed(self):
        self.e['id'] = 'invalid'
        self.assert_rejected_unchanged()

    def test_bad_event_timestamp_fail_closed(self):
        self.e['created'] = None
        self.assert_rejected_unchanged()

    def test_wrong_latest_invoice_refuses(self):
        self.db.data['ledger'][0]['raw_summary_json']['stripe_latest_invoice_id'] = 'in_other'
        self.assert_rejected_unchanged()

    def test_different_failed_invoice_for_intent_refuses(self):
        self.audit()
        self.db.data['ledger'][0]['raw_summary_json']['stripe_latest_invoice_id'] = None
        self.e = event(event_id='evt_a7c5c_2', inv_id='in_a7c5c_2')
        # This call must fail on prior ledger-linked failure event, not simply
        # because an invoice can be reclassified as a new purchase.
        self.assert_rejected_unchanged()

    def test_existing_event_collision_refuses(self):
        self.db.data['events'][self.e['id']] = {
            'id': UUID(EVENT_ROW), 'event_type': 'invoice.paid', 'object_id': INVOICE,
            'user_id': UUID(UID), 'subscription_id': UUID(SUB_ROW),
            'livemode': False, 'transaction_id': None, 'processing_status': 'received'}
        self.assert_rejected_unchanged()

    def test_existing_processed_event_without_receipt_refuses(self):
        self.db.data['events'][self.e['id']] = {
            'id': UUID(EVENT_ROW), 'event_type': 'invoice.payment_failed', 'object_id': INVOICE,
            'user_id': UUID(UID), 'subscription_id': UUID(SUB_ROW),
            'livemode': False, 'transaction_id': None, 'processing_status': 'processed'}
        self.assert_rejected_unchanged()

    def test_failure_mid_write_rolls_back(self):
        for sql in ('INSERT INTO payment_events', 'SELECT id, event_type', 'UPDATE payment_events'):
            with self.subTest(sql=sql):
                db=DB(fail_sql=sql)
                before=copy.deepcopy(db.data)
                self.assert_rejected_unchanged(db=db)
                self.assertEqual(db.data, before)
                self.assertEqual(db.conns[0].rollbacks,1)

    def test_no_provider_sql_entitlement_or_second_payment_store(self):
        self.audit()
        self.assertEqual(len(self.db.conns),1)
        all_sql=' '.join(self.db.log)
        self.assertNotIn('UPDATE users',all_sql)
        self.assertNotIn('UPDATE subscriptions',all_sql)
        self.assertNotIn('INSERT INTO billing_transactions',all_sql)
        self.assertNotIn('UPDATE billing_transactions',all_sql)
        source=SOURCE.read_text('utf8')
        self.assertNotIn('import stripe',source)
        self.assertNotIn('import psycopg2',source)


if __name__ == '__main__':
    unittest.main(verbosity=2)
