"""Dormant authenticated Apple StoreKit transaction-delivery boundary.

Reuses the installed, cryptographically verified Apple contract and the existing
God Incorporated subscription writer. This module NEVER decodes unverified JWS,
creates a billing ledger, or claims App Store proceeds. Its production entrypoint
cannot accept an injected fake verifier. All settings default fail-closed.

Enablement is intentionally separate from source installation and requires
verified Apple DER trust roots, an exact database/environment pair, Apple SDK,
and explicit operational authorization. Do not enable before native acceptance.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import os
import re
from typing import Mapping
from uuid import UUID

from services.apple_storekit_contract import (
    AppleContractError,
    PRODUCT_ID,
    build_signed_data_verifier,
    verify_signed_transaction,
)
from services.apple_storekit_settlement import (
    AppleSettlementError,
    AppleSettlementResult,
    settle_verified_apple_transaction,
)


class AppleDeliveryError(ValueError):
    """Untrusted or disabled native transaction delivery."""


class AppleDeliveryUnavailable(AppleDeliveryError):
    """Trusted verification service is unconfigured or unavailable."""


@dataclass(frozen=True)
class AppleDeliveryConfiguration:
    expected_database: str
    environment: str
    apple_app_id: int | None
    roots: tuple[bytes, ...]


_ALLOWED_DATABASE_ENVIRONMENTS = {
    'godinc_dev': 'Sandbox',
    'godinc_clean_staging_db': 'Sandbox',
    'godinc_prod': 'Production',
}
_FILENAME_RE = re.compile(r'\.(cer|der)\Z', re.I)
_APPLE_ROOT_SHA256 = frozenset({
    'c2b9b042dd57830e7d117dac55ac8ae19407d38e41d88f3215bc3a890444a050',
    '63343abfb89a6a03ebb57e9b3f5fa7be7c4f5c756f3017b3a8c488c3653e9179',
})
_UUID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')


def _account_id(value) -> str:
    if not isinstance(value, str) or not _UUID_RE.fullmatch(value):
        raise AppleDeliveryError('Canonical authenticated account UUID required')
    try:
        if str(UUID(value)) != value:
            raise ValueError('Noncanonical UUID')
    except (ValueError, TypeError, AttributeError) as exc:
        raise AppleDeliveryError('Canonical authenticated account UUID required') from exc
    return value


def delivery_enabled(env: Mapping | None = None) -> bool:
    settings = os.environ if env is None else env
    return settings.get('GODINC_APPLE_DELIVERY_MODE') == 'verified_settlement'


def load_delivery_configuration(env: Mapping | None = None) -> AppleDeliveryConfiguration:
    """Derive signed-data trust configuration from explicit deployment settings.

    Apple root certificates are public trust material; their paths are nevertheless
    required to be configured explicitly. No auto-download or implicit trust roots.
    """
    settings = os.environ if env is None else env
    if not delivery_enabled(settings):
        raise AppleDeliveryUnavailable('Apple delivery is disabled')
    database = settings.get('GODINC_APPLE_EXPECTED_DATABASE')
    environment = settings.get('GODINC_APPLE_ENVIRONMENT')
    if database not in _ALLOWED_DATABASE_ENVIRONMENTS:
        raise AppleDeliveryUnavailable('Unknown application database identity')
    if _ALLOWED_DATABASE_ENVIRONMENTS[database] != environment:
        raise AppleDeliveryUnavailable('Apple environment does not match database identity')
    raw_id = settings.get('GODINC_APPLE_APP_ID', '')
    if environment == 'Production':
        if not isinstance(raw_id, str) or not re.fullmatch(r'[1-9][0-9]{0,17}', raw_id):
            raise AppleDeliveryUnavailable('Apple production app identifier is missing')
        app_id = int(raw_id)
    else:
        if raw_id not in ('', None):
            raise AppleDeliveryUnavailable('Sandbox must not depend on a production app ID')
        app_id = None
    configured = settings.get('GODINC_APPLE_ROOT_CERT_PATHS')
    if not isinstance(configured, str) or not configured or len(configured) > 4096:
        raise AppleDeliveryUnavailable('Explicit Apple root certificate paths are missing')
    paths = configured.split(os.pathsep)
    if not 1 <= len(paths) <= 5 or len(set(paths)) != len(paths):
        raise AppleDeliveryUnavailable('Invalid Apple trusted-root file list')
    roots = []
    seen_certificates = set()
    for name in paths:
        path = Path(name)
        if (not path.is_absolute() or path.is_symlink()
                or not path.is_file() or not _FILENAME_RE.search(path.name)):
            raise AppleDeliveryUnavailable('Invalid Apple trusted-root certificate path')
        if not 128 <= path.stat().st_size <= 20000:
            raise AppleDeliveryUnavailable('Unexpected Apple trusted-root certificate size')
        content = path.read_bytes()
        if not content or content[0] != 0x30:
            raise AppleDeliveryUnavailable('Expected binary DER Apple trust certificate')
        root_fingerprint = hashlib.sha256(content).hexdigest()
        if root_fingerprint not in _APPLE_ROOT_SHA256:
            raise AppleDeliveryUnavailable('Unrecognized Apple trusted-root fingerprint')
        if root_fingerprint in seen_certificates:
            raise AppleDeliveryUnavailable('Repeated Apple trusted-root certificate')
        seen_certificates.add(root_fingerprint)
        roots.append(content)
    return AppleDeliveryConfiguration(database, environment, app_id, tuple(roots))


def authenticated_account_token(*, user_id: str, config: AppleDeliveryConfiguration) -> dict:
    """Share account-bound StoreKit appAccountToken with an authenticated native UI."""
    if type(config) is not AppleDeliveryConfiguration:
        raise AppleDeliveryError('Verified Apple runtime configuration required')
    return {'app_account_token': _account_id(user_id),
            'product_id': PRODUCT_ID, 'environment': config.environment}


def deliver_apple_signed_transaction(*, payload, user_id: str,
                                     config: AppleDeliveryConfiguration,
                                     connection_factory, compute_floors) -> dict:
    """Verify Apple JWS FIRST; only then invoke the existing atomic writer.

    A replay/stale receipt is durable evidence of processing but DOES NOT imply
    newly granted access. Clients must query /me for current entitlement.
    """
    if type(config) is not AppleDeliveryConfiguration:
        raise AppleDeliveryError('Verified Apple runtime configuration required')
    account = _account_id(user_id)
    if not isinstance(payload, dict) or set(payload) != {'signed_transaction'}:
        raise AppleDeliveryError('Exactly one signed transaction required')
    signed = payload['signed_transaction']
    if not isinstance(signed, str) or not 10 <= len(signed) <= 100000:
        raise AppleDeliveryError('Invalid signed transaction data length')
    if not callable(connection_factory) or not callable(compute_floors):
        raise AppleDeliveryError('Existing billing and floor authority required')
    # The verifier factory here is Apple's ACTUAL implementation. It has no
    # externally injectable substitute in this production entrypoint.
    try:
        verifier = build_signed_data_verifier(environment=config.environment,
                                              apple_app_id=config.apple_app_id,
                                              roots=config.roots)
    except AppleContractError as exc:
        raise AppleDeliveryUnavailable('Trusted Apple verifier unavailable') from exc
    facts = verify_signed_transaction(signed, verifier=verifier,
                                      expected_account_id=account)
    result = settle_verified_apple_transaction(
        facts=facts, expected_user_id=account,
        environment=config.environment,
        connection_factory=connection_factory,
        compute_floors=compute_floors,
        expected_database=config.expected_database,
    )
    if type(result) is not AppleSettlementResult or result.outcome not in (
            'granted', 'already_processed', 'stale_ignored'):
        raise AppleDeliveryError('Unrecognized durable Apple settlement result')
    return {
        'status': result.outcome,
        'durably_recorded': True,
        'access_granted_this_delivery': result.access_granted,
        'transaction_id': result.transaction_id,
        'subscription_id': result.subscription_id,
        'refresh_entitlement_from': '/me',
    }
