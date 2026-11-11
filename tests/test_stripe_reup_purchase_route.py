"""A7C5E: AST-only authenticated early-reup purchase routing; zero Stripe/DB/HTTP."""
import ast
import copy
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from unittest import TestCase, main, mock

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT/'main.py'
USER = '24d20b0d-c073-466e-a010-08c3db27b6d1'
INTENT = 'e1cdb624-ff3b-429c-95df-4ad279e0f4ef'
REQUEST = '799eb1d9-49b0-4e90-858b-e12d907dfda7'
PURCHASE_FLAG = 'GODINC_STRIPE_REUP_PURCHASE_MODE'
WEBHOOK_FLAG = 'GODINC_STRIPE_REUP_WEBHOOK_MODE'


class FakeHTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


class Harness:
    def __init__(self):
        self.calls = []
        self.account = {'user_id': USER, 'email': 'user@example.invalid',
                        'display_name': 'Synthetic Only'}
        self.prepared = {'status':'prepared','intent_id':INTENT,
                         'plan_code':'seeker','expected_paid_cents':199}
        self.dispatch = {
            'user_id':USER,'stripe_subscription_id':'sub_a7c5e_1',
            'plan_code':'seeker','expected_paid_cents':199,'livemode':False,
        }
        self.claim_answer = {'status':'dispatching','dispatch':self.dispatch}
        self.confirm_answer = {'status':'sent'}
        self.uncertain_answer = {'status':'uncertain'}
        self.provider_answer = {
            'changed_subscription':True,'pending_paid_invoice':True,
            'plan_code':'seeker','support_mode':'monthly_recurring','livemode':False,
            'subscription_obj':{'id':'sub_a7c5e_1','customer':'cus_a7c5e_1',
                                'metadata':{'reup_intent_id':INTENT}},
        }
        self.offer = {"eligible": True, "quote_only": True}
        self.fault = None
        self.hold_fault = False
        self.service_dao = object()
        self.request = SimpleNamespace(base_url='https://temple.example/', headers={
            'origin':'https://temple.example','sec-fetch-site':'same-origin'})
        self.payload = SimpleNamespace(confirm_charge=True, purchase_request_id=REQUEST,
                                       confirmed_amount_cents=199, confirmed_currency="usd",
                                       confirmed_question_limit=17,
                                       confirmed_existing_period_ends_at="2099-01-01T00:00:00+00:00")
        def prepare(**kwargs):
            self.calls.append(('prepare',kwargs))
            if self.fault == 'prepare': raise RuntimeError('synthetic prepare error')
            if kwargs.get('exhausted_quote', {}).get('eligible') is not True:
                raise RuntimeError('synthetic unverified offer')
            return copy.deepcopy(self.prepared)
        def transition(**kwargs):
            action=kwargs['action']
            self.calls.append((action,kwargs))
            if action == 'claim':
                if self.fault == 'claim': raise RuntimeError('synthetic claim error')
                return copy.deepcopy(self.claim_answer)
            if action == 'confirm':
                if self.fault == 'confirm': raise RuntimeError('synthetic confirm failure')
                return copy.deepcopy(self.confirm_answer)
            if action == 'uncertain':
                if self.hold_fault: raise RuntimeError('synthetic hold failure')
                return copy.deepcopy(self.uncertain_answer)
            raise AssertionError(action)
        def provider(**kwargs):
            self.calls.append(('provider',kwargs))
            if self.fault == 'provider': raise RuntimeError('synthetic Stripe timeout')
            return copy.deepcopy(self.provider_answer)
        def current_user(request):
            self.calls.append(('auth',{}))
            return self.account
        import uuid
        ns = dict(os=os,uuid=uuid,HTTPException=FakeHTTPException,Request=object,
                  StripeEarlyReupInput=object,
                  STRIPE_SECRET_KEY='synthetic_enabled',
                  STRIPE_WEBHOOK_SECRET='synthetic_signed',
                  urlsplit=urlsplit, logger=logging.getLogger('a7c5e.synthetic'),
                  get_current_user=current_user,
                  billing_stripe_early_reup_quote=lambda request: self.offer,
                  get_db_connection=lambda: self.service_dao,
                  get_stripe_livemode=lambda:False,
                  prepare_stripe_reup_intent=prepare,
                  transition_stripe_reup_intent=transition,
                  change_existing_subscription_plan=provider,
                  stripe_obj_to_plain=lambda x:x)
        tree=ast.parse(MAIN.read_text())
        route=[n for n in tree.body if isinstance(n,ast.FunctionDef)
               and n.name == 'billing_stripe_early_reup']
        assert len(route)==1
        route_node=copy.deepcopy(route[0])
        route_node.decorator_list=[]
        exec(compile(ast.fix_missing_locations(ast.Module(body=[route_node],type_ignores=[])),
                     str(MAIN),'exec'),ns)
        self.fn=ns['billing_stripe_early_reup']
        self.ns=ns

    def run(self, enabled=True, webhook=True):
        values={PURCHASE_FLAG:'enabled' if enabled else '',
                WEBHOOK_FLAG:'verified_settlement' if webhook else '',
                'APP_BASE_URL':'https://temple.example'}
        with mock.patch.dict(os.environ,values):
            return self.fn(self.request,self.payload)

    def names(self): return [name for name,_ in self.calls]


class PurchaseRouteTests(TestCase):
    def raises_code(self,h,code,**kwargs):
        with self.assertRaises(FakeHTTPException) as context: h.run(**kwargs)
        self.assertEqual(context.exception.status_code,code)
        return context.exception

    def test_default_disabled_never_calls_auth_provider_or_db(self):
        h=Harness(); self.raises_code(h,404,enabled=False)
        self.assertEqual(h.calls,[])

    def test_noncannonical_enable_strings_never_activate(self):
        for value in ('1','true','ENABLED','enable','on','enabled '):
            h=Harness()
            with self.subTest(value=value),mock.patch.dict(os.environ,
                {PURCHASE_FLAG:value,WEBHOOK_FLAG:'verified_settlement'}):
                with self.assertRaises(FakeHTTPException) as e: h.fn(h.request,h.payload)
                self.assertEqual(e.exception.status_code,404)
                self.assertEqual(h.calls,[])

    def test_disabled_webhook_prevents_purchase(self):
        h=Harness(); self.raises_code(h,503,webhook=False)
        self.assertEqual(h.calls,[])

    def test_missing_provider_secret_prevents_purchase(self):
        h=Harness(); h.ns['STRIPE_SECRET_KEY']=None
        self.raises_code(h,503);self.assertEqual(h.calls,[])

    def test_missing_webhook_secret_prevents_purchase(self):
        h=Harness(); h.ns['STRIPE_WEBHOOK_SECRET']=None
        self.raises_code(h,503);self.assertEqual(h.calls,[])

    def test_strict_origin_requires_origin_header(self):
        h=Harness(); h.request.headers.pop('origin')
        self.raises_code(h,403);self.assertEqual(h.calls,[])

    def test_strict_origin_refuses_different_origin(self):
        for origin in ('https://evil.example','http://temple.example',
                       'https://temple.example:443','null',
                       'https://temple.example/evil','https://temple.example?x=1',
                       'https://temple.example#part','https://temple.example.evil'):
            h=Harness();h.request.headers['origin']=origin
            with self.subTest(origin=origin):
                self.raises_code(h,403);self.assertEqual(h.calls,[])

    def test_cross_site_fetch_metadata_refused(self):
        for value in ('cross-site','same-site','none'):
            h=Harness();h.request.headers['sec-fetch-site']=value
            with self.subTest(value=value):
                self.raises_code(h,403);self.assertEqual(h.calls,[])

    def test_origin_derived_from_request_if_config_missing(self):
        h=Harness()
        with mock.patch.dict(os.environ,{PURCHASE_FLAG:'enabled',
                    WEBHOOK_FLAG:'verified_settlement'},clear=True):
            # Keys are verified from module initialization in the real app.
            result=h.fn(h.request,h.payload)
        self.assertEqual(result['status'],'awaiting_verified_payment')
        self.assertEqual(h.names().count('provider'),1)

    def test_invalid_explicit_confirmation_refused(self):
        for value in (False,None,'true',1,0):
            h=Harness();h.payload.confirm_charge=value
            with self.subTest(value=value):
                self.raises_code(h,400);self.assertEqual(h.calls,[])

    def test_unverified_session_cannot_prepare(self):
        h=Harness();h.account=None
        self.raises_code(h,401)
        self.assertEqual(h.names(),['auth'])

    def test_server_session_identity_used_for_prepare(self):
        h=Harness();h.run()
        self.assertEqual(h.calls[1][1]['user_id'],USER)
        self.assertIs(h.calls[1][1]['connect'],h.ns['get_db_connection'])
        self.assertEqual(h.calls[1][1]['purchase_request_id'],REQUEST)

    def test_happy_path_stages_claims_dispatches_confirms_once(self):
        h=Harness();result=h.run()
        self.assertEqual(h.names(),['auth','prepare','claim','provider','confirm'])
        self.assertEqual(h.calls[2][1]['intent_id'],INTENT)
        provider=h.calls[3][1]
        self.assertIs(provider['force_paid_reup'],True)
        self.assertEqual(provider['reup_intent_id'],INTENT)
        self.assertEqual(provider['user_id'],USER)
        self.assertEqual(provider['plan_code'],'seeker')
        self.assertEqual(provider['support_mode'],'monthly_recurring')
        self.assertEqual(result['status'],'awaiting_verified_payment')
        self.assertTrue(result['ok'])
        self.assertIs(result['payment_confirmed'],False)
        self.assertIs(result['questions_replenished'],False)

    def test_settled_same_purchase_request_never_dispatches_again(self):
        h=Harness();h.prepared={'status':'already_settled','intent_id':INTENT}
        result=h.run()
        self.assertEqual(result['status'],'already_processed')
        self.assertEqual(h.names(),['auth','prepare'])
        self.assertEqual(result['questions_replenished'],False)

    def test_missing_or_invalid_purchase_request_refused_before_any_io(self):
        for value in (None,'not-a-uuid','A49EB1D9-49B0-4E90-858B-E12D907DFDA7',
                      '00000000-0000-0000-0000-000000000000', 7):
            h=Harness();h.payload.purchase_request_id=value
            with self.subTest(value=value):
                self.raises_code(h,400)
                self.assertEqual(h.calls,[])

    def test_client_request_id_binds_same_authenticated_account(self):
        h=Harness();h.run()
        prepare=h.calls[1][1]
        self.assertEqual(prepare['user_id'],USER)
        self.assertEqual(prepare['purchase_request_id'],REQUEST)

    def test_existing_pending_never_dispatches(self):
        for state in ('prepared','dispatching','sent','uncertain'):
            h=Harness();h.prepared={'status':'already_pending','intent_id':INTENT,
                                    'dispatch_status':state}
            with self.subTest(state=state):
                result=h.run();self.assertEqual(result['status'],'held')
                self.assertEqual(h.names(),['auth','prepare'])

    def test_claim_already_held_never_dispatches(self):
        h=Harness();h.claim_answer={'status':'held','dispatch_status':'uncertain'}
        result=h.run();self.assertEqual(result['status'],'held')
        self.assertEqual(h.names(),['auth','prepare','claim'])

    def test_unexpected_prepare_response_never_dispatches(self):
        h=Harness();h.prepared={'status':'invalid'}
        self.raises_code(h,409);self.assertNotIn('provider',h.names())

    def test_claim_failure_never_dispatches(self):
        h=Harness();h.fault='claim'
        self.raises_code(h,409);self.assertNotIn('provider',h.names())

    def test_prepare_failure_never_dispatches(self):
        h=Harness();h.fault='prepare'
        self.raises_code(h,409);self.assertEqual(h.names(),['auth','prepare'])

    def test_mismatched_claim_account_never_dispatches_and_holds(self):
        h=Harness();h.dispatch['user_id']='not_this_user'
        self.raises_code(h,502)
        self.assertEqual(h.names(),['auth','prepare','claim','uncertain'])

    def test_mismatched_claim_price_never_dispatches_and_holds(self):
        h=Harness();h.dispatch['expected_paid_cents']=999
        self.raises_code(h,502)
        self.assertEqual(h.names(),['auth','prepare','claim','uncertain'])

    def test_mismatched_claim_plan_never_dispatches_and_holds(self):
        h=Harness();h.dispatch['plan_code']='magister'
        self.raises_code(h,502)
        self.assertEqual(h.names(),['auth','prepare','claim','uncertain'])

    def test_mismatched_claim_livemode_never_dispatches_and_holds(self):
        h=Harness();h.dispatch['livemode']=True
        self.raises_code(h,502)
        self.assertEqual(h.names(),['auth','prepare','claim','uncertain'])

    def test_provider_timeout_quarantines_and_does_not_confirm(self):
        h=Harness();h.fault='provider'
        self.raises_code(h,502)
        self.assertEqual(h.names(),['auth','prepare','claim','provider','uncertain'])

    def test_provider_response_missing_paid_invoice_quarantines(self):
        h=Harness();h.provider_answer['pending_paid_invoice']=False
        self.raises_code(h,502)
        self.assertEqual(h.names(),['auth','prepare','claim','provider','uncertain'])

    def test_provider_response_mismatched_plan_quarantines(self):
        h=Harness();h.provider_answer['plan_code']='magister'
        self.raises_code(h,502)
        self.assertEqual(h.names(),['auth','prepare','claim','provider','uncertain'])

    def test_provider_response_mismatched_livemode_quarantines(self):
        h=Harness();h.provider_answer['livemode']=True
        self.raises_code(h,502)
        self.assertEqual(h.names(),['auth','prepare','claim','provider','uncertain'])

    def test_confirm_db_failure_quarantines_without_new_provider_call(self):
        h=Harness();h.fault='confirm'
        self.raises_code(h,502)
        self.assertEqual(h.names(),['auth','prepare','claim','provider','confirm','uncertain'])
        self.assertEqual(h.names().count('provider'),1)

    def test_confirm_unrecognized_does_not_report_success(self):
        h=Harness();h.confirm_answer={'status':'rejected'}
        self.raises_code(h,502);self.assertIn('uncertain',h.names())

    def test_failed_quarantine_does_not_retry_provider(self):
        h=Harness();h.fault='provider';h.hold_fault=True
        self.raises_code(h,502);self.assertEqual(h.names().count('provider'),1)

    def test_static_no_legacy_financial_writers_in_new_route(self):
        tree=ast.parse(MAIN.read_text())
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef)
                  and n.name=='billing_stripe_early_reup')
        calls=[ast.unparse(n.func) for n in ast.walk(node) if isinstance(n,ast.Call)]
        for denied in ('apply_subscription_renewal_success','upsert_local_stripe_subscription',
                       'cancel_failed_stripe_subscription','insert_payment_event_if_new',
                       'upsert_billing_transaction_from_invoice','stripe.Subscription.modify'):
            self.assertNotIn(denied,calls)
        self.assertIn('change_existing_subscription_plan',calls)
        self.assertIn('prepare_stripe_reup_intent',calls)
        self.assertIn('transition_stripe_reup_intent',calls)

    def test_static_only_one_new_purchase_route_and_unmodified_original(self):
        s=MAIN.read_text()
        self.assertEqual(s.count('@app.post("/billing/stripe/early-reup")'),1)
        self.assertEqual(s.count('@app.post("/billing/checkout-session")'),1)
        self.assertEqual(s.count('@app.post("/billing/stripe/webhook")'),1)
        self.assertEqual(s.count('GODINC_STRIPE_REUP_PURCHASE_MODE'),2)
        self.assertIn('confirm_charge: Literal[True]',s)
        self.assertIn('purchase_request_id: str',s)


if __name__=='__main__': main(verbosity=2)
