"""Zero-provider synthetic contract tests, no database or application imports."""
import importlib.util
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace as O
from datetime import datetime, timezone, timedelta
from uuid import UUID

SOURCE = Path(__file__).resolve().parents[1] / 'services' / 'apple_storekit_contract.py'
spec = importlib.util.spec_from_file_location('apple_contract_under_test', SOURCE)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)

ACCT = '1a991fee-bbdc-4896-81a3-96636473911a'
OTHER = 'faba593d-9997-426c-aaad-f6fe02cac6c4'
JWS = 'eyJhbGciOiJFUzI1NiJ9.eyJ0ZXN0IjoxfQ.signature'
NOW = datetime(2026, 10, 21, 23, 0, tzinfo=timezone.utc)
def ms(dt): return int(dt.timestamp() * 1000)

def valid_transaction(**kwargs):
    fields = dict(bundleId=m.BUNDLE_ID, environment='Sandbox', productId=m.PRODUCT_ID,
                  appAccountToken=UUID(ACCT), originalTransactionId='20000000111111', transactionId='20000000111112',
                  purchaseDate=ms(NOW - timedelta(days=1)), expiresDate=ms(NOW + timedelta(days=29)),
                  signedDate=ms(NOW), revocationDate=None, isUpgraded=False)
    fields.update(kwargs)
    return O(**fields)


class Verifier:
    def __init__(self, obj=None, notification=None, raises=False, renewal=None):
        self.obj = obj or valid_transaction()
        self.notification = notification
        self.raises = raises
        self.renewal = renewal or O(environment='Sandbox', originalTransactionId='20000000111111',
                                    gracePeriodExpiresDate=ms(NOW + timedelta(days=35)), autoRenewStatus=0)
        self.calls = []
    def verify_and_decode_signed_transaction(self, signed):
        self.calls.append('transaction')
        if self.raises: raise ValueError('not valid')
        return self.obj
    def verify_and_decode_notification(self, signed):
        self.calls.append('notification')
        if self.raises: raise ValueError('not valid')
        return self.notification
    def verify_and_decode_renewal_info(self, signed):
        self.calls.append('renewal')
        if self.raises: raise ValueError('not valid')
        return self.renewal


class Contract(unittest.TestCase):
    def test_good_transaction(self):
        v = Verifier()
        fact = m.verify_signed_transaction(JWS, verifier=v, expected_account_id=ACCT)
        self.assertEqual(v.calls, ['transaction'])
        self.assertTrue(fact.active_at(NOW))
        self.assertEqual(fact.original_transaction_id, '20000000111111')
        self.assertFalse(fact.active_at(NOW + timedelta(days=30)))

    def test_wrong_account(self):
        with self.assertRaises(m.AppleContractError):
            m.verify_signed_transaction(JWS, verifier=Verifier(), expected_account_id=OTHER)

    def test_invalid_signature_cannot_grant(self):
        with self.assertRaises(m.AppleContractError):
            m.verify_signed_transaction(JWS, verifier=Verifier(raises=True))

    def test_missing_account_token(self):
        with self.assertRaises(m.AppleContractError):
            m.validate_verified_transaction(valid_transaction(appAccountToken=None))

    def test_wrong_bundle_product_environment(self):
        for item in (dict(bundleId='ai.other.app'), dict(productId='ai.other.product'), dict(environment='Xcode')):
            with self.subTest(item=item), self.assertRaises(m.AppleContractError):
                m.validate_verified_transaction(valid_transaction(**item))

    def test_missing_and_malformed_dates(self):
        for item in (dict(expiresDate=None), dict(purchaseDate=True),
                     dict(expiresDate=ms(NOW - timedelta(days=2))), dict(signedDate=-1)):
            with self.subTest(item=item), self.assertRaises(m.AppleContractError):
                m.validate_verified_transaction(valid_transaction(**item))

    def test_expired_revoked_upgraded_not_active(self):
        for item in (dict(expiresDate=ms(NOW - timedelta(hours=1))),
                     dict(revocationDate=ms(NOW - timedelta(hours=1))), dict(isUpgraded=True)):
            with self.subTest(item=item):
                fact = m.validate_verified_transaction(valid_transaction(**item))
                self.assertFalse(fact.active_at(NOW))

    def test_invalid_jws_rejected_before_verifier(self):
        verifier = Verifier()
        for bad in ('', 'a.b', 'a.b.c.d', 'not signed', 'a.b.=', 123, 'a' * 100001):
            with self.subTest(bad=str(bad)[:12]), self.assertRaises(m.AppleContractError):
                m.verify_signed_transaction(bad, verifier=verifier)
        self.assertEqual(verifier.calls, [])

    def test_verified_notification_and_nested_jws(self):
        outer = O(notificationUUID='0cbac7be-d128-4230-99c5-3e00a0dd2228',
                  rawNotificationType='DID_RENEW', rawSubtype=None, signedDate=ms(NOW),
                  data=O(signedTransactionInfo=JWS))
        v = Verifier(notification=outer)
        result = m.verify_signed_notification(JWS, verifier=v)
        self.assertEqual(v.calls, ['notification', 'transaction'])
        self.assertEqual(result.action, 'grant')
        self.assertIsNotNone(result.transaction)

    def test_missing_nested_signed_transaction_refuses_grant(self):
        outer = O(notificationUUID='0cbac7be-d128-4230-99c5-3e00a0dd2228',
                  rawNotificationType='DID_RENEW', rawSubtype=None, signedDate=ms(NOW), data=None)
        with self.assertRaises(m.AppleContractError):
            m.verify_signed_notification(JWS, verifier=Verifier(notification=outer))

    def test_failed_notification_signature(self):
        with self.assertRaises(m.AppleContractError):
            m.verify_signed_notification(JWS, verifier=Verifier(raises=True))

    def test_notification_kind_policy(self):
        for name, expected in [('SUBSCRIBED', 'grant'), ('DID_RENEW','grant'), ('EXPIRED','terminate'),
                               ('DID_FAIL_TO_RENEW','terminate'), ('PRICE_INCREASE','ignore')]:
            with self.subTest(event=name):
                outer = O(notificationUUID='0cbac7be-d128-4230-99c5-3e00a0dd2228', rawNotificationType=name,
                          rawSubtype=None, signedDate=ms(NOW), data=O(signedTransactionInfo=JWS))
                self.assertEqual(m.verify_signed_notification(JWS, verifier=Verifier(notification=outer)).action, expected)

    def test_grace_requires_verified_renewal(self):
        outer = O(notificationUUID='0cbac7be-d128-4230-99c5-3e00a0dd2228',
                  rawNotificationType='DID_FAIL_TO_RENEW', rawSubtype='GRACE_PERIOD',
                  signedDate=ms(NOW), data=O(signedTransactionInfo=JWS, signedRenewalInfo=JWS))
        verifier = Verifier(notification=outer)
        result = m.verify_signed_notification(JWS, verifier=verifier)
        self.assertEqual(result.action, 'grace')
        self.assertEqual(result.grace_expires_at, NOW + timedelta(days=35))
        self.assertEqual(verifier.calls, ['notification', 'transaction', 'renewal'])

    def test_grace_missing_signed_renewal_refuses(self):
        outer = O(notificationUUID='0cbac7be-d128-4230-99c5-3e00a0dd2228',
                  rawNotificationType='DID_FAIL_TO_RENEW', rawSubtype='GRACE_PERIOD',
                  signedDate=ms(NOW), data=O(signedTransactionInfo=JWS))
        with self.assertRaises(m.AppleContractError):
            m.verify_signed_notification(JWS, verifier=Verifier(notification=outer))

    def test_grace_wrong_original_or_date_refuses(self):
        outer = O(notificationUUID='0cbac7be-d128-4230-99c5-3e00a0dd2228',
                  rawNotificationType='DID_FAIL_TO_RENEW', rawSubtype='GRACE_PERIOD',
                  signedDate=ms(NOW), data=O(signedTransactionInfo=JWS, signedRenewalInfo=JWS))
        for renewal in (
            O(environment='Sandbox', originalTransactionId='9999', gracePeriodExpiresDate=ms(NOW + timedelta(days=35))),
            O(environment='Production', originalTransactionId='20000000111111', gracePeriodExpiresDate=ms(NOW + timedelta(days=35))),
            O(environment='Sandbox', originalTransactionId='20000000111111', gracePeriodExpiresDate=ms(NOW + timedelta(days=20))),
        ):
            with self.subTest(renewal=renewal), self.assertRaises(m.AppleContractError):
                m.verify_signed_notification(JWS, verifier=Verifier(notification=outer, renewal=renewal))

    def test_cancel_off_and_on_require_verified_renewal(self):
        for subtype, flag in [('AUTO_RENEW_DISABLED', 0), ('AUTO_RENEW_ENABLED', 1)]:
            outer = O(notificationUUID='0cbac7be-d128-4230-99c5-3e00a0dd2228',
                      rawNotificationType='DID_CHANGE_RENEWAL_STATUS', rawSubtype=subtype,
                      signedDate=ms(NOW), data=O(signedTransactionInfo=JWS, signedRenewalInfo=JWS))
            renewal = O(environment='Sandbox', originalTransactionId='20000000111111', autoRenewStatus=flag)
            fact = m.verify_signed_notification(JWS, verifier=Verifier(notification=outer, renewal=renewal))
            self.assertEqual(fact.action, 'metadata')
            self.assertEqual(fact.auto_renew_enabled, bool(flag))

    def test_unverified_or_contradictory_auto_renew_refuses(self):
        for subtype, flag in [('AUTO_RENEW_DISABLED', 1), ('AUTO_RENEW_ENABLED', 0), ('UNKNOWN', 1)]:
            outer = O(notificationUUID='0cbac7be-d128-4230-99c5-3e00a0dd2228',
                      rawNotificationType='DID_CHANGE_RENEWAL_STATUS', rawSubtype=subtype,
                      signedDate=ms(NOW), data=O(signedTransactionInfo=JWS, signedRenewalInfo=JWS))
            renewal = O(environment='Sandbox', originalTransactionId='20000000111111', autoRenewStatus=flag)
            with self.subTest(subtype=subtype, flag=flag), self.assertRaises(m.AppleContractError):
                m.verify_signed_notification(JWS, verifier=Verifier(notification=outer, renewal=renewal))

    def test_production_verifier_requires_app_id_and_roots(self):
        for kwargs in (dict(environment='Production', apple_app_id=None, roots=(b'root',)),
                       dict(environment='Sandbox', apple_app_id=None, roots=()),
                       dict(environment='Xcode', apple_app_id=None, roots=(b'root',))):
            with self.subTest(kwargs=kwargs), self.assertRaises(m.AppleContractError):
                m.build_signed_data_verifier(**kwargs)


if __name__ == '__main__':
    unittest.main(verbosity=2)
