"""A7C5G5: source-isolated Seeker early-renewal browser integration tests."""
import html.parser
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(os.environ.get('GODINC_A7C5G5_ROOT', Path(__file__).resolve().parents[1]))
JS = ROOT / 'static/temple.js'
HTML = ROOT / 'templates/temple.html'
NODE_TEST = Path(__file__).with_name('test_stripe_reup_web_ui_vm.js')

class Elements(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = {}
    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if 'id' in d:
            self.ids.setdefault(d['id'], []).append((tag, d))

class BrowserWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = JS.read_text(encoding='utf-8')
        cls.html = HTML.read_text(encoding='utf-8')
        cls.parser = Elements()
        cls.parser.feed(cls.html)
        cls.begin = cls.js.find('  // A7C5G5: Web-only, quote-derived early renewal review.')
        cls.end = cls.js.find('  // End A7C5G5 web early-renewal review seam.')
        assert cls.begin >= 0 and cls.end > cls.begin
        cls.block = cls.js[cls.begin:cls.end + len('  // End A7C5G5 web early-renewal review seam.')]
    def test_all_markup_ids_exactly_once(self):
        ids = ['stripeReupOfferPanel', 'stripeReupOfferCopy', 'stripeReupReviewBtn',
               'stripeReupConfirmPanel', 'stripeReupConfirmCopy',
               'stripeReupAcknowledge', 'stripeReupChargeBtn', 'stripeReupCancelBtn']
        for ident in ids:
            self.assertEqual(len(self.parser.ids.get(ident, [])), 1, ident)
            self.assertEqual(self.block.count('getElementById("'+ident+'")'), 1)
    def test_payment_panels_default_hidden(self):
        for ident in ['stripeReupOfferPanel','stripeReupConfirmPanel']:
            _, attrs = self.parser.ids[ident][0]
            self.assertIn('hidden', attrs)
            self.assertIn('display:none', attrs.get('style',''))
    def test_charge_button_default_disabled(self):
        _, attrs = self.parser.ids['stripeReupChargeBtn'][0]
        self.assertIn('disabled', attrs)
        self.assertEqual(attrs.get('type'), 'button')
        _, check = self.parser.ids['stripeReupAcknowledge'][0]
        self.assertEqual(check.get('type'), 'checkbox')
        self.assertNotIn('checked', check)
    def test_exact_quote_and_purchase_routes(self):
        self.assertEqual(self.block.count('"/billing/stripe/early-reup/quote"'), 1)
        self.assertEqual(self.block.count('"/billing/stripe/early-reup"'), 1)
        self.assertIn('confirm_charge: true', self.block)
        self.assertIn('purchase_request_id: purchaseRequestId', self.block)
    def test_sensitive_charge_requires_checked_acknowledgment(self):
        self.assertIn('!stripeReupAcknowledge.checked',self.block)
        self.assertIn('stripeReupAcknowledge.checked = false',self.block)
        self.assertIn('stripeReupChargeBtn.disabled = true',self.block)
        self.assertIn('requires_explicit_charge_confirmation !== true',self.block)
    def test_single_dispatch_no_auto_retry(self):
        self.assertIn('stripeReupAttempted = true;',self.block)
        self.assertIn('if (stripeReupBusy || stripeReupAttempted ||',self.block)
        self.assertIn('stripeReupQuoteMatches(stripeReupQuote, fresh)',self.block)
        self.assertIn('window.crypto.randomUUID()',self.block)
        self.assertNotIn('setInterval',self.block)
        self.assertNotIn('setTimeout',self.block)
    def test_server_paid_invoice_only_not_immediate_grant(self):
        self.assertIn('result.status === "awaiting_verified_payment"',self.block)
        self.assertIn('result.payment_confirmed === false',self.block)
        self.assertIn('result.questions_replenished === false',self.block)
        self.assertIn('NOT yet been verified',self.block)
    def test_no_apple_storekit_or_legacy_checkout_interference(self):
        self.assertNotIn('templeStoreKit',self.block)
        self.assertNotIn('/billing/checkout-session',self.block)
        self.assertIn('!isNativeIOSApp()',self.block)
        self.assertEqual(self.js.count('function launchAppleStoreKitCheckout('),1)
        self.assertEqual(self.js.count('async function launchStripeCheckout('),1)
    def test_once_only_modal_lifecycle_integration(self):
        self.assertEqual(self.js.count('void refreshStripeReupQuote();'),1)
        self.assertEqual(self.js.count('function renderSupportModal()'),1)
        self.assertEqual(self.block.count('addEventListener("click", reviewStripeReup)'),1)
    def test_asset_cache_version_and_confirmation_copy(self):
        self.assertIn('/static/temple.js?v=20261116-s16d13-r1-candidate-auth1',self.html)
        self.assertIn('existing recurring Stripe subscription',self.html)
        self.assertIn('billing date will shift',self.block)
        self.assertIn('new_period_question_limit',self.block)
    @unittest.skipUnless(shutil.which('node'), 'Node unavailable: browser runtime acceptance remains OPEN')
    def test_isolated_javascript_behavior_and_full_parse(self):
        check = subprocess.run(['node','--check',str(JS)],capture_output=True,text=True,timeout=30)
        self.assertEqual(check.returncode,0,check.stderr[:800])
        with tempfile.TemporaryDirectory(prefix='godinc-g5-ui-vm-') as temp:
            block_file = Path(temp)/'ui_block.js'
            block_file.write_text(self.block, encoding='utf-8')
            env = os.environ.copy();env['GODINC_A7C5G5_UI_BLOCK']=str(block_file)
            cmd = subprocess.run(['node',str(NODE_TEST)],capture_output=True,text=True,env=env,timeout=40)
            self.assertEqual(cmd.returncode,0,(cmd.stdout+cmd.stderr)[-1400:])
            self.assertIn('RESULT: PASS 16 isolated web UI runtime tests',cmd.stdout)

if __name__=='__main__':
    unittest.main(verbosity=2)
