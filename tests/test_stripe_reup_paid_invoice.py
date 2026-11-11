"""A7C2: offline strict paid Stripe early re-up invoice evidence tests.

AST-load only selected function, no Stripe import, network or database.
"""
import ast
import copy
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'services' / 'stripe_billing.py'
OLD_START = 1700000000
START = OLD_START + 17 * 86400
END = START + 30 * 86400


def invoice(*, modern=False):
    line = dict(amount=199, period=dict(start=START, end=END), proration=False)
    if modern:
        line['pricing'] = dict(price_details=dict(price='price_seeker'))
        line['parent'] = dict(subscription_item_details=dict(subscription='sub_1', proration=False))
    else:
        line['price'] = dict(id='price_seeker')
    result = dict(
        id='in_1', object='invoice', status='paid', paid=True, paid_out_of_band=False,
        billing_reason='subscription_update', currency='usd',
        livemode=False, customer='cus_1', amount_paid=199,
        total=199, amount_remaining=0,
        lines=dict(data=[line], has_more=False),
    )
    details = dict(subscription='sub_1', metadata=dict(change_source='phase8_paid_reup', user_id='user_1'))
    if modern:
        result['parent'] = dict(subscription_details=details)
    else:
        result['subscription'] = 'sub_1'
        result['subscription_details'] = details
    return result


class PaidReupInvoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        unit = ast.parse(SOURCE.read_text('utf-8'))
        target = [node for node in unit.body if isinstance(node, ast.FunctionDef)
                  and node.name == 'validate_paid_stripe_reup_invoice']
        if len(target) != 1:
            raise AssertionError('Exactly one invoice validator required')
        ns = {}
        exec(compile(ast.Module(body=target, type_ignores=[]), str(SOURCE), 'exec'), ns)
        cls.validate = staticmethod(ns['validate_paid_stripe_reup_invoice'])

    def ok(self, item, **kw):
        kwargs = dict(expected_user_id='user_1', expected_subscription_id='sub_1',
                      expected_customer_id='cus_1', expected_price_id='price_seeker',
                      expected_livemode=False, minimum_paid_cents=199,
                      previous_period_start=OLD_START)
        kwargs.update(kw)
        return self.validate(item, **kwargs)

    def fails(self, item, **kw):
        with self.assertRaises(ValueError):
            self.ok(item, **kw)

    def test_valid_legacy_invoice(self):
        fact = self.ok(invoice())
        self.assertEqual(fact['period_start'], START)
        self.assertEqual(fact['period_end'], END)
        self.assertEqual(fact['paid_cents'], 199)

    def test_valid_modern_invoice(self):
        fact = self.ok(invoice(modern=True))
        self.assertEqual(fact['invoice_id'], 'in_1')

    def test_not_an_invoice(self):
        for invalid in (None, {}, {'object': 'checkout.session'}, []):
            with self.subTest(item=invalid): self.fails(invalid)

    def test_manual_or_unknown_payment_origin_refuses(self):
        for mode in (True, None, 'false', 0):
            item=invoice(); item['paid_out_of_band']=mode
            with self.subTest(mode=mode): self.fails(item)
        item=invoice(); item.pop('paid_out_of_band'); self.fails(item)

    def test_payment_not_confirmed(self):
        for status, paid in (('open',False), ('paid',False), ('void',True), ('paid','yes')):
            item=invoice(); item.update(status=status,paid=paid)
            with self.subTest(status=status, paid=paid): self.fails(item)

    def test_no_free_or_partial_payment(self):
        for cents in (0,99,198,None,True,'199'):
            item=invoice();item['amount_paid']=cents
            with self.subTest(cents=cents): self.fails(item)

    def test_invalid_invoice_total(self):
        for cents in (0,198,None,True,'199'):
            item=invoice();item['total']=cents
            with self.subTest(total=cents): self.fails(item)

    def test_balance_and_overpay_refused(self):
        item=invoice(); item['amount_remaining']=1; self.fails(item)
        item=invoice(); item['amount_paid']=400; self.fails(item)

    def test_currency_and_environment(self):
        item=invoice();item['currency']='eur';self.fails(item)
        item=invoice();item['livemode']=True;self.fails(item)
        item=invoice();item['livemode']=0;self.fails(item)
        self.fails(invoice(), expected_livemode=0)

    def test_wrong_customer(self):
        item=invoice();item['customer']='cus_other';self.fails(item)
        self.fails(invoice(), expected_customer_id='cus_other')

    def test_wrong_subscription(self):
        item=invoice();item['subscription']='sub_other';self.fails(item)
        item=invoice(modern=True);item['parent']['subscription_details']['subscription']='sub_other';self.fails(item)

    def test_wrong_account(self):
        item=invoice();item['subscription_details']['metadata']['user_id']='user_other';self.fails(item)
        self.fails(invoice(), expected_user_id='user_other')

    def test_marker_required(self):
        item=invoice();item['subscription_details']['metadata']={};self.fails(item)
        item=invoice();item['subscription_details']['metadata']['change_source']='phase8_change_plan';self.fails(item)

    def test_not_ordinary_renewal(self):
        for reason in ('subscription_cycle','subscription_create','manual',None):
            item=invoice();item['billing_reason']=reason
            with self.subTest(reason=reason): self.fails(item)

    def test_conflicting_subscription_fields(self):
        item=invoice(modern=True);item['subscription']='sub_other';self.fails(item)
        item=invoice(modern=True);item['subscription']='sub_1'
        item['parent']['subscription_details']['subscription']='sub_other';self.fails(item)

    def test_conflicting_price_fields(self):
        item=invoice();item['lines']['data'][0]['pricing']=dict(price_details=dict(price='price_other'))
        self.fails(item)

    def test_line_currency_and_quantity(self):
        item=invoice();item['lines']['data'][0]['currency']='eur';self.fails(item)
        for quantity in (0,2,True,'1'):
            item=invoice();item['lines']['data'][0]['quantity']=quantity
            with self.subTest(quantity=quantity):self.fails(item)

    def test_price_match_required(self):
        item=invoice();item['lines']['data'][0]['price']['id']='price_magus';self.fails(item)
        item=invoice(modern=True);item['lines']['data'][0]['pricing']['price_details']['price']='price_magus';self.fails(item)

    def test_line_must_cover_month(self):
        for cents in (198,None,True):
            item=invoice();item['lines']['data'][0]['amount']=cents
            with self.subTest(cents=cents): self.fails(item)

    def test_prior_period_not_renewed(self):
        item=invoice(); item['lines']['data'][0]['period']['start']=OLD_START;self.fails(item)
        self.fails(invoice(), previous_period_start=START)
        self.fails(invoice(), previous_period_start=None)

    def test_invalid_service_period(self):
        for start, end in ((START, START),(START, START+25*86400),(START, START+35*86400),
                           ('not-int',END),(True,END),(START,'not-int')):
            item=invoice();item['lines']['data'][0]['period']=dict(start=start,end=end)
            with self.subTest(start=start,end=end): self.fails(item)

    def test_reject_incomplete_or_multiple_lines(self):
        item=invoice();item['lines']['has_more']=True;self.fails(item)
        item=invoice();item['lines']['data'].append(copy.deepcopy(item['lines']['data'][0]));self.fails(item)
        item=invoice();item['lines']['data']=[];self.fails(item)

    def test_reject_proration_or_foreign_line(self):
        item=invoice();item['lines']['data'][0]['proration']=True;self.fails(item)
        item=invoice(modern=True);item['lines']['data'][0]['parent']['subscription_item_details']['proration']=True;self.fails(item)
        item=invoice(modern=True);item['lines']['data'][0]['parent']['subscription_item_details']['subscription']='sub_other';self.fails(item)

    def test_invoice_id_required(self):
        for ident in (None,'','wrong','in_'):
            item=invoice();item['id']=ident
            with self.subTest(ident=ident): self.fails(item)

    def test_no_dynamic_dependencies(self):
        tree=ast.parse(SOURCE.read_text('utf-8'))
        target=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='validate_paid_stripe_reup_invoice')
        calls={n.func.id for n in ast.walk(target) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)}
        self.assertFalse(calls.intersection({'get_db_connection','init_stripe','requests','open'}))
        self.assertTrue({'insist','id_of'}.issubset(calls))


if __name__=='__main__':
    unittest.main(verbosity=2)
