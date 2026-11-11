"""Dormant App Store Server Notification V2 signed grant and lifecycle ingress.

Verified grant events use H1; lifecycle events use the same existing authority
with atomic notifications and account fallback. Unknown types defer and retry.

Reuse the canonical Apple JWS verifier, H1 atomic entitlement writer and the
existing payment_events ledger. Never log or persist raw signed JWS. A grant's
entitlement commit may precede its notification receipt; retry is safe because
H1 purchase receipts are transaction-ID-idempotent. No new financial ledger.
"""
from datetime import datetime, timezone, timedelta
import json
from uuid import UUID

from services.apple_storekit_contract import (
    AppleNotificationFacts, AppleContractError, build_signed_data_verifier,
    verify_signed_notification,
)
from services.apple_storekit_delivery import AppleDeliveryConfiguration
from services.apple_storekit_settlement import (
    AppleSettlementResult, settle_verified_apple_transaction,
)


class AppleNotificationError(ValueError):
    """Malformed or unverifiable notification boundary data."""


class AppleNotificationDeferred(AppleNotificationError):
    """A verified event cannot yet be durably processed; request Apple retry."""


_HANDLER = 'a7c5h3d_apple_verified_grant_notification.v1'
_SCHEMA = 'apple_server_notification_v2_receipt.v1'


def _check_facts(notification, environment, now):
    if type(notification) is not AppleNotificationFacts:
        raise AppleNotificationError('Signed AppleNotificationFacts required')
    try:
        canonical = str(UUID(notification.notification_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise AppleNotificationError('Valid signed notification UUID required') from exc
    if canonical != notification.notification_id:
        raise AppleNotificationError('Canonical signed notification UUID required')
    if (not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None
            or not isinstance(notification.signed_date, datetime)
            or notification.signed_date.tzinfo is None
            or notification.signed_date > now + timedelta(minutes=5)):
        raise AppleNotificationDeferred('Signed notification timestamp requires review')
    if notification.action != 'grant':
        raise AppleNotificationDeferred('Grant-only validator requires grant')
    facts = notification.transaction
    if facts is None or facts.environment != environment:
        raise AppleNotificationError('Nested transaction environment mismatch')
    if notification.notification_type not in (
            'SUBSCRIBED', 'DID_RENEW', 'RENEWAL_EXTENDED', 'REFUND_REVERSED'):
        raise AppleNotificationError('Unexpected grant notification classification')
    return facts


def _store_grant_notification(*, notification, settlement, user_id, environment,
                              expected_database, connection_factory):
    """Append idempotent notification receipt *after* purchase ledger is durable."""
    event_id = 'notification:' + environment + ':' + notification.notification_id
    object_id = notification.transaction.transaction_id
    payload = {
        'schema': _SCHEMA, 'environment': environment,
        'notification_id': notification.notification_id,
        'notification_type': notification.notification_type,
        'subtype': notification.subtype,
        'transaction_id': object_id,
        'settlement_outcome': settlement.outcome,
        'signed_date': notification.signed_date.isoformat(),
    }
    conn = connection_factory()
    try:
        if getattr(conn, 'autocommit', False):
            raise AppleNotificationDeferred('Autocommit notification connection refused')
        with conn.cursor() as cur:
            cur.execute('SELECT current_database() AS db_name')
            row = cur.fetchone() or {}
            if row.get('db_name') != expected_database:
                raise AppleNotificationDeferred('Notification database identity mismatch')
            cur.execute("SET LOCAL lock_timeout = '3s'")
            cur.execute("SET LOCAL statement_timeout = '12s'")
            cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))',
                        ('godinc_apple_notification_v1', event_id))
            cur.execute("""SELECT id, user_id, subscription_id, object_id,
                                  processing_status, event_type, livemode
                           FROM payment_events
                           WHERE provider='apple' AND provider_event_id=%s
                           FOR UPDATE""", (event_id,))
            old = cur.fetchone()
            if old is not None:
                if (old['event_type'] != 'APPLE_NOTIFICATION_VERIFIED_GRANT'
                        or old['processing_status'] != 'processed'
                        or old['object_id'] != object_id
                        or str(old['user_id']) != user_id
                        or str(old['subscription_id']) != settlement.subscription_id
                        or old['livemode'] is not (environment == 'Production')):
                    raise AppleNotificationDeferred('Conflicting signed notification receipt')
                conn.rollback()
                return 'already_recorded'
            cur.execute("""INSERT INTO payment_events
                           (provider, provider_event_id, event_type, event_created_at,
                            object_type, object_id, user_id, subscription_id,
                            livemode, payload_json, processing_status,
                            processed_at, handler_version)
                           VALUES ('apple', %s, 'APPLE_NOTIFICATION_VERIFIED_GRANT', %s,
                                   'notification', %s, %s::uuid, %s::uuid,
                                   %s, %s::jsonb, 'processed', NOW(), %s)
                           RETURNING id""",
                        (event_id, notification.signed_date, object_id, user_id,
                         settlement.subscription_id, environment == 'Production',
                         json.dumps(payload), _HANDLER))
            if cur.fetchone() is None:
                raise AppleNotificationDeferred('Notification receipt did not persist')
        conn.commit()
        return 'recorded'
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()



_TEST_EVENT_TYPE = 'APPLE_NOTIFICATION_VERIFIED_TEST'
_TEST_SCHEMA = 'apple_server_notification_v2_test_receipt.v1'
_TEST_HANDLER = 'a7c5h3j_verified_test_notification.v1'


def _store_verified_test_notification(*, notification, environment,
                                      expected_database, connection_factory, now):
    """Durably acknowledge ONLY an Apple-signed TEST, with no entitlement change.

    The caller has already verified Apple's outer JWS. No client-supplied type,
    unverified JSON, or unknown notification is eligible for this path.
    """
    if (type(notification) is not AppleNotificationFacts
            or notification.notification_type != 'TEST'
            or notification.action != 'ignore'
            or notification.transaction is not None
            or notification.subtype is not None
            or notification.grace_expires_at is not None
            or notification.auto_renew_enabled is not None):
        raise AppleNotificationDeferred('Not a verified transaction-free Apple TEST')
    try:
        identity = str(UUID(notification.notification_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise AppleNotificationDeferred('Apple TEST notification ID is invalid') from exc
    if identity != notification.notification_id:
        raise AppleNotificationDeferred('Apple TEST notification ID is noncanonical')
    if (environment not in ('Sandbox', 'Production')
            or not isinstance(now, datetime) or now.tzinfo is None
            or now.utcoffset() is None
            or not isinstance(notification.signed_date, datetime)
            or notification.signed_date.tzinfo is None
            or notification.signed_date.utcoffset() is None
            or notification.signed_date > now + timedelta(minutes=5)):
        raise AppleNotificationDeferred('Apple TEST signature time or environment invalid')

    event_id = 'notification:' + environment + ':' + identity
    metadata = {
        'schema': _TEST_SCHEMA,
        'configured_environment': environment,
        'notification_type': 'TEST',
        'notification_id': identity,
        'signed_date': notification.signed_date.isoformat(),
    }
    conn = connection_factory()
    try:
        if getattr(conn, 'autocommit', False):
            raise AppleNotificationDeferred('Autocommit TEST receipt connection refused')
        with conn.cursor() as cur:
            cur.execute('SELECT current_database() AS db_name')
            row = cur.fetchone() or {}
            if row.get('db_name') != expected_database:
                raise AppleNotificationDeferred('Apple TEST receipt database mismatch')
            cur.execute("SET LOCAL lock_timeout = '3s'")
            cur.execute("SET LOCAL statement_timeout = '12s'")
            cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))',
                        ('godinc_apple_notification_v1', event_id))
            cur.execute("""SELECT event_type, processing_status, object_type, object_id,
                                  user_id, subscription_id, transaction_id, livemode,
                                  payload_json, event_created_at
                           FROM payment_events
                           WHERE provider='apple' AND provider_event_id=%s
                           FOR UPDATE""", (event_id,))
            old = cur.fetchone()
            if old is not None:
                if (old['event_type'] != _TEST_EVENT_TYPE
                        or old['processing_status'] != 'processed'
                        or old['object_type'] != 'notification_test'
                        or old['object_id'] != identity
                        or old['user_id'] is not None
                        or old['subscription_id'] is not None
                        or old['transaction_id'] is not None
                        or old['livemode'] is not (environment == 'Production')
                        or old['payload_json'] != metadata
                        or old['event_created_at'] != notification.signed_date):
                    raise AppleNotificationDeferred('Conflicting durable Apple TEST receipt')
                conn.rollback()
                return 'already_recorded'
            cur.execute("""INSERT INTO payment_events
                           (provider, provider_event_id, event_type,
                            event_created_at, object_type, object_id,
                            livemode, payload_json, processing_status,
                            processed_at, handler_version)
                           VALUES ('apple', %s, %s, %s, 'notification_test', %s,
                                   %s, %s::jsonb, 'processed', NOW(), %s)
                           RETURNING id""",
                        (event_id, _TEST_EVENT_TYPE, notification.signed_date,
                         identity, environment == 'Production',
                         json.dumps(metadata, sort_keys=True), _TEST_HANDLER))
            if cur.fetchone() is None:
                raise AppleNotificationDeferred('Apple TEST receipt was not persisted')
        conn.commit()
        return 'recorded'
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def process_verified_apple_notification(*, payload, config, connection_factory,
                                        compute_floors, now=None):
    """Verify outer + nested JWS, settle active grant, append durable event.

    Supported verified lifecycle events have atomic state and notification
    receipts. Unsupported events defer. Feature remains OFF by default.
    """
    if type(config) is not AppleDeliveryConfiguration:
        raise AppleNotificationError('Configured Apple runtime trust required')
    if not isinstance(payload, dict) or set(payload) != {'signedPayload'}:
        raise AppleNotificationError('Exactly one signedPayload required')
    signed = payload['signedPayload']
    if not isinstance(signed, str) or not 10 <= len(signed) <= 100000:
        raise AppleNotificationError('Invalid signedPayload size')
    if not callable(connection_factory) or not callable(compute_floors):
        raise AppleNotificationError('Canonical account and billing authority required')
    clock = now if now is not None else datetime.now(timezone.utc)
    try:
        verifier = build_signed_data_verifier(environment=config.environment,
                                            apple_app_id=config.apple_app_id,
                                            roots=config.roots)
    except AppleContractError as exc:
        raise AppleNotificationDeferred('Apple cryptographic verifier unavailable') from exc
    notification = verify_signed_notification(signed, verifier=verifier)
    if notification.notification_type == 'TEST':
        receipt = _store_verified_test_notification(
            notification=notification, environment=config.environment,
            expected_database=config.expected_database,
            connection_factory=connection_factory, now=clock,
        )
        return {'ok': True, 'notification_type': 'TEST',
                'notification_receipt': receipt, 'entitlement_changed': False}
    if notification.action != 'grant':
        from services.apple_storekit_lifecycle import (
            AppleLifecycleError, apply_verified_apple_lifecycle,
        )
        try:
            outcome = apply_verified_apple_lifecycle(
                notification=notification, environment=config.environment,
                connection_factory=connection_factory, compute_floors=compute_floors,
                expected_database=config.expected_database, now=clock,
            )
        except AppleLifecycleError as exc:
            raise AppleNotificationDeferred('Verified lifecycle event requires retry or review') from exc
        return {'ok': True, 'lifecycle_outcome': outcome.outcome,
                'notification_receipt': 'already_recorded' if outcome.outcome == 'already_processed' else 'recorded'}
    facts = _check_facts(notification, config.environment, clock)
    settlement = settle_verified_apple_transaction(
        facts=facts, expected_user_id=facts.app_account_token,
        environment=config.environment,
        connection_factory=connection_factory, compute_floors=compute_floors,
        expected_database=config.expected_database, now=clock,
    )
    if type(settlement) is not AppleSettlementResult or settlement.outcome not in (
            'granted', 'already_processed', 'stale_ignored') or settlement.subscription_id is None:
        raise AppleNotificationDeferred('Verified Apple settlement not durably accepted')
    receipt = _store_grant_notification(
        notification=notification, settlement=settlement,
        user_id=facts.app_account_token, environment=config.environment,
        expected_database=config.expected_database,
        connection_factory=connection_factory,
    )
    return {
        'ok': True, 'notification_receipt': receipt,
        'entitlement_outcome': settlement.outcome,
    }
