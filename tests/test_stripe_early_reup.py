"""Offline Stripe same-tier re-up regression; AST-only, no imports of Stripe/app/DB."""
import ast
import hashlib
from uuid import UUID
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'services' / 'stripe_billing.py'
INTENT = 'e069585c-5829-402b-94bd-514b1b6baf5e'
OTHER_INTENT = '02a930d6-2318-4843-b065-0c9869d18f9e'


def sub(price='price_seeker', status='active', start=1700000000, cancel=False, customer='cus_1'):
    # Modern Stripe contract: authoritative dates belong to the subscription item.
    end = start + 2592000 if type(start) is int else 1702592000
    return dict(id='sub_1', customer=customer, status=status,
                cancel_at_period_end=cancel, livemode=False, metadata={'original': 'keep'},
                items={'data': [{'id': 'si_1', 'price': {'id': price},
                                 'current_period_start': start,
                                 'current_period_end': end}]})


class Fake:
    def __init__(self, row=None, price='price_seeker', mode='monthly_recurring', failure=False):
        self.row, self.price, self.mode, self.failure = row or sub(), price, mode, failure
        self.calls = []
        def modify(subscription_id, **kw):
            self.calls.append(kw)
            if self.failure: raise ValueError('declined card')
            return dict(self.row, metadata=kw['metadata'])
        unit = ast.parse(SOURCE.read_text())
        required = ('extract_stripe_single_item_period_v1',
                    'change_existing_subscription_plan')
        functions = [x for x in unit.body if isinstance(x, ast.FunctionDef)
                     and x.name in required]
        if tuple(x.name for x in functions) != required:
            raise RuntimeError('Stripe function dependency identity failed')
        scope = dict(Optional=Optional, UUID=UUID, hashlib=hashlib, init_stripe=lambda: None,
                     get_checkout_plan_and_price=lambda **kw: dict(
                         stripe_price_id=self.price, support_mode=self.mode,
                         plan_code=kw['plan_code'], display_name='Seeker'),
                     get_or_create_billing_customer=lambda **kw: {'stripe_customer_id': 'cus_1'},
                     get_subscription_item_id=lambda row: row['items']['data'][0]['id'],
                     stripe_obj_to_plain=lambda obj: obj,
                     stripe=SimpleNamespace(Subscription=SimpleNamespace(
                         retrieve=lambda subscription_id: self.row, modify=modify)))
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), 'exec'), scope)
        self.change = scope['change_existing_subscription_plan']

    def run(self, force=False, intent=INTENT):
        return self.change(user_id='user_1', user_email='seek@example.test',
                           display_name='Seeker', current_subscription_id='sub_1',
                           plan_code='seeker', support_mode=self.mode,
                           force_paid_reup=force, reup_intent_id=intent)


class ReupTests(unittest.TestCase):
    def test_normal_same_price_noop(self):
        f = Fake(); self.assertFalse(f.run()['changed_subscription']); self.assertEqual(f.calls, [])

    def test_upgrade_unchanged(self):
        f = Fake(price='price_magus'); result = f.run(); opts = f.calls[0]
        self.assertTrue(result['changed_subscription']); self.assertFalse(result['pending_paid_invoice'])
        self.assertEqual(opts['billing_cycle_anchor'], 'now'); self.assertEqual(opts['proration_behavior'], 'none')
        self.assertEqual(opts['payment_behavior'], 'error_if_incomplete')
        self.assertEqual(opts['metadata']['change_source'], 'phase8_change_plan')
        self.assertNotIn('idempotency_key', opts)
        self.assertNotIn('reup_intent_id', opts['metadata'])

    def test_reup_requires_paid_invoice(self):
        f = Fake(); result = f.run(True); opts = f.calls[0]
        self.assertTrue(result['pending_paid_invoice']); self.assertIn('verified new invoice', result['message'])
        self.assertEqual(opts['billing_cycle_anchor'], 'now'); self.assertEqual(opts['proration_behavior'], 'none')
        self.assertEqual(opts['payment_behavior'], 'error_if_incomplete')
        self.assertEqual(opts['metadata']['original'], 'keep')
        self.assertEqual(opts['metadata']['change_source'], 'phase8_paid_reup')
        self.assertEqual(opts['metadata']['reup_intent_id'], INTENT)

    def test_retry_key_stable(self):
        f = Fake(); f.run(True); f.run(True)
        self.assertEqual(f.calls[0]['idempotency_key'], f.calls[1]['idempotency_key'])
        self.assertLessEqual(len(f.calls[0]['idempotency_key']), 255)

    def test_new_intent_new_key(self):
        a,b = Fake(), Fake(); a.run(True, INTENT); b.run(True, OTHER_INTENT)
        self.assertNotEqual(a.calls[0]['idempotency_key'], b.calls[0]['idempotency_key'])

    def test_same_intent_stable_after_billing_period_advances(self):
        a,b = Fake(), Fake(row=sub(start=1700006000)); a.run(True, INTENT); b.run(True, INTENT)
        self.assertEqual(a.calls[0]['idempotency_key'], b.calls[0]['idempotency_key'])

    def test_missing_intent_refuses_dispatch(self):
        f = Fake()
        with self.assertRaises(ValueError): f.run(True, None)
        self.assertEqual(f.calls, [])

    def test_malformed_intent_refuses_dispatch(self):
        for bad in ('', 'other', '00000000-0000-0000-0000-000000000000',
                    'e069585c-5829-402b-94bd-514b1b6baf5E'):
            with self.subTest(intent=bad):
                f = Fake()
                with self.assertRaises(ValueError): f.run(True, bad)
                self.assertEqual(f.calls, [])

    def test_reup_no_different_price(self):
        f = Fake(price='price_magus')
        with self.assertRaises(ValueError): f.run(True)
        self.assertEqual(f.calls, [])

    def test_reup_no_annual(self):
        f = Fake(mode='annual_recurring')
        with self.assertRaises(ValueError): f.run(True)
        self.assertEqual(f.calls, [])

    def test_reup_no_inactive(self):
        for status in ('trialing','past_due','unpaid','incomplete','canceled'):
            with self.subTest(status=status):
                f = Fake(row=sub(status=status))
                with self.assertRaises(ValueError): f.run(True)
                self.assertEqual(f.calls, [])

    def test_reup_no_cancel_pending(self):
        f = Fake(row=sub(cancel=True))
        with self.assertRaises(ValueError): f.run(True)
        self.assertEqual(f.calls, [])

    def test_reup_requires_period_identity(self):
        for start in (None,0,-1,True,'1700000000'):
            with self.subTest(start=start):
                f = Fake(row=sub(start=start))
                with self.assertRaises(ValueError): f.run(True)
                self.assertEqual(f.calls, [])

    def test_reup_checks_customer(self):
        f = Fake(row=sub(customer='cus_other'))
        with self.assertRaises(ValueError): f.run(True)
        self.assertEqual(f.calls, [])

    def test_reup_requires_price_identity(self):
        row=sub(); row['items']['data'][0]['price']={}; f=Fake(row=row)
        with self.assertRaises(ValueError): f.run(True)
        self.assertEqual(f.calls, [])

    def test_failure_never_returns_success(self):
        f=Fake(failure=True)
        with self.assertRaisesRegex(ValueError, 'declined card'): f.run(True)
        self.assertEqual(len(f.calls), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
