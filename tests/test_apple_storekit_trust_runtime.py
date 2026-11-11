"""Actual Apple SDK + source-packaged, pinned Apple trust anchors.

Run with the already accepted isolated SDK venv for prerelease acceptance.
No app/server import, database connection, runtime activation or network required.
"""
import hashlib
import importlib.metadata
import os
from pathlib import Path
import sys
import unittest
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.apple_storekit_contract import (
    AppleContractError, build_signed_data_verifier, verify_signed_transaction,
)
from services.apple_storekit_delivery import (
    AppleDeliveryError, AppleDeliveryUnavailable,
    deliver_apple_signed_transaction, delivery_enabled, load_delivery_configuration,
)

FILES = (
    ('AppleRootCA-G2.cer', 'c2b9b042dd57830e7d117dac55ac8ae19407d38e41d88f3215bc3a890444a050'),
    ('AppleRootCA-G3.cer', '63343abfb89a6a03ebb57e9b3f5fa7be7c4f5c756f3017b3a8c488c3653e9179'),
)


def root_files():
    return [ROOT / 'services' / 'apple_storekit_trust' / name for name, _ in FILES]


def test_config():
    return {
        'GODINC_APPLE_DELIVERY_MODE': 'verified_settlement',
        'GODINC_APPLE_EXPECTED_DATABASE': 'godinc_dev',
        'GODINC_APPLE_ENVIRONMENT': 'Sandbox',
        'GODINC_APPLE_APP_ID': '',
        'GODINC_APPLE_ROOT_CERT_PATHS': os.pathsep.join(map(str, root_files())),
    }


class RealAppleTrustTests(unittest.TestCase):
    def test_exact_app_requirement_pin(self):
        lines = (ROOT / 'requirements.txt').read_text(encoding='utf-8').splitlines()
        found = [x.strip() for x in lines if x.strip().lower().startswith('app-store-server-library')]
        self.assertEqual(found, ['app-store-server-library==3.1.2'])

    def test_source_packaged_roots_match_accepted_apple_fingerprints(self):
        for path, (_, expected) in zip(root_files(), FILES):
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file() and not path.is_symlink())
                self.assertTrue(128 <= path.stat().st_size <= 20000)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected)

    def test_current_sdk_matches_exact_project_pin(self):
        self.assertEqual(importlib.metadata.version('app-store-server-library'), '3.1.2')

    def test_modes_remain_disabled_without_settings(self):
        self.assertFalse(delivery_enabled({}))
        with self.assertRaises(AppleDeliveryUnavailable):
            load_delivery_configuration({})

    def test_real_sdk_verifier_constructs_from_packaged_roots(self):
        from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier
        from appstoreserverlibrary.models.Environment import Environment
        config = load_delivery_configuration(test_config())
        self.assertEqual(config.expected_database, 'godinc_dev')
        self.assertEqual(config.environment, 'Sandbox')
        self.assertIsNone(config.apple_app_id)
        self.assertEqual(config.roots, tuple(path.read_bytes() for path in root_files()))
        verifier = build_signed_data_verifier(
            environment=config.environment,
            apple_app_id=config.apple_app_id,
            roots=config.roots,
        )
        self.assertIs(type(verifier), SignedDataVerifier)
        self.assertEqual(verifier._bundle_id, 'ai.godincorporated.temple')
        self.assertEqual(verifier._environment, Environment.SANDBOX)
        self.assertTrue(verifier._enable_online_checks)

    def test_real_sdk_refuses_non_apple_jws_before_db_access(self):
        import jwt
        forged = jwt.encode({
            'bundleId': 'ai.godincorporated.temple',
            'environment': 'Sandbox',
            'productId': 'ai.godincorporated.seeker.monthly',
        }, 'not-an-apple-key', algorithm='HS256')
        config = load_delivery_configuration(test_config())
        called = []

        def forbidden_db():
            called.append(True)
            raise AssertionError('A forged signature reached database code')

        with self.assertRaises(AppleContractError):
            deliver_apple_signed_transaction(
                payload={'signed_transaction': forged},
                user_id=str(UUID('12345678-1234-4234-8234-123456789012')),
                config=config,
                connection_factory=forbidden_db,
                compute_floors=lambda *a: (_ for _ in ()).throw(AssertionError('Floor invocation')),
            )
        self.assertEqual(called, [])

    def test_non_apple_certificate_refused_even_if_der_shaped(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            injected = Path(temp) / 'AppleRootCA.cer'
            injected.write_bytes(b'\x30' + b'F' * 250)
            env = test_config()
            env['GODINC_APPLE_ROOT_CERT_PATHS'] = str(injected)
            with self.assertRaises(AppleDeliveryUnavailable):
                load_delivery_configuration(env)


if __name__ == '__main__':
    unittest.main(verbosity=2)
