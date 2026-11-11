"""A7C5G7: server-trusted quote binding before a new paid re-up intent.

Synthetic SQL/HTTP/Stripe fixtures only; never imports the live application.
"""
import ast
import copy
import importlib.util
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parents[1]

def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT/rel)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

fixture = load('a7c5g7_purchase_fixtures', 'tests/test_stripe_reup_purchase_idempotency.py')
route = load('a7c5g7_post_fixtures', 'tests/test_stripe_reup_purchase_route.py')

class ServerGateTests(TestCase):
    def setUp(self):
        self.db = fixture.PurchaseDB()
        self.offer = {
            'eligible': True, 'quote_only': True,
            'plan_code': 'seeker', 'support_mode': 'monthly_recurring',
            'currency': 'usd', 'new_period_question_limit': 17,
            'questions_used': 17, 'amount_cents': 199,
            'quoted_period_start_epoch': int(fixture.it.START.timestamp()),
            'quoted_period_end_epoch': int(fixture.it.END.timestamp()),
            'existing_period_ends_at': fixture.it.END.isoformat(),
        }

    def confirmed(self):
        return {
            'amount_cents': self.offer['amount_cents'],
            'currency': self.offer['currency'],
            'new_period_question_limit': self.offer['new_period_question_limit'],
            'existing_period_ends_at': self.offer['existing_period_ends_at'],
        }

    def stage(self, offer=None, request=fixture.REQUEST):
        from uuid import UUID
        return fixture.it.m.prepare_stripe_reup_intent(
            user_id=fixture.it.UID, connect=self.db.connect,
            expected_livemode=False, now=fixture.it.NOW,
            purchase_request_id=request,
            intent_factory=lambda: UUID(fixture.it.INTENT),
            exhausted_quote=self.offer if offer is None else offer,
            confirmed_terms=self.confirmed(),
        )

    def refused_without_ledger(self, bad):
        prior = copy.deepcopy(self.db.committed)
        with self.assertRaises((RuntimeError, ValueError)):
            self.stage(bad)
        self.assertEqual(self.db.committed, prior)
        self.assertFalse(any('insert into billing_transactions' in s for s in self.db.sql))
        self.assertGreaterEqual(self.db.rollbacks, 1)

    def test_valid_exhausted_quote_stages_one_intent(self):
        result = self.stage()
        self.assertEqual(result['status'], 'prepared')
        self.assertEqual(len(self.db.committed['ledger']), 1)

    def test_under_limit_cannot_stage(self):
        bad = dict(self.offer, questions_used=16)
        self.refused_without_ledger(bad)

    def test_missing_eligible_quote_cannot_stage(self):
        self.refused_without_ledger({'eligible': False})

    def test_inconsistent_plan_and_support_mode_refused(self):
        for field, value in [('plan_code','magister'), ('support_mode','annual_recurring'),
                             ('currency','eur'), ('quote_only',False)]:
            with self.subTest(field=field):
                self.refused_without_ledger(dict(self.offer, **{field:value}))

    def test_noninteger_or_zero_values_refused(self):
        for field,value in [('new_period_question_limit',0),('new_period_question_limit','17'),
                            ('questions_used',17.0),('amount_cents','199'),
                            ('quoted_period_start_epoch',float(self.offer['quoted_period_start_epoch']))]:
            with self.subTest(field=field):
                self.refused_without_ledger(dict(self.offer, **{field:value}))

    def test_period_start_changes_before_registration_refused(self):
        self.db.committed['user']['current_period_started_at'] += fixture.it.dt.timedelta(days=1)
        self.refused_without_ledger(self.offer)

    def test_period_end_changes_before_registration_refused(self):
        self.db.committed['user']['subscription_expires_at'] += fixture.it.dt.timedelta(days=1)
        self.refused_without_ledger(self.offer)

    def test_price_changes_before_registration_refused(self):
        self.db.committed['catalog']['monthly_price_cents'] = 299
        self.refused_without_ledger(self.offer)

    def test_pending_request_is_held_even_with_now_unavailable_offer(self):
        self.assertEqual(self.stage()['status'], 'prepared')
        stale = {'eligible': False}
        self.assertEqual(self.stage(stale)['status'], 'already_pending')
        self.assertEqual(len(self.db.committed['ledger']),1)

    def test_settled_request_stays_historical_after_quota_reset(self):
        row = fixture.it.m.prepare_stripe_reup_intent(
            user_id=fixture.it.UID, connect=self.db.connect,
            expected_livemode=False, now=fixture.it.NOW,
            purchase_request_id=fixture.REQUEST, exhausted_quote=self.offer,
            confirmed_terms=self.confirmed(),
            intent_factory=lambda: fixture.it.UUID(fixture.it.INTENT),
        )
        self.assertEqual(row['status'],'prepared')
        entry = self.db.committed['ledger'][0]
        entry['raw_summary_json'].update(schema='stripe_paid_reup_receipt.v2',
                                         intent_id=fixture.it.INTENT,invoice_id='in_prepaid')
        entry.update(status='succeeded',provider_invoice_id='in_prepaid')
        self.db.committed['user']['entitlement_status']='expired'
        self.assertEqual(self.stage({'eligible':False})['status'],'already_settled')
        self.assertEqual(len(self.db.committed['ledger']),1)

    def test_route_passes_quote_to_durable_writer(self):
        h = route.Harness()
        output = h.run()
        self.assertEqual(output['status'],'awaiting_verified_payment')
        self.assertIs(h.calls[1][1]['exhausted_quote'],h.offer)
        self.assertEqual(h.names().count('provider'),1)

    def test_route_does_not_dispatch_when_quote_ineligible(self):
        h = route.Harness()
        h.offer = {'eligible':False}
        with self.assertRaises(route.FakeHTTPException) as raised:
            h.run()
        self.assertEqual(raised.exception.status_code,409)
        self.assertNotIn('provider',h.names())

    def test_quote_and_writer_are_both_server_owned_and_bound_before_insert(self):
        source = (ROOT/'main.py').read_text(encoding='utf8')
        service = (ROOT/'services/stripe_reup_settlement.py').read_text(encoding='utf8')
        tree = ast.parse(source)
        routefn = next(n for n in tree.body if isinstance(n,ast.FunctionDef)
                       and n.name == 'billing_stripe_early_reup')
        calls = [ast.unparse(x.func) for x in ast.walk(routefn) if isinstance(x,ast.Call)]
        self.assertIn('billing_stripe_early_reup_quote',calls)
        self.assertIn('prepare_stripe_reup_intent',calls)
        begin = source.index('def billing_stripe_early_reup(request:')
        part=source[begin:source.index('\n@app.post("/billing/checkout-session")',begin)]
        self.assertLess(part.index('billing_stripe_early_reup_quote(request)'),
                        part.index('prepare_stripe_reup_intent('))
        self.assertIn('exhausted_quote=offer',part)
        start = service.index('def prepare_stripe_reup_intent(')
        end = service.index('\ndef transition_stripe_reup_intent(',start)
        section=service[start:end]
        self.assertLess(section.index('if historical:'),section.index('if exhausted_quote is not None:'))
        self.assertLess(section.index("'dispatch_status': stored['dispatch_status']"),
                        section.index('if exhausted_quote is not None:'))
        self.assertLess(section.index('if exhausted_quote is not None:'),
                        section.index('INSERT INTO billing_transactions'))
        self.assertIn("exhausted_quote['amount_cents'] == plan['monthly_price_cents']",section)

if __name__=='__main__': main(verbosity=2)
