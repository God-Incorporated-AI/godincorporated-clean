"""A7C5G8: synthetic browser-reviewed amount/period -> durable new-intent binding.

No real application imports, PostgreSQL connections, Stripe calls, or HTTP requests.
"""
import ast
import copy
import importlib.util
from pathlib import Path
from unittest import TestCase, main

ROOT=Path(__file__).resolve().parents[1]

def load(name,relative):
    spec=importlib.util.spec_from_file_location(name,ROOT/relative)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

server=load('a7c5g8_server_fixtures','tests/test_stripe_reup_server_gate.py')
route=load('a7c5g8_purchase_fixtures','tests/test_stripe_reup_purchase_route.py')

class ConsentIntegrityTests(TestCase):
    def setUp(self):
        self.db=server.fixture.PurchaseDB()
        self.offer={
            'eligible':True, 'quote_only':True, 'plan_code':'seeker',
            'support_mode':'monthly_recurring', 'currency':'usd',
            'amount_cents':199,'new_period_question_limit':17,'questions_used':17,
            'quoted_period_start_epoch':int(server.fixture.it.START.timestamp()),
            'quoted_period_end_epoch':int(server.fixture.it.END.timestamp()),
            'existing_period_ends_at':server.fixture.it.END.isoformat(),
        }
        self.confirmed={
            'amount_cents':199, 'currency':'usd','new_period_question_limit':17,
            'existing_period_ends_at':self.offer['existing_period_ends_at']
        }

    def stage(self,confirmed=None,quote=None,request=server.fixture.REQUEST):
        return server.fixture.it.m.prepare_stripe_reup_intent(
            user_id=server.fixture.it.UID,connect=self.db.connect,
            expected_livemode=False,now=server.fixture.it.NOW,
            purchase_request_id=request,
            intent_factory=lambda:server.fixture.it.UUID(server.fixture.it.INTENT),
            exhausted_quote=self.offer if quote is None else quote,
            confirmed_terms=self.confirmed if confirmed is None else confirmed,
        )

    def refused(self,confirmed):
        previous=copy.deepcopy(self.db.committed)
        sql_before=len(self.db.sql)
        with self.assertRaises((RuntimeError,ValueError,TypeError)):
            self.stage(confirmed=confirmed)
        self.assertEqual(self.db.committed,previous)
        self.assertFalse(any('insert into billing_transactions' in s
                             for s in self.db.sql[sql_before:]))
        self.assertGreaterEqual(self.db.rollbacks,1)

    def test_valid_reviewed_terms_allow_one_new_intent(self):
        result=self.stage()
        self.assertEqual(result['status'],'prepared')
        self.assertEqual(len(self.db.committed['ledger']),1)
        self.assertEqual(self.db.committed['ledger'][0]['raw_summary_json']['expected_paid_cents'],199)

    def test_forged_cheaper_price_fails_before_ledger(self):
        self.refused(dict(self.confirmed,amount_cents=1))

    def test_forged_higher_price_fails_before_ledger(self):
        self.refused(dict(self.confirmed,amount_cents=299))

    def test_forged_currency_fails_before_ledger(self):
        self.refused(dict(self.confirmed,currency='eur'))

    def test_different_question_limit_fails_before_ledger(self):
        self.refused(dict(self.confirmed,new_period_question_limit=33))

    def test_altered_period_end_fails_before_ledger(self):
        self.refused(dict(self.confirmed,existing_period_ends_at='2099-02-01T00:00:00+00:00'))

    def test_missing_consent_fails_before_ledger(self):
        self.refused({})

    def test_bool_float_or_string_amounts_fail_before_ledger(self):
        for value in (True,199.0,'199',None):
            with self.subTest(value=value):
                self.db=server.fixture.PurchaseDB()
                self.refused(dict(self.confirmed,amount_cents=value))

    def test_bad_limit_and_missing_period_fail_before_ledger(self):
        for field,value in [('new_period_question_limit','17'),
                            ('new_period_question_limit',True),
                            ('existing_period_ends_at',None)]:
            with self.subTest(field=field,value=value):
                self.db=server.fixture.PurchaseDB()
                self.refused(dict(self.confirmed,**{field:value}))

    def test_quote_price_drift_fails_even_if_reviewed_terms_unchanged(self):
        self.db.committed['catalog']['monthly_price_cents']=299
        self.refused(self.confirmed)

    def test_period_drift_fails_even_if_reviewed_terms_unchanged(self):
        self.db.committed['user']['subscription_expires_at']+=server.fixture.it.dt.timedelta(days=1)
        self.refused(self.confirmed)

    def test_historical_pending_request_does_not_require_current_consent(self):
        self.assertEqual(self.stage()['status'],'prepared')
        self.assertEqual(self.stage(confirmed={},quote={'eligible':False})['status'],'already_pending')
        self.assertEqual(len(self.db.committed['ledger']),1)

    def test_historical_settled_request_never_authorizes_another_charge(self):
        self.assertEqual(self.stage()['status'],'prepared')
        row=self.db.committed['ledger'][0]
        row['raw_summary_json'].update(schema='stripe_paid_reup_receipt.v2',
                                       intent_id=server.fixture.it.INTENT,
                                       invoice_id='in_g8_accepted')
        row.update(status='succeeded',provider_invoice_id='in_g8_accepted')
        self.db.committed['user']['entitlement_status']='expired'
        self.assertEqual(self.stage(confirmed={'amount_cents':1},
                                    quote={'eligible':False})['status'],'already_settled')
        self.assertEqual(len(self.db.committed['ledger']),1)

    def test_post_passes_exact_reviewed_fields_to_writer(self):
        h=route.Harness()
        response=h.run()
        self.assertEqual(response['status'],'awaiting_verified_payment')
        terms=h.calls[1][1]['confirmed_terms']
        self.assertEqual(terms,{
            'amount_cents':199,'currency':'usd','new_period_question_limit':17,
            'existing_period_ends_at':'2099-01-01T00:00:00+00:00',
        })
        self.assertEqual(h.names().count('provider'),1)

    def test_post_ineligible_offer_still_fails_closed(self):
        h=route.Harness()
        h.offer={'eligible':False}
        with self.assertRaises(route.FakeHTTPException):h.run()
        self.assertEqual(h.names().count('provider'),0)

    def test_model_fields_required_and_not_client_price_authority(self):
        root=ast.parse((ROOT/'main.py').read_text(encoding='utf8'))
        nodes=[n for n in root.body if isinstance(n,ast.ClassDef)
               and n.name=='StripeEarlyReupInput']
        self.assertEqual(len(nodes),1)
        fields={n.target.id for n in nodes[0].body if isinstance(n,ast.AnnAssign)
                and isinstance(n.target,ast.Name) and n.value is None}
        for name in ('confirm_charge','purchase_request_id','confirmed_amount_cents',
                     'confirmed_currency','confirmed_question_limit',
                     'confirmed_existing_period_ends_at'):
            self.assertIn(name,fields)
        mainroute=next(n for n in root.body if isinstance(n,ast.FunctionDef)
                       and n.name=='billing_stripe_early_reup')
        calls=[ast.unparse(n.func) for n in ast.walk(mainroute) if isinstance(n,ast.Call)]
        self.assertIn('billing_stripe_early_reup_quote',calls)
        self.assertIn('prepare_stripe_reup_intent',calls)
        body=ast.get_source_segment((ROOT/'main.py').read_text(encoding='utf8'),mainroute)
        self.assertIn('confirmed_terms={',body)
        self.assertNotIn('stripe_price_id=payload',body)

    def test_reviewed_browser_terms_are_present_without_native_checkout(self):
        src=(ROOT/'static/temple.js').read_text(encoding='utf8')
        first=src.index('  // A7C5G5: Web-only')
        last=src.index('  // End A7C5G5 web early-renewal review seam.',first)
        block=src[first:last]
        for fragment in (
            'confirmed_amount_cents: stripeReupQuote.amount_cents',
            'confirmed_currency: stripeReupQuote.currency',
            'confirmed_question_limit: stripeReupQuote.new_period_question_limit',
            'confirmed_existing_period_ends_at: stripeReupQuote.existing_period_ends_at',
            'stripeReupQuoteMatches(stripeReupQuote, fresh)',
            '!stripeReupAcknowledge.checked',
            'stripeReupAttempted = true',
        ):
            self.assertIn(fragment,block)
        self.assertNotIn('templeStoreKit',block)
        self.assertNotIn('/billing/checkout-session',block)

    def test_same_price_quote_cannot_override_forged_client(self):
        # The POST supplies client terms, but the *writer* rejects any mismatch.
        self.refused(dict(self.confirmed,amount_cents=199,existing_period_ends_at='2020-01-01'))

if __name__=='__main__':main(verbosity=2)
