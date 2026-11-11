"""Apple ASN V2 non-grant lifecycle transitions on the existing billing authority.

The caller MUST have verified both outer and nested Apple signed JWS and signed
renewal evidence where applicable. This function cannot authenticate raw JWS.
No new financial ledger or revenue amounts. All state/event writes are in one
transaction under H1 chain -> user -> subscription lock ordering. Unsupported
or contradictory events fail closed, never acknowledged as successful.
"""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from uuid import UUID
import json

from services.apple_storekit_contract import AppleNotificationFacts, AppleTransactionFacts, PRODUCT_ID
from services.apple_storekit_settlement import _floors, AppleSettlementError


class AppleLifecycleError(ValueError):
    """Notification cannot be safely applied; caller should request retry."""


@dataclass(frozen=True)
class AppleLifecycleResult:
    outcome: str
    subscription_id: str
    notification_id: str
    changed_entitlement: bool


_HANDLER = 'a7c5h3e_apple_lifecycle.v1'
_SCHEMA = 'apple_verified_lifecycle_receipt.v1'
_ALLOWED_EXPIRED_SUBTYPES = frozenset({None, 'VOLUNTARY', 'BILLING_RETRY',
                                       'PRICE_INCREASE', 'PRODUCT_NOT_FOR_SALE'})
_TERMINATORS = frozenset({'EXPIRED','REFUND','REVOKE','GRACE_PERIOD_EXPIRED','DID_FAIL_TO_RENEW'})


def _canonical(value, label):
    if not isinstance(value, str):
        raise AppleLifecycleError(label + ' missing')
    try:
        result = str(UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise AppleLifecycleError(label + ' invalid') from exc
    if result != value:
        raise AppleLifecycleError(label + ' not canonical')
    return result


def _check(notification, environment, now):
    if type(notification) is not AppleNotificationFacts or type(notification.transaction) is not AppleTransactionFacts:
        raise AppleLifecycleError('Signed, independently verified Apple notification facts required')
    facts = notification.transaction
    _canonical(notification.notification_id, 'Signed notification UUID')
    _canonical(facts.app_account_token, 'Signed account UUID')
    if environment not in ('Sandbox','Production') or facts.environment != environment or facts.product_id != PRODUCT_ID:
        raise AppleLifecycleError('Apple environment or product mismatch')
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise AppleLifecycleError('Timezone-aware clock required')
    if (not isinstance(notification.signed_date, datetime) or notification.signed_date.tzinfo is None
            or notification.signed_date > now + timedelta(minutes=5)):
        raise AppleLifecycleError('Notification timestamp invalid or in the future')
    if not (facts.purchase_date <= facts.signed_date <= now + timedelta(minutes=5)
            and facts.purchase_date < facts.expires_date):
        raise AppleLifecycleError('Contradictory signed transaction time bounds')
    if notification.action == 'metadata':
        if notification.notification_type != 'DID_CHANGE_RENEWAL_STATUS' or type(notification.auto_renew_enabled) is not bool:
            raise AppleLifecycleError('Verified renewal-status evidence missing')
        if notification.subtype != ('AUTO_RENEW_ENABLED' if notification.auto_renew_enabled else 'AUTO_RENEW_DISABLED'):
            raise AppleLifecycleError('Renewal subtype conflicts with signed state')
    elif notification.action == 'grace':
        # A verified Apple grace event cannot grant an unpaid paid-through period.
        # Our StoreKit billing-grace policy is OFF: defer before any DB connection.
        raise AppleLifecycleError('Unpaid Apple billing grace is not enabled')
    elif notification.action == 'terminate':
        typ = notification.notification_type
        if typ not in _TERMINATORS or (typ == 'EXPIRED' and notification.subtype not in _ALLOWED_EXPIRED_SUBTYPES):
            raise AppleLifecycleError('Unknown termination event subtype')
        if typ == 'DID_FAIL_TO_RENEW' and notification.subtype is not None:
            raise AppleLifecycleError('Unexpected billing retry subtype')
        if typ in ('REFUND','REVOKE') and (facts.revoked_at is None or facts.revoked_at > now + timedelta(minutes=5)):
            raise AppleLifecycleError('Refund/revocation lacks signed revocationDate')
    else:
        raise AppleLifecycleError('Unsupported notification action must not be ACKed')
    return facts


def apply_verified_apple_lifecycle(*, notification, environment, connection_factory,
                                  compute_floors, expected_database, now=None):
    """Apply one non-grant signed lifecycle event or a recorded stale outcome.

    No source/user/transaction data is copied to another ledger. Financial refund
    amounts remain unreconciled; no numeric credits or invented revenue are posted.
    """
    clock = now if now is not None else datetime.now(timezone.utc)
    facts = _check(notification, environment, clock)
    if not callable(connection_factory) or not callable(compute_floors):
        raise AppleLifecycleError('Existing billing connection and floor policy required')
    if not isinstance(expected_database, str) or not expected_database:
        raise AppleLifecycleError('Explicit database identity required')
    event_id = 'notification:' + environment + ':' + notification.notification_id
    is_live = environment == 'Production'
    user_id = facts.app_account_token
    original_id = facts.original_transaction_id
    conn = connection_factory()
    try:
        if getattr(conn,'autocommit',False):
            raise AppleLifecycleError('Autocommit financial connection refused')
        with conn.cursor() as cur:
            cur.execute('SELECT current_database() AS db_name')
            db_row = cur.fetchone() or {}
            if db_row.get('db_name') != expected_database:
                raise AppleLifecycleError('Database identity mismatch')
            cur.execute("SET LOCAL lock_timeout = '3s'")
            cur.execute("SET LOCAL statement_timeout = '12s'")
            cur.execute("""SELECT i.indexrelid::regclass::text AS name FROM pg_catalog.pg_index i
                           WHERE i.indexrelid IN (to_regclass('public.idx_subscriptions_apple_original_unique_v1'),
                             to_regclass('public.idx_billing_transactions_apple_tx_unique_v1'))
                             AND i.indisunique AND i.indisvalid AND i.indisready""")
            if len(cur.fetchall() or []) != 2:
                raise AppleLifecycleError('Apple ledger uniqueness migration missing')
            cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))',
                        ('godinc_apple_original_v1', environment + ':' + original_id))
            cur.execute("""SELECT id, email_verified, is_banned, highest_paid_plan_ever, scroll_count,
                                  plan_code, entitlement_status, subscription_expires_at
                           FROM users WHERE id=%s::uuid FOR UPDATE""", (user_id,))
            user = cur.fetchone()
            if not user or not user['email_verified'] or user['is_banned']:
                raise AppleLifecycleError('Signed Apple account missing or not permitted')
            cur.execute("""SELECT id, user_id, provider, provider_subscription_id, plan_code,
                                  support_mode, provider_price_id, livemode, provider_status,
                                  internal_status, current_period_start, current_period_end,
                                  grace_period_ends_at, subscription_metadata_json, auto_renews
                           FROM subscriptions WHERE user_id=%s::uuid
                              OR (provider='apple' AND provider_subscription_id=%s AND livemode=%s)
                           ORDER BY id FOR UPDATE""", (user_id, original_id, is_live))
            rows = cur.fetchall() or []
            chain = [row for row in rows if row['provider']=='apple'
                     and row['provider_subscription_id']==original_id and row['livemode'] is is_live]
            if len(chain) != 1 or str(chain[0]['user_id']) != user_id:
                raise AppleLifecycleError('No single previously settled Apple chain owned by account')
            sub = chain[0]
            if (sub['plan_code'] != 'seeker' or sub['support_mode'] != 'monthly_recurring'
                    or sub['provider_price_id'] != PRODUCT_ID):
                raise AppleLifecycleError('Existing Apple plan or product contract differs')
            sub_id = str(sub['id'])
            other_paid = any(row['provider'] != 'apple' and row['internal_status'] in ('active','grace')
                             and row['provider_status'] in ('active','trialing','grace')
                             and (row['current_period_end'] is None or row['current_period_end'] > clock)
                             for row in rows)
            if other_paid and user['entitlement_status'] == 'active':
                # Never let an old Apple refund modify a newly activated Stripe account.
                raise AppleLifecycleError('Competing active paid provider requires manual reconciliation')
            cur.execute("""SELECT id, user_id, subscription_id, object_id, event_type,
                                  processing_status, livemode, payload_json
                           FROM payment_events WHERE provider='apple' AND provider_event_id=%s
                           FOR UPDATE""", (event_id,))
            old = cur.fetchone()
            if old is not None:
                payload = old['payload_json']
                if (old['event_type'] != 'APPLE_NOTIFICATION_LIFECYCLE'
                        or old['processing_status'] not in ('processed','ignored')
                        or old['object_id'] != facts.transaction_id
                        or str(old['user_id']) != user_id
                        or str(old['subscription_id']) != sub_id
                        or old['livemode'] is not is_live
                        or not isinstance(payload,dict) or payload.get('schema') != _SCHEMA
                        or payload.get('notification_type') != notification.notification_type
                        or payload.get('subtype') != notification.subtype
                        or payload.get('action') != notification.action):
                    raise AppleLifecycleError('Conflicting durable lifecycle notification replay')
                conn.rollback()
                return AppleLifecycleResult('already_processed', sub_id, notification.notification_id, False)
            cur.execute("""SELECT event_created_at, event_type
                           FROM payment_events
                           WHERE provider='apple' AND subscription_id=%s::uuid
                             AND event_type IN ('APPLE_NOTIFICATION_LIFECYCLE',
                                   'APPLE_NOTIFICATION_VERIFIED_GRANT', 'APPLE_VERIFIED_PURCHASE')
                             AND processing_status='processed'
                           ORDER BY event_created_at DESC, id DESC LIMIT 1""", (sub_id,))
            latest_event = cur.fetchone()
            metadata = sub['subscription_metadata_json']
            if not isinstance(metadata,dict):
                raise AppleLifecycleError('Existing Apple subscription provenance missing')
            last_tx = metadata.get('transaction_id')
            if not isinstance(last_tx,str):
                raise AppleLifecycleError('Existing Apple current transaction not recorded')
            late = (latest_event is not None and latest_event['event_created_at'] >= notification.signed_date)
            is_current_transaction = (last_tx == facts.transaction_id)
            outcome = None
            changed = False
            if late or not is_current_transaction:
                outcome = 'stale_ignored'
            elif notification.action == 'metadata':
                flag = notification.auto_renew_enabled
                canceled = not flag
                cur.execute("""UPDATE subscriptions SET auto_renews=%s, cancel_at_period_end=%s,
                               canceled_at=CASE WHEN %s THEN %s ELSE NULL END,
                               subscription_metadata_json=subscription_metadata_json || %s::jsonb,
                               updated_at=NOW() WHERE id=%s::uuid AND provider='apple'""",
                            (flag, canceled, canceled, notification.signed_date,
                             json.dumps({'lifecycle': {'action':'metadata','signed_date':notification.signed_date.isoformat()}}), sub_id))
                if cur.rowcount != 1:
                    raise AppleLifecycleError('Renewal status update did not persist')
                if not other_paid:
                    cur.execute('UPDATE users SET cancel_at_period_end=%s, updated_at=NOW() WHERE id=%s::uuid',
                                (canceled,user_id))
                    if cur.rowcount != 1:
                        raise AppleLifecycleError('Account renewal status update did not persist')
                changed = True
                outcome = 'auto_renew_enabled' if flag else 'auto_renew_disabled'
            elif notification.action == 'terminate':
                typ = notification.notification_type
                effective = facts.revoked_at if typ in ('REFUND','REVOKE') else facts.expires_date
                if typ == 'GRACE_PERIOD_EXPIRED':
                    if sub['grace_period_ends_at'] is None:
                        raise AppleLifecycleError('Cannot expire grace without prior verified grace state')
                    # Historical grace records never extend the verified paid end.
                    effective = facts.expires_date
                if sub['current_period_end'] is not None and sub['current_period_end'] > facts.expires_date:
                    outcome = 'stale_ignored'
                elif effective > clock:
                    # Paid period remains valid until its verified expiration.
                    outcome = 'future_end_recorded'
                else:
                    status = 'revoked' if typ in ('REFUND','REVOKE') else 'expired'
                    cur.execute("""UPDATE subscriptions SET internal_status='expired',
                                   provider_status=%s, ended_at=%s, grace_period_ends_at=NULL,
                                   subscription_metadata_json=subscription_metadata_json || %s::jsonb,
                                   updated_at=NOW() WHERE id=%s::uuid AND provider='apple'""",
                                (status, effective,
                                 json.dumps({'lifecycle':{'action':status,'signed_date':notification.signed_date.isoformat()}}),sub_id))
                    if cur.rowcount != 1:
                        raise AppleLifecycleError('Subscription termination did not persist')
                    if not other_paid:
                        highest = user['highest_paid_plan_ever']
                        floors = _floors(compute_floors, highest, user['scroll_count'])
                        cur.execute("""UPDATE users SET entitlement_status='expired',
                                       subscription_expires_at=%s, subscription_renews_at=NULL,
                                       grace_period_ends_at=NULL, last_support_ended_at=%s,
                                       donor_floor_plan_code=%s, scroll_floor_plan_code=%s,
                                       fallback_floor_plan_code=%s, updated_at=NOW()
                                       WHERE id=%s::uuid""",
                                    (effective, effective, floors['donor'], floors['scroll'],floors['fallback'],user_id))
                        if cur.rowcount != 1:
                            raise AppleLifecycleError('Account termination did not persist')
                    changed = True
                    outcome = status
                if typ == 'DID_FAIL_TO_RENEW':
                    cur.execute("""UPDATE subscriptions SET last_failed_payment_at=%s, updated_at=NOW()
                                   WHERE id=%s::uuid AND provider='apple'""",
                                (notification.signed_date,sub_id))
                    if cur.rowcount != 1:
                        raise AppleLifecycleError('Billing retry timestamp did not persist')
            else:
                raise AppleLifecycleError('Unexpected lifecycle policy state')
            summary = dict(schema=_SCHEMA, environment=environment, action=notification.action,
                           notification_type=notification.notification_type, subtype=notification.subtype,
                           original_transaction_id=original_id, transaction_id=facts.transaction_id,
                           signed_date=notification.signed_date.isoformat(), outcome=outcome)
            cur.execute("""INSERT INTO payment_events
                           (provider, provider_event_id, event_type, event_created_at,
                            object_type, object_id, user_id, subscription_id, livemode,
                            payload_json, processing_status, processed_at, handler_version)
                           VALUES ('apple', %s, 'APPLE_NOTIFICATION_LIFECYCLE', %s,
                                   'notification', %s, %s::uuid, %s::uuid, %s,
                                   %s::jsonb, %s, NOW(), %s) RETURNING id""",
                        (event_id, notification.signed_date, facts.transaction_id, user_id, sub_id,
                         is_live, json.dumps(summary), 'ignored' if outcome=='stale_ignored' else 'processed',_HANDLER))
            if not cur.fetchone():
                raise AppleLifecycleError('Durable lifecycle notification receipt missing')
        conn.commit()
        return AppleLifecycleResult(outcome, sub_id, notification.notification_id, changed)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
