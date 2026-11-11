"""Verified Apple StoreKit transaction facts for the existing billing authority.

This is a provider-specific trust boundary, NOT a subscription ledger or
independent entitlement authority. No database, provider, or application imports.

Only the actual SignedDataVerifier may be injected in deployed application code.
A fake verifier is permissible solely in isolated synthetic tests.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
import re
from uuid import UUID


PRODUCT_ID = "ai.godincorporated.seeker.monthly"
BUNDLE_ID = "ai.godincorporated.temple"
SUPPORTED_ENVIRONMENTS = {"Sandbox", "Production"}
_JWS = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\Z")
_ID = re.compile(r"[0-9]{1,32}\Z")


class AppleContractError(ValueError):
    """Untrusted, incomplete, inconsistent, or unsupported Apple evidence."""


def _uuid(value) -> str:
    if value is None:
        raise AppleContractError("Apple account token is missing")
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise AppleContractError("Invalid Apple account token") from exc


def _environment(value) -> str:
    result = getattr(value, "value", value)
    if result not in SUPPORTED_ENVIRONMENTS:
        raise AppleContractError("Unsupported Apple transaction environment")
    return result


def _timestamp(ms, label: str) -> datetime:
    if type(ms) is not int or ms <= 0:
        raise AppleContractError("Missing or invalid " + label)
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise AppleContractError("Invalid " + label) from exc


def _jws(value: str) -> str:
    if not isinstance(value, str) or len(value) > 100000 or not _JWS.fullmatch(value):
        raise AppleContractError("Invalid compact JWS structure")
    return value


@dataclass(frozen=True)
class AppleTransactionFacts:
    environment: str
    app_account_token: str
    original_transaction_id: str
    transaction_id: str
    product_id: str
    purchase_date: datetime
    expires_date: datetime
    signed_date: datetime
    revoked_at: Optional[datetime]
    upgraded: bool

    def active_at(self, now: datetime) -> bool:
        if now.tzinfo is None:
            raise AppleContractError("Active check requires timezone-aware time")
        return (
            self.revoked_at is None
            and not self.upgraded
            and self.purchase_date <= now
            and self.expires_date > now
        )


def validate_verified_transaction(decoded, *, expected_account_id: Optional[str] = None) -> AppleTransactionFacts:
    """Validate an ALREADY cryptographically verified Apple library object.

    Calling this method on arbitrary data is unsafe; deployed callers MUST
    first invoke SignedDataVerifier.verify_and_decode_signed_transaction().
    """
    if getattr(decoded, "bundleId", None) != BUNDLE_ID:
        raise AppleContractError("Apple bundle mismatch")
    environment = _environment(getattr(decoded, "environment", None))
    if getattr(decoded, "productId", None) != PRODUCT_ID:
        raise AppleContractError("Apple product mismatch")

    account_token = _uuid(getattr(decoded, "appAccountToken", None))
    if expected_account_id is not None and account_token != _uuid(expected_account_id):
        raise AppleContractError("Apple transaction belongs to another account")

    original = getattr(decoded, "originalTransactionId", None)
    transaction = getattr(decoded, "transactionId", None)
    if not isinstance(original, str) or not _ID.fullmatch(original):
        raise AppleContractError("Invalid Apple original transaction ID")
    if not isinstance(transaction, str) or not _ID.fullmatch(transaction):
        raise AppleContractError("Invalid Apple transaction ID")

    purchase = _timestamp(getattr(decoded, "purchaseDate", None), "purchaseDate")
    expiry = _timestamp(getattr(decoded, "expiresDate", None), "expiresDate")
    signed = _timestamp(getattr(decoded, "signedDate", None), "signedDate")
    if expiry <= purchase or signed < purchase:
        raise AppleContractError("Inconsistent Apple transaction time bounds")

    revoked_ms = getattr(decoded, "revocationDate", None)
    revoked_at = _timestamp(revoked_ms, "revocationDate") if revoked_ms is not None else None
    upgraded = getattr(decoded, "isUpgraded", None)
    if upgraded is not None and type(upgraded) is not bool:
        raise AppleContractError("Invalid upgrade flag")

    return AppleTransactionFacts(
        environment=environment,
        app_account_token=account_token,
        original_transaction_id=original,
        transaction_id=transaction,
        product_id=PRODUCT_ID,
        purchase_date=purchase,
        expires_date=expiry,
        signed_date=signed,
        revoked_at=revoked_at,
        upgraded=bool(upgraded),
    )


def verify_signed_transaction(signed_transaction: str, *, verifier, expected_account_id: Optional[str] = None) -> AppleTransactionFacts:
    """Trust boundary: cryptographic verification MUST precede all decoding."""
    signed_transaction = _jws(signed_transaction)
    try:
        verified = verifier.verify_and_decode_signed_transaction(signed_transaction)
    except Exception as exc:
        raise AppleContractError("Apple signed transaction verification failed") from exc
    return validate_verified_transaction(verified, expected_account_id=expected_account_id)


@dataclass(frozen=True)
class AppleNotificationFacts:
    notification_id: str
    notification_type: str
    subtype: Optional[str]
    signed_date: datetime
    transaction: Optional[AppleTransactionFacts]
    action: str
    grace_expires_at: Optional[datetime] = None
    auto_renew_enabled: Optional[bool] = None


_GRANT_EVENTS = {"SUBSCRIBED", "DID_RENEW", "RENEWAL_EXTENDED", "REFUND_REVERSED"}
_END_EVENTS = {"EXPIRED", "REFUND", "REVOKE", "GRACE_PERIOD_EXPIRED", "DID_FAIL_TO_RENEW"}
_METADATA_EVENTS = {"DID_CHANGE_RENEWAL_STATUS"}


def verify_signed_notification(signed_payload: str, *, verifier) -> AppleNotificationFacts:
    """Verify outer ASN V2 JWS AND independently verify nested transaction JWS."""
    signed_payload = _jws(signed_payload)
    try:
        outer = verifier.verify_and_decode_notification(signed_payload)
    except Exception as exc:
        raise AppleContractError("Apple notification signature verification failed") from exc

    ident = getattr(outer, "notificationUUID", None)
    _uuid(ident)
    name = getattr(outer, "rawNotificationType", None)
    if not isinstance(name, str):
        name = getattr(getattr(outer, "notificationType", None), "value", None)
    if not isinstance(name, str) or not name:
        raise AppleContractError("Notification type missing")
    subtype = getattr(outer, "rawSubtype", None)
    if subtype is None:
        subtype = getattr(getattr(outer, "subtype", None), "value", None)
    if subtype is not None and not isinstance(subtype, str):
        raise AppleContractError("Invalid Apple notification subtype")
    timestamp = _timestamp(getattr(outer, "signedDate", None), "notification signedDate")

    if name in _GRANT_EVENTS:
        action = "grant"
    elif name == "DID_FAIL_TO_RENEW" and subtype == "GRACE_PERIOD":
        action = "grace"
    elif name in _END_EVENTS:
        action = "terminate"
    elif name in _METADATA_EVENTS:
        action = "metadata"
    else:
        action = "ignore"

    data = getattr(outer, "data", None)
    nested = getattr(data, "signedTransactionInfo", None) if data is not None else None
    facts = verify_signed_transaction(nested, verifier=verifier) if nested is not None else None
    if action != "ignore" and facts is None:
        raise AppleContractError("Financial notification lacks a verified transaction")

    grace_end = None
    auto_renew = None
    if action in {"grace", "metadata"}:
        signed_renewal = getattr(data, "signedRenewalInfo", None)
        if signed_renewal is None:
            raise AppleContractError("Renewal event lacks signed renewal information")
        try:
            renewal = verifier.verify_and_decode_renewal_info(_jws(signed_renewal))
        except Exception as exc:
            raise AppleContractError("Apple signed renewal verification failed") from exc
        if _environment(getattr(renewal, "environment", None)) != facts.environment:
            raise AppleContractError("Renewal environment differs from transaction")
        if str(getattr(renewal, "originalTransactionId", None)) != facts.original_transaction_id:
            raise AppleContractError("Renewal belongs to a different original transaction")

        if action == "grace":
            grace_end = _timestamp(
                getattr(renewal, "gracePeriodExpiresDate", None),
                "gracePeriodExpiresDate",
            )
            if grace_end <= facts.expires_date:
                raise AppleContractError("Grace period does not extend past paid expiry")
        else:
            if subtype not in {"AUTO_RENEW_ENABLED", "AUTO_RENEW_DISABLED"}:
                raise AppleContractError("Unknown renewal-status subtype")
            flag = getattr(renewal, "autoRenewStatus", None)
            flag = getattr(flag, "value", flag)
            if type(flag) is not int or flag not in (0, 1):
                raise AppleContractError("Missing verified auto-renew status")
            auto_renew = bool(flag)
            if auto_renew != (subtype == "AUTO_RENEW_ENABLED"):
                raise AppleContractError("Renewal status conflicts with subtype")

    return AppleNotificationFacts(
        notification_id=str(UUID(str(ident))),
        notification_type=name,
        subtype=subtype,
        signed_date=timestamp,
        transaction=facts,
        action=action,
        grace_expires_at=grace_end,
        auto_renew_enabled=auto_renew,
    )


def build_signed_data_verifier(*, environment: str, apple_app_id: Optional[int], roots: tuple[bytes, ...]):
    """Create Apple's real verifier; never decode payloads without its signature checks.

    Network certificate checks are enabled for deployed integrations.
    The app-store-server-library package and trusted Apple PKI roots must be
    installed/configured in each target environment before enabling processing.
    """
    if environment not in SUPPORTED_ENVIRONMENTS or not roots:
        raise AppleContractError("Apple verification environment or trusted roots missing")
    if environment == "Production" and (type(apple_app_id) is not int or apple_app_id <= 0):
        raise AppleContractError("Apple production app ID is required")
    try:
        from appstoreserverlibrary.models.Environment import Environment
        from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier
    except ImportError as exc:
        raise AppleContractError("Apple server verification library is not installed") from exc
    selected = Environment.PRODUCTION if environment == "Production" else Environment.SANDBOX
    return SignedDataVerifier(
        list(roots), True, selected, BUNDLE_ID,
        apple_app_id if environment == "Production" else None,
    )
