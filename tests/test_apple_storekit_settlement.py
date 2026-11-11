"""A7C5H1 zero-provider, zero-database acceptance of dormant Apple writer.

Tests use the installed Apple cryptographic-contract TYPES and a simulated
cursor/connection. Actual PostgreSQL locking and index behavior remain open.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.apple_storekit_contract import AppleTransactionFacts, PRODUCT_ID
from services.apple_storekit_settlement import (
    AppleSettlementError, settle_verified_apple_transaction,
)

USER = '1a991fee-bbdc-4896-81a3-96636473911a'
OTHER = 'faba593d-9997-426c-aaad-f6fe02cac6c4'
NOW = datetime(2026, 10, 23, 17, tzinfo=timezone.utc)
TXID = '20000000000010'
ORIG = '20000000000001'
SUBID = '00000000-0000-4000-8000-000000000001'
CUSTOMERID = '00000000-0000-4000-8000-000000000002'
RECEIPTID = '00000000-0000-4000-8000-000000000003'


def facts(**changes):
    d = dict(environment='Sandbox', app_account_token=USER,
             original_transaction_id=ORIG, transaction_id=TXID,
             product_id=PRODUCT_ID, purchase_date=NOW - timedelta(days=1),
             expires_date=NOW + timedelta(days=29), signed_date=NOW,
             revoked_at=None, upgraded=False)
    d.update(changes)
    return AppleTransactionFacts(**d)


def chain(**changes):
    d = dict(id=SUBID, user_id=UUID(USER), provider='apple',
             provider_subscription_id=ORIG, provider_status='active',
             internal_status='active', current_period_start=NOW - timedelta(days=31),
             current_period_end=NOW - timedelta(days=1), livemode=False,
             cancel_at_period_end=True, auto_renews=False, started_at=NOW - timedelta(days=61),
             plan_code='seeker', support_mode='monthly_recurring', provider_price_id=PRODUCT_ID)
    d.update(changes)
    return d


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, typ, val, tb):
        return False

    def execute(self, sql, params=None):
        normalized = ' '.join(sql.lower().split())
        self.conn.calls.append((normalized, params))
        self.rowcount = 1
        self.rows = []
        if self.conn.fail_on and self.conn.fail_on in normalized:
            raise RuntimeError('simulated database error')
        if 'select current_database()' in normalized:
            self.rows = [{'db_name': self.conn.database}]
        elif 'from pg_catalog.pg_index' in normalized:
            self.rows = [self.conn.guards]
        elif ' from users ' in normalized and 'for update' in normalized:
            self.rows = [self.conn.user] if self.conn.user else []
        elif ' from provider_product_map ' in normalized:
            self.rows = self.conn.products
        elif ' from subscriptions ' in normalized:
            self.rows = self.conn.subscriptions
        elif ' from payment_events ' in normalized:
            self.rows = [self.conn.old_event] if self.conn.old_event else []
        elif ' from billing_customers ' in normalized:
            self.rows = [self.conn.customer] if self.conn.customer else []
        elif 'insert into billing_customers ' in normalized:
            self.rows = [{'id': CUSTOMERID}]
        elif 'insert into subscriptions ' in normalized:
            self.rows = [{'id': SUBID}]
        elif 'insert into billing_transactions ' in normalized:
            self.rows = [{'id': RECEIPTID}]
        elif 'insert into payment_events ' in normalized:
            self.rows = [{'id': '00000000-0000-4000-8000-000000000004'}]
        elif 'update users ' in normalized and self.conn.fail_user_update:
            self.rowcount = 0

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0
        self.autocommit = False
        self.database = 'godinc_dev'
        self.fail_on = None
        self.fail_user_update = False
        self.guards = {'chain_unique': True, 'receipt_unique': True}
        self.user = dict(id=UUID(USER), email_verified=True, is_banned=False,
                         highest_paid_plan_ever=None, scroll_count=0)
        self.products = [{'provider_product_id': PRODUCT_ID, 'provider_price_id': PRODUCT_ID}]
        self.subscriptions = []
        self.old_event = None
        self.customer = None

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1

    def close(self):
        self.close_count += 1

    def writes(self):
        return [c for c in self.calls if c[0].startswith(('insert ', 'update ', 'delete '))]


def floors(highest, scroll):
    return {'donor': 'pilgrim' if highest == 'seeker' else 'seeker',
            'scroll': None, 'fallback': 'pilgrim'}


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.conn = FakeConnection()
        self.opens = 0

    def connect(self):
        self.opens += 1
        return self.conn

    def settle(self, tx=None, **changes):
        params = dict(facts=facts() if tx is None else tx,
                      expected_user_id=USER, environment='Sandbox',
                      connection_factory=self.connect, compute_floors=floors,
                      expected_database='godinc_dev', now=NOW)
        params.update(changes)
        return settle_verified_apple_transaction(**params)

    def assertStopped(self, tx=None, **changes):
        with self.assertRaises(AppleSettlementError):
            self.settle(tx=tx, **changes)
        self.assertEqual(self.conn.commit_count, 0)

    def test_new_purchase_atomic_transaction_and_pending_financial_amount(self):
        result = self.settle()
        self.assertEqual(result.outcome, 'granted')
        self.assertTrue(result.access_granted)
        self.assertEqual(result.subscription_id, SUBID)
        self.assertEqual((self.conn.commit_count, self.conn.rollback_count, self.conn.close_count), (1, 0, 1))
        writes = self.conn.writes()
        self.assertEqual(len(writes), 5)
        labels = [sql.split()[2] for sql, _ in writes if sql.startswith('insert ')]
        self.assertEqual(labels, ['billing_customers', 'subscriptions', 'billing_transactions', 'payment_events'])
        ledger = next((sql, params) for sql, params in writes if 'insert into billing_transactions' in sql)
        self.assertIn("'xxx', 0, 'pending'", ledger[0])
        self.assertIn('unreconciled_not_revenue', ledger[1][-1])
        self.assertNotIn('signedpayload', ''.join(x[0] for x in self.conn.calls))
        self.assertTrue(any('update users set plan_code' in sql for sql, _ in writes))
        self.assertTrue(any('pg_advisory_xact_lock' in sql for sql, _ in self.conn.calls))

    def test_renewal_preserves_existing_subscription_identity_and_cancellation(self):
        self.conn.subscriptions = [chain()]
        result = self.settle()
        self.assertEqual(result.outcome, 'granted')
        self.assertEqual(result.subscription_id, SUBID)
        self.assertTrue(any('update subscriptions set' in sql for sql, _ in self.conn.writes()))
        self.assertFalse(any('insert into subscriptions' in sql for sql, _ in self.conn.writes()))
        ledger = next(params for sql, params in self.conn.calls if 'insert into billing_transactions' in sql)
        self.assertEqual(ledger[2], 'monthly_renewal')
        update = next(params for sql, params in self.conn.calls if 'update users set plan_code' in sql)
        self.assertTrue(update[4])

    def test_repeat_event_returns_without_any_write_or_second_allowance(self):
        self.conn.subscriptions = [chain()]
        self.conn.old_event = {'id': 'old', 'user_id': UUID(USER), 'object_id': TXID,
                               'processing_status': 'processed', 'subscription_id': SUBID}
        result = self.settle()
        self.assertEqual(result.outcome, 'already_processed')
        self.assertFalse(result.access_granted)
        self.assertEqual(self.conn.writes(), [])
        self.assertEqual((self.conn.commit_count, self.conn.rollback_count), (0, 1))

    def test_stale_unrecorded_purchase_does_not_change_entitlement(self):
        self.conn.subscriptions = [chain(current_period_start=NOW - timedelta(hours=8),
                                         current_period_end=NOW + timedelta(days=30))]
        result = self.settle()
        self.assertEqual(result.outcome, 'stale_ignored')
        self.assertEqual(self.conn.commit_count, 1)
        self.assertEqual(len(self.conn.writes()), 1)
        self.assertIn('insert into payment_events', self.conn.writes()[0][0])
        self.assertIn("'ignored'", self.conn.writes()[0][0])

    def test_renewal_from_grace_clears_stale_grace_fields(self):
        self.conn.subscriptions = [chain(internal_status='grace',provider_status='grace',
            current_period_end=NOW-timedelta(hours=4),grace_period_ends_at=NOW+timedelta(days=2))]
        result=self.settle()
        self.assertEqual(result.outcome,'granted')
        updates=[q for q,_ in self.conn.writes()]
        self.assertTrue(any('update subscriptions set' in q and 'grace_period_ends_at=null' in q for q in updates))
        self.assertTrue(any('update users set' in q and 'grace_period_ends_at=null' in q for q in updates))

    def test_second_apple_chain_blocked_during_grace_after_paid_expiry(self):
        self.conn.subscriptions = [chain(provider_subscription_id='other',
            internal_status='grace', provider_status='grace',
            current_period_end=NOW-timedelta(days=1),
            grace_period_ends_at=NOW+timedelta(days=2))]
        self.assertStopped()
        self.assertFalse(self.conn.writes())

    def test_other_stripe_paid_rail_refused(self):
        self.conn.subscriptions = [chain(provider='stripe', provider_subscription_id='sub_other',
                                         current_period_end=NOW + timedelta(days=10))]
        self.assertStopped()
        self.assertFalse(self.conn.writes())

    def test_different_active_apple_chain_refused(self):
        self.conn.subscriptions = [chain(provider_subscription_id='2000000099999',
                                         current_period_end=NOW + timedelta(days=10))]
        self.assertStopped()

    def test_other_account_chain_refused(self):
        self.conn.subscriptions = [chain(user_id=UUID(OTHER))]
        self.assertStopped()

    def test_chain_plan_product_mismatch_refused(self):
        for x in ({'plan_code':'magister'}, {'provider_price_id':'ai.other.product'}, {'support_mode':'annual_recurring'}):
            with self.subTest(x=x):
                self.conn = FakeConnection()
                self.conn.subscriptions = [chain(**x)]
                self.assertStopped()

    def test_missing_or_duplicated_product_mapping_refused(self):
        for products in ([], [dict(provider_product_id='invalid')],
                         [{'provider_product_id': PRODUCT_ID}] * 2):
            with self.subTest(products=products):
                self.conn = FakeConnection()
                self.conn.products = products
                self.assertStopped()
                self.assertEqual(self.conn.writes(), [])

    def test_ledger_unique_index_is_required_before_writes(self):
        for name in ('chain_unique', 'receipt_unique'):
            with self.subTest(index=name):
                self.conn = FakeConnection()
                self.conn.guards[name] = None
                self.assertStopped()
                self.assertEqual(self.conn.writes(), [])

    def test_user_must_be_verified_and_permitted(self):
        for user in (None, dict(self.conn.user, email_verified=False),
                     dict(self.conn.user, is_banned=True)):
            with self.subTest(user=user):
                self.conn = FakeConnection()
                self.conn.user = user
                self.assertStopped()
                self.assertEqual(self.conn.writes(), [])

    def test_invalid_fact_account_or_environment_no_db_connection(self):
        cases = [dict(tx=facts(app_account_token=OTHER)),
                 dict(environment='Production'),
                 dict(expected_user_id='not-uuid'),
                 dict(tx=facts(environment='Production')),
                 dict(tx=facts(revoked_at=NOW - timedelta(hours=1))),
                 dict(tx=facts(upgraded=True)),
                 dict(tx=facts(expires_date=NOW - timedelta(seconds=1))),
                 dict(tx=facts(purchase_date=NOW + timedelta(hours=1))),
                 dict(tx=facts(signed_date=NOW + timedelta(minutes=10)))]
        for settings in cases:
            with self.subTest(settings=str(settings)[:90]):
                self.conn = FakeConnection()
                with self.assertRaises(AppleSettlementError):
                    self.settle(**settings)
                self.assertEqual(self.opens, 0)

    def test_untrusted_plain_dict_is_rejected_without_connection(self):
        self.assertStopped(tx={'transaction_id': TXID})
        self.assertEqual(self.opens, 0)

    def test_malformed_callback_and_history_fail_closed(self):
        self.conn.user['highest_paid_plan_ever'] = 'undefined'
        self.assertStopped()
        self.conn = FakeConnection()
        self.assertStopped(compute_floors=lambda *_: {'donor': 'seeker'})

    def test_apple_customer_identity_conflict_refuses(self):
        self.conn.customer = {'id': CUSTOMERID, 'provider_customer_id': 'somebody-else', 'livemode': False}
        self.assertStopped()

    def test_apple_customer_environment_conflict_refuses(self):
        self.conn.customer = {'id': CUSTOMERID,
                              'provider_customer_id': 'app-account-token:' + USER, 'livemode': True}
        self.assertStopped()

    def test_explicit_database_identity_required(self):
        self.assertStopped(expected_database='other_db')
        self.assertEqual(self.opens, 0)
        self.conn = FakeConnection()
        self.conn.database = 'godinc_prod'
        self.assertStopped()
        self.assertEqual(self.conn.writes(), [])

    def test_autocommit_mode_refused(self):
        self.conn.autocommit = True
        self.assertStopped()

    def test_database_error_rolls_back_whole_settlement(self):
        self.conn.fail_on = 'insert into payment_events'
        with self.assertRaises(RuntimeError):
            self.settle()
        self.assertEqual((self.conn.commit_count, self.conn.rollback_count, self.conn.close_count), (0, 1, 1))

    def test_entitlement_update_failure_rolls_back_all_writes(self):
        self.conn.fail_user_update = True
        self.assertStopped()
        self.assertEqual(self.conn.rollback_count, 1)

    def test_conflicting_replay_never_confirms(self):
        self.conn.subscriptions = [chain()]
        self.conn.old_event = {'id': 'old', 'user_id': UUID(OTHER), 'object_id': TXID,
                               'processing_status': 'processed', 'subscription_id': SUBID}
        self.assertStopped()

    def test_replay_with_missing_durable_subscription_is_not_accepted(self):
        self.conn.old_event = {'id': 'old', 'user_id': UUID(USER), 'object_id': TXID,
                               'processing_status': 'processed', 'subscription_id': SUBID}
        self.assertStopped()

    def test_existing_higher_donor_history_is_preserved(self):
        self.conn.user['highest_paid_plan_ever'] = 'sovereign'
        self.settle()
        update = next(params for sql, params in self.conn.calls if 'update users set plan_code' in sql)
        self.assertEqual(update[5], 'sovereign')
        self.assertEqual(update[6], 'seeker')


if __name__ == '__main__':
    unittest.main(verbosity=2)
