"""Offline transaction tests for dormant A7C3. Only simulated DB, no provider."""
import ast
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import unittest
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'services/stripe_reup_settlement.py'
STRIPE = ROOT / 'services/stripe_billing.py'
spec = importlib.util.spec_from_file_location('stripe_a7c3_under_test', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
ast_tree = ast.parse(STRIPE.read_text('utf8'))
functions = [x for x in ast_tree.body if isinstance(x, ast.FunctionDef)
             and x.name == 'validate_paid_stripe_reup_invoice']
assert len(functions) == 1
scope = {}
exec(compile(ast.Module(body=functions, type_ignores=[]), str(STRIPE), 'exec'), scope)
validator = scope['validate_paid_stripe_reup_invoice']

NOW = dt.datetime(2026, 10, 22, 18, 0, tzinfo=dt.timezone.utc)
START = int((NOW - dt.timedelta(minutes=2)).timestamp())
OLD = START - 17 * 86400
END = START + 30 * 86400
INTENT = '763c3656-aabc-4224-bd48-98765e007faa'
OTHER_INTENT = '54100d99-d62d-42e1-93de-75057f33708e'
USER = '11111111-2222-3333-4444-555555555555'
SUB = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
BC = 'bbbbbbbb-cccc-dddd-eeee-ffffffffffff'
TRANS = 'cccccccc-dddd-eeee-ffff-000000000000'
EVROW = 'dddddddd-eeee-ffff-0000-111111111111'


def invoice(event_id='evt_1', ident='in_1', period_start=START, period_end=END,
            intent_id=INTENT):
    payload = dict(
        id=ident, object='invoice', status='paid', paid=True, paid_out_of_band=False,
        billing_reason='subscription_update', livemode=False, currency='usd',
        customer='cus_1', subscription='sub_1',
        subscription_details={'metadata': {
            'user_id': USER, 'change_source': 'phase8_paid_reup',
            'reup_intent_id': intent_id,
        }},
        total=199, amount_paid=199, amount_remaining=0,
        lines={'has_more': False, 'data': [{
            'amount': 199, 'price': {'id': 'price_1'},
            'period': {'start': period_start, 'end': period_end},
        }]},
    )
    return dict(id=event_id, type='invoice.paid', created=START,
                livemode=False, api_version='2024-04-10', data={'object': payload})


def pending_intent(intent_id=INTENT):
    return dict(id=UUID(TRANS), user_id=UUID(USER), subscription_id=UUID(SUB),
                provider_invoice_id=None, invoice_id=None, status='pending',
                gross_amount_cents=199, livemode=False,
                raw_summary_json={
                    'schema': 'stripe_paid_reup_intent.v1',
                    'reup_intent_id': intent_id,
                    'dispatch_status': 'sent',
                    'user_id': USER,
                    'subscription_row_id': SUB,
                    'stripe_subscription_id': 'sub_1',
                    'livemode': False,
                    'plan_code': 'seeker',
                    'price_id': 'price_1',
                    'customer_id': 'cus_1',
                    'previous_period_start': OLD,
                    'expected_paid_cents': 199,
                })


def seed_state():
    return dict(
        subscription=dict(id=UUID(SUB), user_id=UUID(USER),
                          billing_customer_id=UUID(BC), plan_code='seeker',
                          support_mode='monthly_recurring', provider='stripe',
                          stripe_subscription_id='sub_1', stripe_price_id='price_1',
                          provider_status='active', internal_status='active', livemode=False),
        user=dict(id=UUID(USER), plan_code='seeker', entitlement_status='active',
                  current_period_started_at=dt.datetime.fromtimestamp(OLD, dt.timezone.utc),
                  subscription_expires_at=dt.datetime.fromtimestamp(OLD+30*86400, dt.timezone.utc)),
        customer=dict(id=UUID(BC), user_id=UUID(USER), stripe_customer_id='cus_1',
                      provider_customer_id='cus_1', livemode=False),
        price=dict(stripe_price_id='price_1', plan_code='seeker',
                   support_mode='monthly_recurring', livemode=False),
        catalog=dict(monthly_price_cents=199, monthly_enabled=True, active=True),
        events={}, transactions={'intent:' + INTENT: pending_intent()},
    )


class FakeDB:
    def __init__(self, state=None, fail_on=None):
        self.committed = copy.deepcopy(state) if state is not None else seed_state()
        self.fail_on = fail_on
        self.connections = []
        self.sql_log = []

    def connect(self):
        conn = FakeConnection(self)
        self.connections.append(conn)
        return conn


class FakeConnection:
    def __init__(self, db):
        self.db = db
        self.data = copy.deepcopy(db.committed)
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self): return FakeCursor(self)
    def commit(self):
        self.db.committed = copy.deepcopy(self.data)
        self.commits += 1
    def rollback(self): self.rollbacks += 1
    def close(self): self.closed = True


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.one = None
        self.many = None
        self.rowcount = 1
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def fetchone(self): return self.one
    def fetchall(self): return self.many

    def execute(self, sql, args=None):
        norm = ' '.join(sql.split())
        self.conn.db.sql_log.append(norm)
        if self.conn.db.fail_on and self.conn.db.fail_on in norm:
            raise RuntimeError('Injected failure: ' + self.conn.db.fail_on)
        data = self.conn.data
        self.one, self.many, self.rowcount = None, None, 1
        if 'pg_advisory_xact_lock' in norm:
            assert args[0].startswith('godinc:stripe:paid-reup:in_')
        elif norm.startswith('SELECT') and 'FROM billing_transactions' in norm:
            if 'provider_invoice_id = %s' in norm:
                self.many = [v for v in data['transactions'].values()
                             if v['invoice_id'] == args[0]]
            elif "raw_summary_json ->> 'reup_intent_id'" in norm:
                self.many = [v for v in data['transactions'].values()
                             if (json.loads(v['raw_summary_json']) if isinstance(v['raw_summary_json'], str)
                                 else v['raw_summary_json']).get('reup_intent_id') == args[0]]
            else:
                raise AssertionError('Unknown invoice ledger SELECT')
        elif norm.startswith('SELECT') and 'FROM subscriptions' in norm:
            self.one = data['subscription'] if args[0] == 'sub_1' else None
        elif norm.startswith('SELECT') and 'FROM users' in norm:
            self.one = data['user'] if args[0] == USER else None
        elif norm.startswith('SELECT') and 'FROM billing_customers' in norm:
            self.one = data['customer'] if args[0] == USER else None
        elif norm.startswith('SELECT') and 'FROM stripe_price_map' in norm:
            self.one = data['price'] if args[0] == 'price_1' else None
        elif norm.startswith('SELECT') and 'FROM plan_catalog' in norm:
            self.one = data['catalog'] if args[0] == 'seeker' else None
        elif norm.startswith('UPDATE billing_transactions'):
            inv_id, inv2, event_id, amount, total, when, summary, row_id = args
            assert inv_id == inv2 and row_id == TRANS
            # In reality this WHERE has status=pending and RETURNING id.
            candidates = [(k, v) for k, v in data['transactions'].items()
                          if str(v['id']) == row_id and v['status'] == 'pending']
            if candidates:
                key, entry = candidates[0]
                del data['transactions'][key]
                entry.update(status='succeeded', invoice_id=inv_id,
                             provider_invoice_id=inv_id, gross_amount_cents=amount,
                             raw_summary_json=summary)
                data['transactions'][inv_id] = entry
                self.one = {'id': entry['id']}
            else:
                self.rowcount = 0
        elif norm.startswith('UPDATE subscriptions'):
            start, end, inv, at, sub_id = args
            assert sub_id == SUB
            data['subscription'].update(current_period_start=start,
                                        current_period_end=end, latest_invoice_id=inv)
        elif norm.startswith('UPDATE users'):
            plan, base, start, next_renew, expires, last_paid, offer, uid = args
            assert uid == USER
            data['user'].update(plan_code=plan, current_period_started_at=start,
                                subscription_expires_at=expires, entitlement_status='active')
        elif norm.startswith('INSERT INTO payment_events'):
            event_id, _, created, invoice_id, uid, subrow, live, api, summary = args
            if event_id not in data['events']:
                data['events'][event_id] = dict(id=UUID(EVROW), event_type='invoice.paid',
                    object_id=invoice_id, user_id=UUID(uid), subscription_id=UUID(subrow),
                    livemode=live, processing_status='received', transaction_id=None)
        elif norm.startswith('SELECT') and 'FROM payment_events' in norm:
            self.one = data['events'].get(args[0])
        elif norm.startswith('UPDATE payment_events'):
            tr, su, helper, event_id = args
            data['events'][event_id].update(processing_status='processed',
                transaction_id=UUID(tr), subscription_id=UUID(su), helper_name=helper)
        else:
            raise AssertionError('Unrecognized mock SQL: ' + norm)


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.event = invoice()

    def run_settlement(self, event=None, db=None, now=NOW):
        db = db or self.db
        return module.settle_verified_paid_reup(
            event if event is not None else self.event,
            connect=db.connect, validate_invoice=validator,
            expected_livemode=False, now=now)

    def assert_unchanged(self, before):
        self.assertEqual(self.db.committed, before)
        self.assertTrue(self.db.connections[-1].closed)
        self.assertEqual(self.db.connections[-1].rollbacks, 1)
        self.assertEqual(self.db.connections[-1].commits, 0)

    def test_paid_event_one_commit_one_replenishment(self):
        result = self.run_settlement()
        self.assertEqual(result['status'], 'settled')
        self.assertEqual(result['paid_cents'], 199)
        self.assertEqual(len(self.db.committed['transactions']), 1)
        receipt = self.db.committed['transactions']['in_1']
        self.assertEqual(receipt['status'], 'succeeded')
        self.assertEqual(receipt['id'], UUID(TRANS))
        self.assertEqual(self.db.committed['user']['current_period_started_at'],
                         dt.datetime.fromtimestamp(START, dt.timezone.utc))
        self.assertEqual(self.db.committed['events']['evt_1']['processing_status'], 'processed')
        self.assertEqual(self.db.connections[0].commits, 1)
        self.assertTrue(self.db.connections[0].closed)
        self.assertEqual(len([q for q in self.db.sql_log if q.startswith('UPDATE users')]), 1)
        self.assertEqual(len([q for q in self.db.sql_log if q.startswith('UPDATE billing_transactions')]), 1)

    def test_repeated_same_event_is_duplicate(self):
        self.assertEqual(self.run_settlement()['status'], 'settled')
        self.assertEqual(self.run_settlement()['status'], 'duplicate')
        self.assertEqual(len([q for q in self.db.sql_log if q.startswith('UPDATE users')]), 1)

    def test_second_event_same_invoice_is_duplicate(self):
        self.run_settlement()
        self.assertEqual(self.run_settlement(invoice(event_id='evt_2'))['status'], 'duplicate')
        self.assertEqual(len(self.db.committed['transactions']), 1)
        self.assertEqual(self.db.committed['events']['evt_2']['processing_status'], 'processed')

    def test_duplicate_after_expiry_and_changed_tier(self):
        self.run_settlement()
        self.db.committed['user'].update(plan_code='magister', entitlement_status='expired',
            current_period_started_at=NOW + dt.timedelta(days=50))
        self.db.committed['subscription'].update(provider_status='canceled',
            internal_status='cancelled', plan_code='magister', stripe_price_id='price_new')
        self.db.committed['price']['stripe_price_id'] = 'price_new'
        self.db.committed['catalog']['active'] = False
        self.assertEqual(self.run_settlement(invoice(event_id='evt_9'),
            now=NOW+dt.timedelta(days=180))['status'], 'duplicate')
        self.assertEqual(len([q for q in self.db.sql_log if q.startswith('UPDATE users')]), 1)
        self.assertEqual(len(self.db.committed['transactions']), 1)

    def test_refunded_receipt_can_be_replayed_without_grant(self):
        self.run_settlement()
        self.db.committed['transactions']['in_1']['status']='refunded'
        result = self.run_settlement(invoice(event_id='evt_2'), now=NOW + dt.timedelta(days=180))
        self.assertEqual(result['status'], 'duplicate')
        self.assertEqual(result['receipt_status'], 'refunded')
        self.assertEqual(len([q for q in self.db.sql_log if q.startswith('UPDATE users')]), 1)

    def test_new_invoice_with_spent_intent_refuses(self):
        self.run_settlement()
        db = self.db
        old = copy.deepcopy(db.committed)
        with self.assertRaises(ValueError):
            self.run_settlement(invoice(event_id='evt_new', ident='in_other'), db=db)
        self.assertEqual(db.committed, old)

    def test_missing_durable_intent_refuses(self):
        self.db.committed['transactions'] = {}
        prior = copy.deepcopy(self.db.committed)
        with self.assertRaises(ValueError): self.run_settlement()
        self.assert_unchanged(prior)

    def test_different_uuid_not_authorized_by_ledger(self):
        self.event = invoice(intent_id=OTHER_INTENT)
        old = copy.deepcopy(self.db.committed)
        with self.assertRaises(ValueError): self.run_settlement()
        self.assert_unchanged(old)

    def test_duplicate_intent_rows_refuse(self):
        self.db.committed['transactions']['other'] = copy.deepcopy(pending_intent())
        self.db.committed['transactions']['other']['id'] = UUID('dddddddd-eeee-ffff-0000-333333333333')
        before = copy.deepcopy(self.db.committed)
        with self.assertRaises(ValueError): self.run_settlement()
        self.assert_unchanged(before)

    def test_unstaged_intent_refuses(self):
        self.db.committed['transactions']['intent:' + INTENT]['raw_summary_json']['dispatch_status']='not_sent'
        old = copy.deepcopy(self.db.committed)
        with self.assertRaises(ValueError): self.run_settlement()
        self.assert_unchanged(old)

    def test_explicit_intent_must_be_uuid_v4(self):
        for value in ('not-uuid','aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',None,''):
            with self.subTest(intent_id=value):
                db=FakeDB()
                with self.assertRaises(ValueError):
                    self.run_settlement(invoice(intent_id=value), db=db)
                self.assertEqual(db.connections, [])

    def test_new_period_must_advance_from_intent(self):
        event=invoice(period_start=OLD, period_end=OLD + 30*86400)
        before=copy.deepcopy(self.db.committed)
        with self.assertRaises(ValueError): self.run_settlement(event)
        self.assert_unchanged(before)

    def test_manual_mark_paid_never_grants(self):
        event=invoice(); event['data']['object']['paid_out_of_band']=True
        before=copy.deepcopy(self.db.committed)
        with self.assertRaises(ValueError): self.run_settlement(event)
        self.assert_unchanged(before)

    def test_failed_invoice_no_access(self):
        event=invoice(); event['data']['object']['paid']=False
        before=copy.deepcopy(self.db.committed)
        with self.assertRaises(ValueError): self.run_settlement(event)
        self.assert_unchanged(before)

    def test_wrong_price_and_catalog_refuse(self):
        for change in ('map','catalog','intent'):
            with self.subTest(change=change):
                db=FakeDB()
                if change == 'map': db.committed['price']['stripe_price_id']='price_wrong'
                if change == 'catalog': db.committed['catalog']['monthly_price_cents']=499
                if change == 'intent':
                    db.committed['transactions']['intent:'+INTENT]['raw_summary_json']['price_id']='price_wrong'
                before=copy.deepcopy(db.committed)
                with self.assertRaises(ValueError): self.run_settlement(db=db)
                self.assertEqual(db.committed,before)

    def test_subscription_or_user_changed_rejects_new(self):
        for change in ('inactive','wrong_plan','wrong_owner'):
            with self.subTest(change=change):
                db=FakeDB()
                if change=='inactive': db.committed['subscription']['internal_status']='expired'
                if change=='wrong_plan': db.committed['user']['plan_code']='magister'
                if change=='wrong_owner': db.committed['customer']['user_id'] = UUID(OTHER_INTENT)
                before=copy.deepcopy(db.committed)
                with self.assertRaises(ValueError): self.run_settlement(db=db)
                self.assertEqual(db.committed,before)

    def test_different_provider_customer_id_refuses(self):
        self.db.committed['customer']['provider_customer_id']='cus_else'
        before=copy.deepcopy(self.db.committed)
        with self.assertRaises(ValueError): self.run_settlement()
        self.assert_unchanged(before)

    def test_event_collision_stops(self):
        self.db.committed['events']['evt_1'] = dict(
            id=UUID(EVROW), event_type='invoice.paid', object_id='in_OTHER',
            user_id=UUID(USER), subscription_id=UUID(SUB), livemode=False,
            processing_status='processed', transaction_id=None)
        prior=copy.deepcopy(self.db.committed)
        with self.assertRaises(ValueError): self.run_settlement()
        self.assert_unchanged(prior)

    def test_processed_event_without_receipt_stops(self):
        self.db.committed['events']['evt_1'] = dict(
            id=UUID(EVROW), event_type='invoice.paid', object_id='in_1',
            user_id=UUID(USER), subscription_id=UUID(SUB), livemode=False,
            processing_status='processed', transaction_id=None)
        prior=copy.deepcopy(self.db.committed)
        with self.assertRaises(ValueError): self.run_settlement()
        self.assert_unchanged(prior)

    def test_recover_preexisting_received_event(self):
        self.db.committed['events']['evt_1'] = dict(
            id=UUID(EVROW), event_type='invoice.paid', object_id='in_1',
            user_id=UUID(USER), subscription_id=UUID(SUB), livemode=False,
            processing_status='received', transaction_id=None)
        self.assertEqual(self.run_settlement()['status'], 'settled')

    def test_replay_wrong_account_fails_closed(self):
        self.run_settlement()
        event=invoice(event_id='evt_bad')
        event['data']['object']['subscription_details']['metadata']['user_id']='other_user'
        before=copy.deepcopy(self.db.committed)
        with self.assertRaises(ValueError): self.run_settlement(event)
        self.assertEqual(self.db.committed,before)

    def test_replay_conflicting_invoice_facts_refuses(self):
        self.run_settlement()
        event=invoice(event_id='evt_bad')
        event['data']['object']['lines']['data'][0]['amount']=499
        event['data']['object']['amount_paid']=499
        event['data']['object']['total']=499
        before=copy.deepcopy(self.db.committed)
        with self.assertRaises(ValueError): self.run_settlement(event)
        self.assertEqual(self.db.committed,before)

    def test_cross_account_ledger_collision_refuses(self):
        self.run_settlement()
        self.db.committed['transactions']['in_1']['user_id'] = UUID(OTHER_INTENT)
        prior=copy.deepcopy(self.db.committed)
        with self.assertRaises(ValueError): self.run_settlement()
        self.assert_unchanged(prior)

    def test_failures_roll_back_existing_intent(self):
        for marker in ('UPDATE billing_transactions','UPDATE subscriptions',
                       'UPDATE users','INSERT INTO payment_events','UPDATE payment_events'):
            with self.subTest(step=marker):
                db=FakeDB(fail_on=marker)
                before=copy.deepcopy(db.committed)
                with self.assertRaises(RuntimeError): self.run_settlement(db=db)
                self.assertEqual(db.committed,before)
                self.assertEqual(db.connections[0].commits,0)
                self.assertEqual(db.connections[0].rollbacks,1)
                self.assertTrue(db.connections[0].closed)

    def test_no_provider_calls_or_nested_connections(self):
        self.run_settlement()
        self.assertEqual(len(self.db.connections),1)
        self.assertEqual(self.db.connections[0].commits,1)
        sql=' '.join(self.db.sql_log)
        for marker in ('pg_advisory_xact_lock','FROM billing_transactions',
                       'FROM subscriptions','FROM users','FROM billing_customers',
                       'FROM stripe_price_map','FROM plan_catalog'):
            self.assertIn(marker,sql)
        for marker in ('requests.', 'stripe.Subscription', 'get_db_connection('):
            self.assertNotIn(marker,SOURCE.read_text('utf8'))


if __name__=='__main__':
    unittest.main(verbosity=2)
