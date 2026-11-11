"""A7C4B: no-provider, no-PostgreSQL purchase-intent lifecycle simulations."""
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
from uuid import UUID
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'services/stripe_reup_settlement.py'
spec = importlib.util.spec_from_file_location('a7c4b_tested_settlement', SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
UID = 'd68f3f9b-a54c-4dfa-a7ed-b4034f63b662'
OTHER_UID = '491beb85-4f45-4f0e-bef0-59da7bb1aeb5'
INTENT = 'e069585c-5829-402b-94bd-514b1b6baf5e'
SUB_ROW = '75ad308b-eb35-4e49-8cbe-9e951512a709'
CUSTOMER_ROW = '63e86ee9-cd20-43be-9994-cc934df7c598'
RECEIPT = 'ca0e8578-c626-41f7-b8b4-b35b1430b9cd'
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
START = NOW - dt.timedelta(days=12)
END = NOW + dt.timedelta(days=18)


def seed():
    return {
        'user': dict(id=UID, plan_code='seeker', entitlement_status='active',
                     current_period_started_at=START, subscription_expires_at=END),
        'subs': [dict(id=SUB_ROW, user_id=UID, billing_customer_id=CUSTOMER_ROW,
                      plan_code='seeker', support_mode='monthly_recurring', provider='stripe',
                      stripe_subscription_id='sub_1', stripe_price_id='price_seeker',
                      provider_status='active', internal_status='active', livemode=False,
                      cancel_at_period_end=False, current_period_start=START,
                      current_period_end=END)],
        'customer': dict(id=CUSTOMER_ROW, user_id=UID, provider='stripe',
                         stripe_customer_id='cus_1', provider_customer_id='cus_1', livemode=False),
        'mapping': dict(stripe_price_id='price_seeker', plan_code='seeker',
                        support_mode='monthly_recurring', livemode=False),
        'catalog': dict(monthly_price_cents=199, monthly_enabled=True, active=True),
        'ledger': [],
    }


class FakeDB:
    def __init__(self, state=None):
        self.committed = copy.deepcopy(state if state is not None else seed())
        self.fail_at = None
        self.commits = 0
        self.rollbacks = 0
        self.connections = 0
        self.sql = []

    def connect(self):
        self.connections += 1
        return FakeConnection(self)


class FakeConnection:
    def __init__(self, db):
        self.db = db
        self.data = copy.deepcopy(db.committed)

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.db.committed = copy.deepcopy(self.data)
        self.db.commits += 1

    def rollback(self):
        self.db.rollbacks += 1

    def close(self):
        return None


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=()):
        db = self.conn.db
        q = ' '.join(sql.lower().split())
        db.sql.append(q)
        if db.fail_at is not None and db.fail_at in q:
            raise RuntimeError('synthetic SQL outage')
        data = self.conn.data
        self.rows, self.rowcount = [], 0
        if 'select' in q and 'from users' in q:
            if data['user'] and data['user']['id'] == params[0]:
                self.rows = [copy.deepcopy(data['user'])]
        elif 'select' in q and 'from billing_transactions' in q:
            if "raw_summary_json ->> 'schema'" in q:
                self.rows = [copy.deepcopy(row) for row in data['ledger']
                             if row['user_id'] == params[0] and row['status'] == 'pending'
                             and row['raw_summary_json'].get('schema') == 'stripe_paid_reup_intent.v1']
            elif "raw_summary_json ->> 'reup_intent_id'" in q:
                self.rows = [copy.deepcopy(row) for row in data['ledger']
                             if row['raw_summary_json'].get('reup_intent_id') == params[0]]
        elif 'select' in q and 'from subscriptions' in q:
            if "where user_id = %s::uuid" in q:
                self.rows = [copy.deepcopy(row) for row in data['subs']
                             if row['user_id'] == params[0] and row['internal_status'] == 'active']
            else:
                self.rows = [copy.deepcopy(row) for row in data['subs']
                             if row['id'] == params[0]]
        elif 'select' in q and 'from billing_customers' in q:
            if data['customer'] and data['customer']['id'] == params[0] \
                    and data['customer']['user_id'] == params[1]:
                self.rows = [copy.deepcopy(data['customer'])]
        elif 'select' in q and 'from stripe_price_map' in q:
            if data['mapping'] and data['mapping']['stripe_price_id'] == params[0]:
                self.rows = [copy.deepcopy(data['mapping'])]
        elif 'select' in q and 'from plan_catalog' in q:
            if data['catalog']:
                self.rows = [copy.deepcopy(data['catalog'])]
        elif 'insert into billing_transactions' in q:
            uid, subid, plan, livemode, payload = params
            row = dict(id=RECEIPT, user_id=uid, subscription_id=subid, plan_code=plan,
                       provider='stripe', transaction_kind='monthly_renewal',
                       provider_invoice_id=None, status='pending', livemode=livemode,
                       gross_amount_cents=0, raw_summary_json=json.loads(payload))
            data['ledger'].append(row)
            self.rows = [{'id': RECEIPT}]
            self.rowcount = 1
        elif 'update billing_transactions' in q:
            payload, recid = params
            row = next((row for row in data['ledger'] if str(row['id']) == str(recid)
                        and row['status'] == 'pending'
                        and row['provider_invoice_id'] is None), None)
            if row is not None:
                row['raw_summary_json'] = json.loads(payload)
                self.rows = [{'id': row['id']}]
                self.rowcount = 1
        else:
            raise AssertionError('Unrecognized or unsupported SQL: ' + q[:250])

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows = self.rows
        self.rows = []
        return rows


def provider_response(intent=INTENT):
    return dict(id='sub_1', customer='cus_1', livemode=False, status='active',
                metadata=dict(user_id=UID, reup_intent_id=intent, change_source='phase8_paid_reup'),
                items=dict(data=[dict(price=dict(id='price_seeker'))]),
                current_period_start=int(NOW.timestamp()),
                current_period_end=int((NOW + dt.timedelta(days=30)).timestamp()),
                latest_invoice='in_confirmed')


class IntentTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()

    def stage(self, **kw):
        args = dict(user_id=UID, connect=self.db.connect, expected_livemode=False,
                    now=NOW, intent_factory=lambda: UUID(INTENT))
        args.update(kw)
        return m.prepare_stripe_reup_intent(**args)

    def transition(self, action, **kw):
        args = dict(user_id=UID, intent_id=INTENT, connect=self.db.connect,
                    action=action)
        args.update(kw)
        return m.transition_stripe_reup_intent(**args)

    def prepare(self):
        result = self.stage()
        self.assertEqual(result['status'], 'prepared')
        return result

    def assert_refused_unchanged(self, run):
        prior = copy.deepcopy(self.db.committed)
        previous_connections, previous_rollbacks = self.db.connections, self.db.rollbacks
        with self.assertRaises((ValueError, RuntimeError)):
            run()
        self.assertEqual(self.db.committed, prior)
        if self.db.connections > previous_connections:
            self.assertGreater(self.db.rollbacks, previous_rollbacks)

    def test_stage_writes_exact_existing_ledger_schema(self):
        self.prepare()
        self.assertEqual(len(self.db.committed['ledger']), 1)
        row = self.db.committed['ledger'][0]
        meta = row['raw_summary_json']
        self.assertEqual(row['status'], 'pending')
        self.assertIsNone(row['provider_invoice_id'])
        self.assertEqual(row['gross_amount_cents'], 0)
        self.assertEqual(meta['schema'], 'stripe_paid_reup_intent.v1')
        self.assertEqual(meta['dispatch_status'], 'prepared')
        self.assertEqual(meta['subscription_row_id'], SUB_ROW)
        self.assertEqual(meta['previous_period_start'], int(START.timestamp()))
        self.assertEqual(meta['expected_paid_cents'], 199)
        self.assertEqual(meta['customer_id'], 'cus_1')
        self.assertEqual(self.db.commits, 1)

    def test_two_register_calls_return_one_pending_intent(self):
        self.prepare()
        retry = self.stage(intent_factory=lambda: UUID('02a930d6-2318-4843-b065-0c9869d18f9e'))
        self.assertEqual(retry, dict(status='already_pending', intent_id=INTENT,
                                     dispatch_status='prepared'))
        self.assertEqual(len(self.db.committed['ledger']), 1)

    def test_already_dispatching_never_generates_new_intent(self):
        self.prepare()
        self.transition('claim')
        self.assertEqual(self.stage()['dispatch_status'], 'dispatching')
        self.assertEqual(len(self.db.committed['ledger']), 1)

    def test_contradictory_pending_intents_refuse(self):
        self.prepare()
        dup=copy.deepcopy(self.db.committed['ledger'][0])
        dup['id']='491beb85-4f45-4f0e-bef0-59da7bb1aeb5'
        self.db.committed['ledger'].append(dup)
        self.assert_refused_unchanged(self.stage)

    def test_no_account_or_bad_user_identity(self):
        for uid in ('other', OTHER_UID, '', None):
            with self.subTest(uid=uid):
                self.assert_refused_unchanged(lambda: self.stage(user_id=uid))

    def test_livemode_must_be_exact(self):
        for env in (True, 0, None):
            with self.subTest(env=env):
                self.assert_refused_unchanged(lambda: self.stage(expected_livemode=env))

    def test_expired_or_inactive_access_refuses(self):
        self.db.committed['user']['subscription_expires_at'] = NOW
        self.assert_refused_unchanged(self.stage)
        self.db.committed['user']['subscription_expires_at'] = END
        self.db.committed['user']['entitlement_status'] = 'expired'
        self.assert_refused_unchanged(self.stage)

    def test_cross_provider_or_dual_active_rail_refuses(self):
        self.db.committed['subs'][0]['provider'] = 'apple'
        self.assert_refused_unchanged(self.stage)
        self.db.committed = seed()
        self.db.committed['subs'].append(dict(self.db.committed['subs'][0], id=OTHER_UID))
        self.assert_refused_unchanged(self.stage)

    def test_subscription_mismatches_refuse(self):
        cases = [('cancel_at_period_end', True), ('provider_status','past_due'),
                 ('stripe_price_id','price_other'), ('plan_code','magister'),
                 ('current_period_start', START - dt.timedelta(days=1))]
        for key, value in cases:
            with self.subTest(key=key):
                self.db.committed = seed()
                self.db.committed['subs'][0][key] = value
                self.assert_refused_unchanged(self.stage)

    def test_customer_mismatch_refuses(self):
        self.db.committed['customer']['provider_customer_id'] = 'cus_other'
        self.assert_refused_unchanged(self.stage)

    def test_price_map_mismatch_refuses(self):
        self.db.committed['mapping']['livemode'] = True
        self.assert_refused_unchanged(self.stage)

    def test_catalog_disabled_or_zero_refuses(self):
        for key, val in [('monthly_enabled', False), ('monthly_price_cents',0)]:
            with self.subTest(key=key):
                self.db.committed = seed()
                self.db.committed['catalog'][key] = val
                self.assert_refused_unchanged(self.stage)

    def test_invalid_clock_and_intent_identity_refuse(self):
        self.assert_refused_unchanged(lambda: self.stage(now=NOW.replace(tzinfo=None)))
        self.assert_refused_unchanged(lambda: self.stage(intent_factory=lambda: UUID(int=0)))

    def test_intent_collision_refuses(self):
        self.prepare()
        self.db.committed['ledger'][0]['status'] = 'failed'
        self.assert_refused_unchanged(self.stage)

    def test_insert_outage_rolls_back(self):
        self.db.fail_at = 'insert into billing_transactions'
        self.assert_refused_unchanged(self.stage)

    def test_claim_exact_once_without_financial_write(self):
        self.prepare()
        reply = self.transition('claim')
        self.assertEqual(reply['status'], 'dispatching')
        self.assertEqual(reply['dispatch']['previous_period_start'], int(START.timestamp()))
        self.assertEqual(reply['dispatch']['expected_paid_cents'], 199)
        self.assertEqual(self.db.committed['ledger'][0]['gross_amount_cents'], 0)
        self.assertEqual(self.db.committed['user']['current_period_started_at'], START)
        self.assertIsNone(self.db.committed['ledger'][0]['provider_invoice_id'])

    def test_repeated_claim_is_held(self):
        self.prepare()
        self.transition('claim')
        self.assertEqual(self.transition('claim')['status'], 'held')
        self.assertEqual(self.db.committed['ledger'][0]['raw_summary_json']['dispatch_status'], 'dispatching')

    def test_claim_refuses_if_subscription_changed(self):
        self.prepare()
        self.db.committed['subs'][0]['current_period_start'] = NOW
        self.assert_refused_unchanged(lambda: self.transition('claim'))

    def test_claim_refuses_if_paid_account_changed(self):
        self.prepare()
        self.db.committed['user']['entitlement_status'] = 'expired'
        self.assert_refused_unchanged(lambda: self.transition('claim'))

    def test_confirm_requires_verified_provider_identity(self):
        self.prepare(); self.transition('claim')
        for key, val in [('id','sub_foreign'),('customer','cus_foreign'),
                         ('status','past_due'),('livemode',True),
                         ('latest_invoice',None),('current_period_start',int(START.timestamp()))]:
            with self.subTest(key=key):
                invalid = provider_response(); invalid[key] = val
                self.assert_refused_unchanged(lambda: self.transition('confirm', stripe_result=invalid))
        bad=provider_response(); bad['metadata']['reup_intent_id']=OTHER_UID
        self.assert_refused_unchanged(lambda: self.transition('confirm', stripe_result=bad))

    def test_confirm_stays_pending_until_invoice_settlement(self):
        self.prepare(); self.transition('claim')
        self.assertEqual(self.transition('confirm', stripe_result=provider_response())['status'], 'sent')
        row=self.db.committed['ledger'][0]
        self.assertEqual(row['raw_summary_json']['stripe_latest_invoice_id'], 'in_confirmed')
        self.assertEqual(row['status'],'pending')
        self.assertEqual(row['gross_amount_cents'],0)
        self.assertEqual(self.db.committed['user']['current_period_started_at'], START)

    def test_uncertain_claim_locks_out_retry(self):
        self.prepare(); self.transition('claim')
        self.assertEqual(self.transition('uncertain')['status'], 'uncertain')
        self.assertEqual(self.transition('claim')['status'], 'held')
        self.assertEqual(self.transition('uncertain')['status'], 'held')
        self.assert_refused_unchanged(lambda: self.transition('confirm', stripe_result=provider_response()))
        self.assertEqual(len(self.db.committed['ledger']), 1)

    def test_invalid_transition_refuses(self):
        self.prepare()
        self.assert_refused_unchanged(lambda: self.transition('confirm', stripe_result=provider_response()))
        self.assert_refused_unchanged(lambda: self.transition('uncertain'))
        self.assert_refused_unchanged(lambda: self.transition('retry'))

    def test_missing_or_wrong_owner_intent_refuses(self):
        self.assert_refused_unchanged(lambda: self.transition('claim'))
        self.prepare()
        self.assert_refused_unchanged(lambda: self.transition('claim',user_id=OTHER_UID))
        self.assert_refused_unchanged(lambda: self.transition('claim',intent_id=OTHER_UID))

    def test_state_update_outage_rolls_back(self):
        self.prepare()
        self.db.fail_at='update billing_transactions'
        self.assert_refused_unchanged(lambda: self.transition('claim'))

    def test_no_direct_provider_or_driver_imports(self):
        source=SOURCE.read_text('utf-8')
        for forbidden in ('import stripe', 'import psycopg', 'import requests', 'import httpx'):
            self.assertNotIn(forbidden, source)
        self.assertIn("FOR UPDATE", source)
        self.assertIn("stripe_paid_reup_intent.v1", source)


if __name__=='__main__':
    unittest.main(verbosity=2)
