"""A7C5B explicit invoice routing; pure source, no provider or DB."""
import ast
import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'services' / 'stripe_reup_settlement.py'


def event(kind='invoice.paid', reason='subscription_update', source='phase8_paid_reup',
          intent='123e4567-e89b-42d3-a456-426614174000', legacy=False):
    meta = {'user_id': 'user1', 'change_source': source}
    if intent is not None:
        meta['reup_intent_id'] = intent
    details = {'subscription': 'sub_1', 'metadata': meta}
    invoice = {'object': 'invoice', 'id': 'in_1', 'billing_reason': reason,
               'customer': 'cus_1', 'subscription': 'sub_1'}
    if legacy:
        invoice['subscription_details'] = details
    else:
        invoice['parent'] = {'subscription_details': details}
    return {'id': 'evt_1', 'type': kind, 'data': {'object': invoice}}


class ClassifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('a7c5b_pure_classification', SOURCE)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.classify = staticmethod(cls.module.classify_stripe_reup_invoice_event)

    def test_paid_reup_modern(self):
        result = self.classify(event())
        self.assertEqual(result, {
            'route': 'reup_paid', 'event_type': 'invoice.paid',
            'invoice_id': 'in_1', 'subscription_id': 'sub_1',
            'intent_id': '123e4567-e89b-42d3-a456-426614174000'})

    def test_paid_reup_legacy(self):
        self.assertEqual(self.classify(event(legacy=True))['route'], 'reup_paid')

    def test_failed_reup_never_routes_to_legacy_renewal_failure(self):
        self.assertEqual(self.classify(event(kind='invoice.payment_failed'))['route'],
                         'reup_payment_failed')

    def test_original_upgrade_preserved(self):
        self.assertIsNone(self.classify(event(source='phase8_change_plan', intent=None)))

    def test_regular_cycle_preserved_even_with_sticky_metadata(self):
        for kind in ('invoice.paid', 'invoice.payment_failed'):
            with self.subTest(kind=kind):
                self.assertIsNone(self.classify(event(kind=kind, reason='subscription_cycle')))

    def test_original_checkout_preserved(self):
        self.assertIsNone(self.classify(event(reason='subscription_create')))

    def test_unrelated_events_preserved(self):
        for kind in ('invoice.upcoming', 'checkout.session.completed', 'customer.subscription.updated'):
            with self.subTest(kind=kind):
                self.assertIsNone(self.classify(event(kind=kind)))

    def test_unknown_midcycle_update_rejected(self):
        for source in (None, '', 'unknown', 'phase8_checkout'):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, 'Unclassified'):
                    self.classify(event(source=source, intent=None))

    def test_intent_in_plan_change_is_inconsistent(self):
        with self.assertRaisesRegex(ValueError, 'Unclassified'):
            self.classify(event(source='phase8_change_plan'))

    def test_missing_reup_uuid_rejected(self):
        with self.assertRaisesRegex(ValueError, 'UUID'):
            self.classify(event(intent=None))

    def test_malformed_reup_uuid_rejected(self):
        for bad in ('', 'not-a-uuid', '123e4567-e89b-12d3-a456-426614174000'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError): self.classify(event(intent=bad))

    def test_noncanonical_uuid_rejected(self):
        with self.assertRaises(ValueError):
            self.classify(event(intent='123E4567-E89B-42D3-A456-426614174000'))

    def test_missing_invoice_id_rejected(self):
        e = event();e['data']['object']['id'] = None
        with self.assertRaisesRegex(ValueError, 'invoice identity'):
            self.classify(e)

    def test_unknown_billing_reason_refused(self):
        for reason in (None, 'manual', 'subscription_threshold'):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ValueError, 'billing reason'):
                    self.classify(event(reason=reason))

    def test_wrong_event_shape_rejected(self):
        for e in (None, [], {'type': 'invoice.paid', 'data': []}):
            with self.subTest(value=e):
                with self.assertRaises(ValueError): self.classify(e)

    def test_unknown_event_without_type_is_ignored(self):
        self.assertIsNone(self.classify({}))

    def test_malformed_invoice_object_rejected(self):
        e=event();e['data']['object']={'object':'subscription'}
        with self.assertRaises(ValueError): self.classify(e)

    def test_bad_parent_rejected(self):
        e=event();e['data']['object']['parent']='bad'
        with self.assertRaises(ValueError): self.classify(e)

    def test_bad_metadata_rejected(self):
        e=event();e['data']['object']['parent']['subscription_details']['metadata']='bad'
        with self.assertRaises(ValueError): self.classify(e)

    def test_legacy_and_modern_matching_ok(self):
        e=event()
        d=e['data']['object']['parent']['subscription_details']
        e['data']['object']['subscription_details']={'subscription': d['subscription'], 'metadata': dict(d['metadata'])}
        self.assertEqual(self.classify(e)['route'], 'reup_paid')

    def test_legacy_and_modern_conflicting_intent_rejected(self):
        e=event()
        e['data']['object']['subscription_details']={'subscription':'sub_1',
            'metadata': {'change_source':'phase8_paid_reup', 'user_id':'user1',
                         'reup_intent_id':'123e4567-e89b-42d3-a456-426614174001'}}
        with self.assertRaisesRegex(ValueError,'Contradictory'):
            self.classify(e)

    def test_legacy_and_modern_conflicting_account_rejected(self):
        e=event()
        d=e['data']['object']['parent']['subscription_details']
        e['data']['object']['subscription_details']={'subscription': d['subscription'],
            'metadata': dict(d['metadata'], user_id='user2')}
        with self.assertRaisesRegex(ValueError,'Contradictory'):
            self.classify(e)

    def test_legacy_and_modern_conflicting_subscription_rejected(self):
        e=event()
        d=e['data']['object']['parent']['subscription_details']
        e['data']['object']['subscription_details']={'subscription':'sub_other',
            'metadata':dict(d['metadata'])}
        with self.assertRaisesRegex(ValueError,'Contradictory'):
            self.classify(e)

    def test_legacy_and_modern_conflicting_marker_rejected(self):
        e=event()
        d=e['data']['object']['parent']['subscription_details']
        e['data']['object']['subscription_details']={'subscription':d['subscription'],
            'metadata':dict(d['metadata'], change_source='phase8_change_plan')}
        with self.assertRaisesRegex(ValueError,'Contradictory'):
            self.classify(e)

    def test_classifier_no_db_provider_or_writer_calls(self):
        tree=ast.parse(SOURCE.read_text('utf8'))
        node=next(x for x in tree.body if isinstance(x,ast.FunctionDef)
                  and x.name=='classify_stripe_reup_invoice_event')
        calls={x.func.id for x in ast.walk(node) if isinstance(x,ast.Call)
               and isinstance(x.func,ast.Name)}
        self.assertFalse(calls.intersection({
            'get_db_connection', 'stripe', 'apply_subscription_renewal_success',
            'apply_subscription_renewal_failure_to_floor', 'settle_verified_paid_reup',
            'transition_stripe_reup_intent', 'insert_payment_event_if_new',
        }))
        self.assertEqual(tree.body[-1].name, 'classify_stripe_reup_invoice_event')


if __name__ == '__main__':
    unittest.main(verbosity=2)
