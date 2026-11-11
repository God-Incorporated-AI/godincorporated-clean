"""A7C5D: offline AST-executed webhook bridge tests; no HTTP, DB or Stripe.

The real main.py is parsed but not imported, so the accepted application and
provider rails are not started. The bridge is exercised with pure mocks.
"""
import ast
import importlib.util
import logging
import os
from pathlib import Path
from unittest import TestCase, main, mock

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / 'main.py'
SERVICE = ROOT / 'services' / 'stripe_reup_settlement.py'
MODE = 'GODINC_STRIPE_REUP_WEBHOOK_MODE'
UID = '24d20b0d-c073-466e-a010-08c3db27b6d1'
INTENT = 'e1cdb624-ff3b-429c-95df-4ad279e0f4ef'


def load_classifier():
    spec = importlib.util.spec_from_file_location('a7c5d_classifier', SERVICE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.classify_stripe_reup_invoice_event


def make_event(kind='invoice.paid', reason='subscription_update', source='phase8_paid_reup',
               event_id='evt_a7c5d_1', intent=INTENT, legacy=False):
    metadata = {'user_id': UID, 'change_source': source}
    if intent is not None:
        metadata['reup_intent_id'] = intent
    binding = {'subscription': 'sub_a7c5d_1', 'metadata': metadata}
    invoice = {
        'object': 'invoice', 'id': 'in_a7c5d_1', 'billing_reason': reason,
        'customer': 'cus_a7c5d_1', 'subscription': 'sub_a7c5d_1',
        'livemode': False, 'paid': kind == 'invoice.paid',
    }
    if legacy:
        invoice['subscription_details'] = binding
    else:
        invoice['parent'] = {'subscription_details': binding}
    return {'id': event_id, 'type': kind, 'livemode': False,
            'created': 1792700000, 'data': {'object': invoice}}


def get_source_nodes():
    tree = ast.parse(MAIN.read_text(encoding='utf8'), filename=str(MAIN))
    functions = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return tree, functions


class Harness:
    def __init__(self):
        self.calls = []
        self.settle_answer = {'status': 'settled'}
        self.failure_answer = {'status': 'failed_attempt_audited'}
        self.reraise = None
        self._connect = object()
        self._validate = object()
        self.classifier = load_classifier()
        self.context = {
            'user_id': UID, 'plan_code': 'seeker',
            'support_mode': 'monthly_recurring', 'subscription_row_id': 'internal_sub',
            'user_email': 'not-persisted@example.invalid', 'display_name': 'test',
        }

        def capture(name, returns=None):
            def fn(*args, **kwargs):
                self.calls.append((name, args, kwargs))
                return returns
            return fn

        def route_paid(event, **kwargs):
            self.calls.append(('settle', (event,), kwargs))
            if self.reraise:
                raise self.reraise
            return self.settle_answer

        def route_failure(event, **kwargs):
            self.calls.append(('audit', (event,), kwargs))
            if self.reraise:
                raise self.reraise
            return self.failure_answer

        def insert(event, user_id):
            self.calls.append(('insert_legacy_event', (event, user_id), {}))
            return True

        self.ns = {
            'os': os,
            'logger': logging.getLogger('a7c5d.tests.null'),
            'stripe_obj_to_plain': lambda event: event,
            'extract_stripe_subscription_id': lambda obj: (obj.get('subscription') or obj.get('id')),
            'classify_stripe_reup_invoice_event': self.classifier,
            'settle_verified_paid_reup': route_paid,
            'audit_failed_stripe_reup_invoice': route_failure,
            'get_db_connection': self._connect,
            'validate_paid_stripe_reup_invoice': self._validate,
            'get_stripe_livemode': lambda: False,
            'resolve_user_and_subscription_context': capture('resolve', self.context),
            'insert_payment_event_if_new': insert,
            'mark_payment_event_processed': capture('mark_legacy_event'),
            'mark_payment_event_error': capture('legacy_error'),
            'upsert_local_stripe_subscription': capture('upsert_sub', dict(self.context)),
            'stripe_ts_to_dt': lambda x: x,
            'apply_subscription_renewal_success': capture('legacy_grant'),
            'apply_subscription_renewal_failure_to_floor': capture('legacy_downgrade'),
            'upsert_billing_transaction_from_invoice': capture('legacy_receipt'),
            'cancel_failed_stripe_subscription': capture('legacy_cancel', {'cancelled': True}),
            'send_upcoming_renewal_email': capture('legacy_email'),
            'set_cancel_at_period_end': capture('legacy_cancel_flag'),
        }
        # Only the function, not the startup/HTTP application, is executed.
        _, functions = get_source_nodes()
        code = compile(ast.fix_missing_locations(ast.Module(
            body=[functions['process_stripe_event']], type_ignores=[])), str(MAIN), 'exec')
        exec(code, self.ns)
        self.run = self.ns['process_stripe_event']

    def call_names(self):
        return [n for n, _, _ in self.calls]


class BridgeTests(TestCase):
    def test_expected_main_source_isolated_and_no_real_runtime_imports(self):
        s = MAIN.read_text(encoding='utf8')
        self.assertEqual(s.count('def process_stripe_event('), 1)
        self.assertEqual(s.count('classify_stripe_reup_invoice_event(event)'), 1)
        webhook_slice = s[s.index('def process_stripe_event('):
                          s.index('@app.get("/billing/apple/server-notifications")')]
        self.assertEqual(webhook_slice.count('GODINC_STRIPE_REUP_WEBHOOK_MODE'), 1)
        self.assertNotIn('stripe.Subscription.modify(', s[s.index('def process_stripe_event('):s.index('@app.get("/billing/apple/server-notifications")')])

    def test_signature_verification_remains_upstream_of_handler(self):
        _, functions = get_source_nodes()
        node = functions['stripe_webhook']
        ops = [ast.unparse(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)]
        self.assertIn('stripe.Webhook.construct_event', ops)
        self.assertIn('process_stripe_event', ops)
        signature_line = next(n.lineno for n in ast.walk(node) if isinstance(n, ast.Call) and ast.unparse(n.func) == 'stripe.Webhook.construct_event')
        handler_line = next(n.lineno for n in ast.walk(node) if isinstance(n, ast.Call) and ast.unparse(n.func) == 'process_stripe_event')
        self.assertLess(signature_line, handler_line)
        self.assertNotIn('process_stripe_event(', MAIN.read_text().split('@app.post("/billing/stripe/webhook")')[0].split('def process_stripe_event(')[0])

    def test_explicit_paid_default_disabled_fails_before_legacy_write(self):
        h = Harness()
        with mock.patch.dict(os.environ, {MODE: ''}):
            with self.assertRaisesRegex(RuntimeError, 'not enabled'):
                h.run(make_event())
        self.assertEqual(h.calls, [])

    def test_explicit_failed_default_disabled_cannot_downgrade(self):
        h = Harness()
        with mock.patch.dict(os.environ, {MODE: ''}):
            with self.assertRaisesRegex(RuntimeError, 'not enabled'):
                h.run(make_event('invoice.payment_failed'))
        self.assertEqual(h.calls, [])

    def test_noncanonical_enable_values_do_not_activate(self):
        for val in ('true', 'enabled', 'verified-settlement', 'VERIFIED_SETTLEMENT', '1'):
            h = Harness()
            with self.subTest(val=val), mock.patch.dict(os.environ, {MODE: val}):
                with self.assertRaises(RuntimeError): h.run(make_event())
                self.assertEqual(h.calls, [])

    def test_paid_routes_once_atomically_with_expected_dependencies(self):
        h = Harness()
        with mock.patch.dict(os.environ, {MODE: 'verified_settlement'}):
            result = h.run(make_event())
        self.assertEqual(h.call_names(), ['settle'])
        self.assertEqual(result['reup_status'], 'settled')
        self.assertEqual(result['helper_name'], 'settle_verified_paid_reup')
        self.assertFalse(result['duplicate'])
        self.assertIs(h.calls[0][2]['connect'], h._connect)
        self.assertIs(h.calls[0][2]['validate_invoice'], h._validate)
        self.assertIs(h.calls[0][2]['expected_livemode'], False)

    def test_failed_routes_audit_and_preserves_original_period(self):
        h = Harness()
        with mock.patch.dict(os.environ, {MODE: 'verified_settlement'}):
            result = h.run(make_event('invoice.payment_failed'))
        self.assertEqual(h.call_names(), ['audit'])
        self.assertEqual(result['helper_name'], 'audit_failed_stripe_reup_invoice')
        self.assertEqual(result['reup_status'], 'failed_attempt_audited')
        self.assertIs(h.calls[0][2]['connect'], h._connect)
        self.assertIs(h.calls[0][2]['expected_livemode'], False)

    def test_historical_duplicate_no_replenishment(self):
        h = Harness()
        h.settle_answer = {'status': 'duplicate'}
        with mock.patch.dict(os.environ, {MODE: 'verified_settlement'}):
            result = h.run(make_event())
        self.assertTrue(result['duplicate'])
        self.assertEqual(h.call_names(), ['settle'])

    def test_unexpected_writer_result_is_not_reported_as_success(self):
        h = Harness()
        h.settle_answer = {'status': 'unrecognized'}
        with mock.patch.dict(os.environ, {MODE: 'verified_settlement'}):
            with self.assertRaisesRegex(RuntimeError, 'unverified result'):
                h.run(make_event())
        self.assertEqual(h.call_names(), ['settle'])

    def test_failed_writer_never_falls_back_or_marks_legacy_event(self):
        for kind in ('invoice.paid', 'invoice.payment_failed'):
            h = Harness(); h.reraise = RuntimeError('expected writer failure')
            with self.subTest(kind=kind), mock.patch.dict(os.environ, {MODE: 'verified_settlement'}):
                with self.assertRaisesRegex(RuntimeError, 'expected writer failure'):
                    h.run(make_event(kind))
                self.assertEqual(h.call_names(), ['settle' if kind == 'invoice.paid' else 'audit'])

    def test_unknown_midcycle_invoice_fails_closed_without_mutation(self):
        for val in ('unknown', '', None):
            h = Harness()
            with self.subTest(source=val), mock.patch.dict(os.environ, {MODE: 'verified_settlement'}):
                with self.assertRaisesRegex(ValueError, 'Unclassified'):
                    h.run(make_event(source=val, intent=None))
                self.assertEqual(h.calls, [])

    def test_wrong_intent_fails_before_legacy_even_when_disabled(self):
        h = Harness()
        with mock.patch.dict(os.environ, {MODE: ''}):
            with self.assertRaises(ValueError):
                h.run(make_event(intent='fake'))
        self.assertEqual(h.calls, [])

    def test_existing_subscription_cycle_paid_preserves_original_legacy_processing(self):
        h = Harness()
        with mock.patch.dict(os.environ, {MODE: ''}):
            result = h.run(make_event(reason='subscription_cycle'))
        self.assertEqual(result['event_type'], 'invoice.paid')
        self.assertIn('insert_legacy_event', h.call_names())
        self.assertIn('legacy_grant', h.call_names())
        self.assertIn('legacy_receipt', h.call_names())
        self.assertIn('mark_legacy_event', h.call_names())
        self.assertNotIn('settle', h.call_names())

    def test_existing_checkout_invoice_preserved_with_flag_enabled(self):
        h = Harness()
        with mock.patch.dict(os.environ, {MODE: 'verified_settlement'}):
            h.run(make_event(reason='subscription_create'))
        self.assertIn('legacy_grant', h.call_names())
        self.assertNotIn('settle', h.call_names())

    def test_existing_plan_change_invoice_preserved(self):
        h = Harness()
        with mock.patch.dict(os.environ, {MODE: 'verified_settlement'}):
            h.run(make_event(source='phase8_change_plan', intent=None))
        self.assertIn('legacy_grant', h.call_names())
        self.assertNotIn('settle', h.call_names())

    def test_existing_regular_renewal_failure_preserved(self):
        h = Harness()
        with mock.patch.dict(os.environ, {MODE: 'verified_settlement'}):
            h.run(make_event('invoice.payment_failed', reason='subscription_cycle'))
        self.assertIn('legacy_downgrade', h.call_names())
        self.assertIn('legacy_cancel', h.call_names())
        self.assertNotIn('audit', h.call_names())

    def test_unrelated_subscription_updates_remain_legacy(self):
        h = Harness()
        e = {'id': 'evt_update', 'type': 'customer.subscription.updated',
             'data': {'object': {'object': 'subscription', 'customer': 'cus_a7c5d_1',
                                 'id': 'sub_a7c5d_1', 'cancel_at_period_end': False}}}
        with mock.patch.dict(os.environ, {MODE: ''}):
            h.run(e)
        self.assertIn('upsert_sub', h.call_names())
        self.assertIn('legacy_cancel_flag', h.call_names())
        self.assertNotIn('settle', h.call_names())

    def test_unrelated_upcoming_stays_in_legacy(self):
        h = Harness()
        with mock.patch.dict(os.environ, {MODE: ''}):
            h.run(make_event(kind='invoice.upcoming'))
        self.assertIn('legacy_email', h.call_names())
        self.assertIn('mark_legacy_event', h.call_names())

    def test_classification_precedes_context_and_event_insert(self):
        _, functions = get_source_nodes()
        body = functions['process_stripe_event'].body
        calls = [(ast.unparse(n.func), n.lineno) for n in ast.walk(functions['process_stripe_event']) if isinstance(n, ast.Call)]
        classify = [line for name, line in calls if name == 'classify_stripe_reup_invoice_event']
        inserts = [line for name, line in calls if name == 'insert_payment_event_if_new']
        resolves = [line for name, line in calls if name == 'resolve_user_and_subscription_context']
        self.assertEqual(len(classify), 1)
        self.assertEqual(len(inserts), 1)
        self.assertTrue(all(classify[0] < n for n in inserts + resolves))

    def test_existing_billing_not_replaced_by_early_reup_route(self):
        data = MAIN.read_text(encoding='utf8')
        self.assertEqual(data.count('@app.post("/billing/stripe/early-reup")'), 1)
        self.assertEqual(data.count('@app.post("/billing/checkout-session")'), 1)
        source = ast.parse(data)
        urls = [ast.unparse(n) for n in ast.walk(source) if isinstance(n, ast.Call) and ast.unparse(n.func) == 'stripe.Subscription.modify']
        self.assertEqual(urls, [])


if __name__ == '__main__':
    main(verbosity=2)
