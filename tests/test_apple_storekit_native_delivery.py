"""A7C5H3B source-bound safety checks for the dormant native StoreKit bridge.

These are static/source contract tests; they cannot simulate iOS StoreKit or prove
that the device compiled, Apple signed a transaction, or the backend is enabled.
"""
import hashlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWIFT_FILE = ROOT / 'ios/Temple/Temple/ContentView.swift'
DELIVERY_FILE = ROOT / 'services/apple_storekit_delivery.py'
MAIN_FILE = ROOT / 'main.py'
SWIFT = SWIFT_FILE.read_text(encoding='utf-8')
DELIVERY = DELIVERY_FILE.read_text(encoding='utf-8')
MAIN = MAIN_FILE.read_text(encoding='utf-8')


def section(start, end):
    assert SWIFT.count(start) == 1, start
    assert SWIFT.count(end) == 1, end
    return SWIFT.split(start, 1)[1].split(end, 1)[0]


class NativeAppleDeliveryContracts(unittest.TestCase):
    def test_exact_seeker_product_id(self):
        self.assertIn('static let seekerMonthlyProductID = "ai.godincorporated.seeker.monthly"', SWIFT)
        self.assertIn('body.product_id == TempleEnvironment.seekerMonthlyProductID', SWIFT)
        self.assertIn('transaction.productID == TempleEnvironment.seekerMonthlyProductID', SWIFT)

    def test_exact_backend_paths_and_session_cookie_transport(self):
        self.assertIn('"billing/apple/account-token", relativeTo: baseAppURL', SWIFT)
        self.assertIn('"billing/apple/verified-transaction", relativeTo: baseAppURL', SWIFT)
        self.assertEqual(SWIFT.count('url: TempleEnvironment.appleAccountTokenURL,'), 1)
        self.assertEqual(SWIFT.count('url: TempleEnvironment.appleVerifiedTransactionURL,'), 1)
        self.assertEqual(SWIFT.count('TempleSessionHTTP.authenticatedRequest('), 3) # Existing session request plus two new StoreKit calls
        self.assertIn('request.setValue("application/json", forHTTPHeaderField: "Content-Type")', SWIFT)
        self.assertIn('@app.get("/billing/apple/account-token")', MAIN)
        self.assertIn('@app.post("/billing/apple/verified-transaction")', MAIN)

    def test_baseline_legacy_web_and_voice_endpoints_preserved(self):
        for token in ('NativeVoiceSessionView(', 'oracleInferencePrepareURL',
                      'voiceTranscribeURL', 'NativeAnonymousIdentity.reconcile(',
                      'TempleWebView(', 'NativeOracleArtwork'):
            self.assertIn(token, SWIFT)

    def test_purchase_requires_backend_token_before_sheet(self):
        purchase = section('    private func purchaseSeekerMonthly() async {',
                           '    private func restoreApplePurchases() async {')
        self.assertLess(purchase.index('purchaseOptions()'), purchase.index('selected.purchase(options: options)'))
        self.assertIn('let purchase = try await selected.purchase(options: options)', purchase)
        self.assertNotIn('product.purchase()', purchase)
        self.assertNotIn('.finish()', purchase)

    def test_apple_account_token_is_backend_generated_and_canonical(self):
        token = section('    private func accountToken() async throws -> UUID {',
                        '    func purchaseOptions() async throws -> Set<Product.PurchaseOption> {')
        self.assertIn('TempleEnvironment.appleAccountTokenURL', token)
        self.assertIn('let token = UUID(uuidString: body.app_account_token)', token)
        self.assertIn('token.uuidString.lowercased() == body.app_account_token', token)
        self.assertNotIn('NativeAnonymousIdentity.currentID', token)
        self.assertNotIn('UUID()', token)
        self.assertNotIn('UserDefaults', token)

    def test_purchase_option_binds_canonical_token(self):
        options = section('    func purchaseOptions() async throws -> Set<Product.PurchaseOption> {',
                          '    func deliver(_ verification: VerificationResult<StoreKit.Transaction>) async throws -> String {')
        self.assertIn('let token = try await accountToken()', options)
        self.assertIn('return [.appAccountToken(token)]', options)

    def test_device_verification_required_before_server_delivery(self):
        delivery = section('    func deliver(_ verification: VerificationResult<StoreKit.Transaction>) async throws -> String {',
                           '    func restorePurchases() async throws -> Int {')
        self.assertLess(delivery.index('guard case .verified(let transaction)'),
                        delivery.index('let signedJWS = verification.jwsRepresentation'))
        self.assertNotIn('unsafePayloadValue', delivery)
        self.assertNotIn('transaction.jwsRepresentation', delivery)
        self.assertIn('verification.jwsRepresentation', delivery)

    def test_delivery_binds_transaction_owner_with_fresh_session(self):
        delivery = section('    func deliver(_ verification: VerificationResult<StoreKit.Transaction>) async throws -> String {',
                           '    func restorePurchases() async throws -> Int {')
        self.assertIn('let token = try await accountToken()', delivery)
        self.assertIn('guard transaction.appAccountToken == token else {', delivery)
        self.assertIn('throw NativeAppleDeliveryError.accountMismatch', delivery)
        self.assertLess(delivery.index('guard transaction.appAccountToken == token'),
                        delivery.index('request.httpBody'))

    def test_signed_payload_contains_no_client_account_override(self):
        body = section('private struct NativeAppleSignedTransactionBody: Encodable {',
                       'private struct NativeAppleSettlementReply: Decodable {')
        self.assertRegex(body, r'\A\s*let signed_transaction: String\s*\}\s*\Z')
        self.assertEqual(set(re.findall(r'\blet (\w+):', body)), {'signed_transaction'})
        self.assertIn('set(payload) != {"signed_transaction"}', MAIN)

    def test_client_does_not_finish_if_server_refuses_or_times_out(self):
        delivery = section('    func deliver(_ verification: VerificationResult<StoreKit.Transaction>) async throws -> String {',
                           '    func restorePurchases() async throws -> Int {')
        self.assertEqual(SWIFT.count('await transaction.finish()'), 1)
        self.assertIn('guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {', delivery)
        self.assertLess(delivery.index('result.durably_recorded'), delivery.index('await transaction.finish()'))
        self.assertLess(delivery.index('result.transaction_id == String(transaction.id)'), delivery.index('await transaction.finish()'))
        self.assertLess(delivery.index('result.refresh_entitlement_from == "/me"'), delivery.index('await transaction.finish()'))
        self.assertEqual(delivery.count('await transaction.finish()'), 1)

    def test_replay_and_old_receipts_acknowledged_without_access_claim(self):
        self.assertIn('"granted", "already_processed", "stale_ignored"', SWIFT)
        self.assertIn('case "already_processed":', SWIFT)
        self.assertIn('Apple recorded this older transaction without granting new access.', SWIFT)
        self.assertIn('result.status != "granted" || result.access_granted_this_delivery', SWIFT)

    def test_no_duplicate_inflight_native_deliveries(self):
        self.assertIn('private var delivering: Set<UInt64> = []', SWIFT)
        self.assertIn('guard !delivering.contains(transaction.id)', SWIFT)
        self.assertIn('defer { delivering.remove(transaction.id) }', SWIFT)
        self.assertIn('@MainActor\nprivate final class NativeAppleStoreKitBridge', SWIFT)

    def test_restore_button_is_explicit_and_preflights_before_apple_prompt(self):
        self.assertIn('Button("Restore Purchases") {', SWIFT)
        restore = section('    func restorePurchases() async throws -> Int {',
                          '    func retryUnfinished(onSettlement:')
        self.assertLess(restore.index('_ = try await accountToken()'), restore.index('try await AppStore.sync()'))
        self.assertEqual(SWIFT.count('AppStore.sync()'), 1)
        self.assertIn('try await NativeAppleStoreKitBridge.shared.restorePurchases()', SWIFT)

    def test_restore_checks_current_and_unfinished_entitlements(self):
        restore = section('    func restorePurchases() async throws -> Int {',
                          '    func retryUnfinished(onSettlement:')
        self.assertIn('Transaction.currentEntitlements', restore)
        self.assertIn('Transaction.unfinished', restore)
        self.assertIn('seen.insert(transaction.id).inserted', restore)
        self.assertEqual(restore.count('case .unverified(let transaction, _):'), 2)
        self.assertIn('throw NativeAppleDeliveryError.restorePending', restore)

    def test_launch_listeners_are_on_root_view_not_only_support(self):
        root = section('struct ContentView: View {', 'struct NativeVoiceSessionView: View {')
        self.assertIn('NativeAppleStoreKitBridge.shared.listenForUpdates', root)
        self.assertIn('NativeAppleStoreKitBridge.shared.retryUnfinished', root)
        self.assertIn('authRefreshNonce', root)
        self.assertIn('Transaction.updates', SWIFT)
        self.assertIn('Transaction.unfinished', SWIFT)

    def test_entitlement_refresh_reuses_existing_temple_nonce(self):
        self.assertIn('NativeSupportView(onEntitlementRecorded:', SWIFT)
        self.assertIn('if status != "stale_ignored" { onSettlement() }', SWIFT)
        self.assertGreaterEqual(SWIFT.count('templeEntryNonce += 1'), 2)
        self.assertIn('if count > 0 { onEntitlementRecorded() }', SWIFT)

    def test_native_and_server_keep_prices_provider_independent(self):
        for bad in ('199', '0.99', '1.99', 'stripe.com', 'gross_amount_cents', 'billing_transactions'):
            self.assertNotIn(bad, section('// A7C5H3B:', 'struct NativeInfoView: View {'))
        self.assertIn(r'Price: \($0.displayPrice) / month', SWIFT)

    def test_server_reuses_existing_verified_settlement_authority(self):
        self.assertIn('verify_signed_transaction(signed, verifier=verifier', DELIVERY)
        self.assertIn('settle_verified_apple_transaction(', DELIVERY)
        self.assertIn("'durably_recorded': True", DELIVERY)
        self.assertIn('GODINC_APPLE_DELIVERY_MODE', DELIVERY)
        self.assertIn('GODINC_APPLE_DELIVERY_MODE', MAIN)

    def test_no_web_legacy_storekit_purchase_path_left_behind(self):
        native = section('// A7C5H3B:', 'struct NativeInfoView: View {')
        self.assertNotIn('await transaction.finish()\n                    await MainActor.run', native)
        self.assertNotIn('product.purchase()', native)
        self.assertEqual(native.count('selected.purchase(options: options)'), 1)
        self.assertNotIn('signed_transaction: "', native)


    def test_explicit_storekit_transaction_type_resolves_swiftui_collision(self):
        self.assertEqual(SWIFT.count('VerificationResult<StoreKit.Transaction>'), 1)
        self.assertNotIn('VerificationResult<Transaction>', SWIFT)
        self.assertEqual(SWIFT.count('for await verification in StoreKit.Transaction.'), 4)
        self.assertNotIn('for await verification in Transaction.', SWIFT)


if __name__ == '__main__':
    unittest.main(verbosity=2)
