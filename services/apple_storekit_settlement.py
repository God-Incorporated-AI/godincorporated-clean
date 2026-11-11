"""Apple StoreKit V2 -> existing God Incorporated ledger, dormant transaction writer.

This is NOT a signature verifier or an API route. Only a trusted caller that has
invoked services.apple_storekit_contract.verify_signed_transaction() may supply
AppleTransactionFacts. A real caller must pass the actual configured environment
and the established Local/Stage/Production connection factory.

Requires UNIQUE partial indexes from the separate A7C5H1 migration *before use*.
Apple's JWS price is not an accounting source of truth. Each verified purchase
receives a non-revenue pending monetary record for subsequent reconciliation,
while entitlement is committed atomically with its durable Apple event receipt.
"""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import json
from typing import Callable
from uuid import UUID

from services.apple_storekit_contract import AppleTransactionFacts, PRODUCT_ID


class AppleSettlementError(ValueError):
    """A verified transaction cannot be safely settled with current authority."""


@dataclass(frozen=True)
class AppleSettlementResult:
    outcome: str  # 'granted', 'already_processed', 'stale_ignored'
    transaction_id: str
    subscription_id: str | None
    access_granted: bool


_ALLOWED_PAID = frozenset({'seeker', 'magister', 'sovereign', 'philosophus', 'theoricus'})
_ALLOWED_FLOORS = frozenset({'anon', 'pilgrim', 'seeker', 'magister', 'sovereign', 'philosophus', 'theoricus'})
_SCHEMA = 'apple_verified_entitlement_receipt.v1'
_HANDLER = 'a7c5h1_dormant_apple_settlement.v1'


def _user_id(value: str) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError('Expected account UUID string')
        canonical = str(UUID(value))
        if canonical != value:
            raise ValueError('Account UUID must be canonical')
        return canonical
    except (ValueError, TypeError, AttributeError) as exc:
        raise AppleSettlementError('Invalid authenticated account identifier') from exc


def _checked_facts(facts, expected_user_id, environment, now):
    if type(facts) is not AppleTransactionFacts:
        raise AppleSettlementError('Verified AppleTransactionFacts are required')
    user_id = _user_id(expected_user_id)
    if facts.app_account_token != user_id:
        raise AppleSettlementError('Verified transaction account token mismatch')
    if environment not in ('Sandbox', 'Production') or facts.environment != environment:
        raise AppleSettlementError('Apple verified environment mismatch')
    if facts.product_id != PRODUCT_ID:
        raise AppleSettlementError('Unsupported Apple subscription product')
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise AppleSettlementError('Timezone-aware clock required')
    if facts.signed_date > now + timedelta(minutes=5):
        raise AppleSettlementError('Apple evidence timestamp is in the future')
    if facts.purchase_date > facts.signed_date or facts.expires_date <= facts.purchase_date:
        raise AppleSettlementError('Contradictory verified Apple time bounds')
    if not facts.active_at(now):
        raise AppleSettlementError('No active, nonrevoked Apple entitlement to grant')
    return user_id


def _floors(compute_floors, highest, scroll_count):
    """Reuse existing main.py floor computation via an explicitly injected callback."""
    if not callable(compute_floors):
        raise AppleSettlementError('Existing fallback policy callback is required')
    values = compute_floors(highest, scroll_count)
    if not isinstance(values, dict) or set(values) != {'donor', 'scroll', 'fallback'}:
        raise AppleSettlementError('Existing fallback policy produced invalid shape')
    if not (values['donor'] is None or values['donor'] in _ALLOWED_FLOORS):
        raise AppleSettlementError('Invalid donor floor')
    if not (values['scroll'] is None or values['scroll'] in _ALLOWED_FLOORS):
        raise AppleSettlementError('Invalid scroll floor')
    if values['fallback'] not in _ALLOWED_FLOORS:
        raise AppleSettlementError('Invalid fallback floor')
    return values


def settle_verified_apple_transaction(*, facts: AppleTransactionFacts,
                                      expected_user_id: str, environment: str,
                                      connection_factory: Callable, compute_floors: Callable,
                                      expected_database: str,
                                      now: datetime | None = None) -> AppleSettlementResult:
    """Apply one Apple renewal/purchase atomically; never create provider calls.

    No database connection is opened for invalid facts. Advisory locks, exact
    unique-index prerequisites, the user lock and existing-rail checks precede
    every insert. A replay cannot grant a second allowance. Foreign active paid
    rails block new grants, including Apple IDs belonging to another account.
    The caller must verify the cryptographic signature BEFORE invoking this API.
    """
    clock = now if now is not None else datetime.now(timezone.utc)
    user_id = _checked_facts(facts, expected_user_id, environment, clock)
    if not callable(connection_factory):
        raise AppleSettlementError('Database authority is required')
    if expected_database not in ('godinc_dev', 'godinc_clean_staging_db', 'godinc_prod'):
        raise AppleSettlementError('Explicit known database identity is required')
    is_live = environment == 'Production'
    event_id = environment + ':' + facts.transaction_id
    receipt_id = event_id
    original_id = facts.original_transaction_id
    conn = connection_factory()
    try:
        if getattr(conn, 'autocommit', False):
            raise AppleSettlementError('Autocommit connection cannot settle subscriptions')
        with conn.cursor() as cur:
            cur.execute("SELECT current_database() AS db_name")
            actual = cur.fetchone() or {}
            if actual.get('db_name') != expected_database:
                raise AppleSettlementError('Connection points to the wrong deployment database')
            cur.execute("SET LOCAL lock_timeout = '3s'")
            cur.execute("SET LOCAL statement_timeout = '12s'")
            cur.execute("""SELECT
                EXISTS (SELECT 1 FROM pg_catalog.pg_index i
                        WHERE i.indexrelid=to_regclass('public.idx_subscriptions_apple_original_unique_v1')
                          AND i.indisunique AND i.indisvalid AND i.indisready) AS chain_unique,
                EXISTS (SELECT 1 FROM pg_catalog.pg_index i
                        WHERE i.indexrelid=to_regclass('public.idx_billing_transactions_apple_tx_unique_v1')
                          AND i.indisunique AND i.indisvalid AND i.indisready) AS receipt_unique""")
            guards = cur.fetchone() or {}
            if guards.get('chain_unique') is not True or guards.get('receipt_unique') is not True:
                raise AppleSettlementError('Apple ledger uniqueness migration not accepted in this environment')
            # The chain lock precedes the user row lock in all Apple entry paths.
            cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))',
                        ('godinc_apple_original_v1', environment + ':' + original_id))
            cur.execute("""SELECT id, email_verified, is_banned, highest_paid_plan_ever,
                                  scroll_count FROM users WHERE id = %s::uuid FOR UPDATE""",
                        (user_id,))
            user = cur.fetchone()
            if not user or not user['email_verified'] or user['is_banned']:
                raise AppleSettlementError('Verified, permitted account required')

            cur.execute("""SELECT provider_product_id, provider_price_id
                           FROM provider_product_map WHERE provider='apple'
                             AND plan_code='seeker' AND support_mode='monthly_recurring'
                             AND livemode=%s AND active=TRUE FOR SHARE""", (is_live,))
            product_rows = cur.fetchall() or []
            if (len(product_rows) != 1
                    or product_rows[0]['provider_product_id'] != PRODUCT_ID):
                raise AppleSettlementError('Configured Apple product mapping missing or conflicting')

            # Also select any OTHER account claiming this chain; never rebind it.
            cur.execute("""SELECT id, user_id, provider, provider_subscription_id,
                                  provider_status, internal_status, current_period_start,
                                  current_period_end, livemode, cancel_at_period_end, auto_renews,
                                  plan_code, support_mode, provider_price_id,
                                  started_at, grace_period_ends_at FROM subscriptions
                           WHERE user_id = %s::uuid
                              OR (provider = 'apple' AND provider_subscription_id = %s AND livemode = %s)
                           ORDER BY id FOR UPDATE""", (user_id, original_id, is_live))
            rows = cur.fetchall() or []
            chain = [row for row in rows if row['provider'] == 'apple'
                     and row['provider_subscription_id'] == original_id
                     and row['livemode'] is is_live]
            if len(chain) > 1:
                raise AppleSettlementError('Duplicate existing Apple subscription chain')
            if chain and str(chain[0]['user_id']) != user_id:
                raise AppleSettlementError('Apple subscription chain owned by another account')
            owned_chain = chain[0] if chain else None
            if owned_chain and (
                    owned_chain['plan_code'] != 'seeker'
                    or owned_chain['support_mode'] != 'monthly_recurring'
                    or owned_chain['provider_price_id'] != PRODUCT_ID):
                raise AppleSettlementError('Existing Apple chain product or plan mismatch')

            cur.execute("""SELECT id, user_id, object_id, processing_status, subscription_id
                           FROM payment_events WHERE provider='apple' AND provider_event_id=%s
                           FOR UPDATE""", (event_id,))
            old_event = cur.fetchone()
            if old_event:
                if (str(old_event['user_id']) != user_id or old_event['object_id'] != facts.transaction_id
                        or old_event['processing_status'] not in ('processed', 'ignored')
                        or (old_event['processing_status'] == 'processed' and not owned_chain)):
                    raise AppleSettlementError('Conflicting or incomplete Apple event history')
                if old_event['processing_status'] == 'processed' and str(old_event['subscription_id']) != str(owned_chain['id']):
                    raise AppleSettlementError('Apple event refers to another subscription')
                conn.rollback()  # read-only replay, no accidental ledger writes
                return AppleSettlementResult('already_processed', facts.transaction_id,
                                             str(owned_chain['id']) if owned_chain else None, False)

            # Reject a second active provider rail. This also rejects a new Apple
            # chain when an older chain for this user remains active.
            for row in rows:
                if (row['internal_status'] in ('active', 'grace')
                        and row['provider_status'] in ('active', 'trialing', 'grace')
                        and (row['current_period_end'] is None or row['current_period_end'] > clock
                             or (row['internal_status'] == 'grace'
                                 and row.get('grace_period_ends_at') is not None
                                 and row['grace_period_ends_at'] > clock))
                        and row is not owned_chain):
                    raise AppleSettlementError('Another paid subscription rail is active')

            summary = {
                'schema': _SCHEMA, 'environment': environment,
                'original_transaction_id': original_id,
                'transaction_id': facts.transaction_id,
                'product_id': PRODUCT_ID,
                'purchase_date': facts.purchase_date.isoformat(),
                'expires_date': facts.expires_date.isoformat(),
                'signed_date': facts.signed_date.isoformat(),
            }
            if owned_chain and (owned_chain['current_period_start'] is not None
                                and owned_chain['current_period_start'] > facts.purchase_date
                                or owned_chain['current_period_end'] is not None
                                and owned_chain['current_period_end'] >= facts.expires_date):
                cur.execute("""INSERT INTO payment_events
                           (provider, provider_event_id, event_type, event_created_at,
                            object_type, object_id, user_id, livemode, payload_json,
                            processing_status, processed_at, handler_version)
                           VALUES ('apple', %s, 'APPLE_VERIFIED_STALE', %s,
                                   'transaction', %s, %s::uuid, %s, %s::jsonb,
                                   'ignored', NOW(), %s)
                           RETURNING id""",
                            (event_id, facts.signed_date, facts.transaction_id,
                             user_id, is_live, json.dumps(summary), _HANDLER))
                if not cur.fetchone():
                    raise AppleSettlementError('Unable to record ignored Apple replay')
                conn.commit()
                return AppleSettlementResult('stale_ignored', facts.transaction_id,
                                             str(owned_chain['id']), False)

            highest = user['highest_paid_plan_ever']
            if highest is not None and highest not in _ALLOWED_PAID:
                raise AppleSettlementError('Unexpected historical paid plan')
            highest = highest or 'seeker'
            floors = _floors(compute_floors, highest, user['scroll_count'])

            cur.execute("""SELECT id, provider_customer_id, livemode FROM billing_customers
                           WHERE user_id=%s::uuid AND provider='apple' FOR UPDATE""", (user_id,))
            customer = cur.fetchone()
            apple_customer = 'app-account-token:' + user_id
            if customer and (customer['provider_customer_id'] != apple_customer
                             or customer['livemode'] is not is_live):
                raise AppleSettlementError('Existing Apple billing customer identity conflict')
            if not customer:
                cur.execute("""INSERT INTO billing_customers
                           (user_id, provider, provider_customer_id, livemode, customer_metadata_json)
                           VALUES (%s::uuid, 'apple', %s, %s, %s::jsonb) RETURNING id""",
                            (user_id, apple_customer, is_live, json.dumps({'schema': _SCHEMA})))
                customer = cur.fetchone()
                if not customer:
                    raise AppleSettlementError('Apple billing customer insert failed')
            customer_id = customer['id']

            if owned_chain:
                cur.execute("""UPDATE subscriptions SET
                             provider_status='active', internal_status='active',
                             current_period_start=%s, current_period_end=%s,
                             last_payment_at=%s, ended_at=NULL, grace_period_ends_at=NULL, updated_at=NOW(),
                             subscription_metadata_json=%s::jsonb
                           WHERE id=%s::uuid AND provider='apple' AND user_id=%s::uuid""",
                            (facts.purchase_date, facts.expires_date, facts.purchase_date,
                             json.dumps(summary), owned_chain['id'], user_id))
                if cur.rowcount != 1:
                    raise AppleSettlementError('Apple subscription update failed')
                subscription_id = owned_chain['id']
            else:
                cur.execute("""INSERT INTO subscriptions
                             (user_id, billing_customer_id, plan_code, provider,
                              provider_subscription_id, provider_price_id, support_mode,
                              provider_status, internal_status, started_at,
                              current_period_start, current_period_end, cancel_at_period_end,
                              auto_renews, last_payment_at, livemode, subscription_metadata_json)
                           VALUES (%s::uuid, %s::uuid, 'seeker', 'apple', %s, %s,
                                   'monthly_recurring', 'active', 'active', %s, %s, %s,
                                   FALSE, TRUE, %s, %s, %s::jsonb) RETURNING id""",
                            (user_id, customer_id, original_id, PRODUCT_ID,
                             facts.purchase_date, facts.purchase_date, facts.expires_date,
                             facts.purchase_date, is_live, json.dumps(summary)))
                new_row = cur.fetchone()
                if not new_row:
                    raise AppleSettlementError('Apple subscription insert failed')
                subscription_id = new_row['id']

            # This is deliberately NOT recognized Apple revenue. The signed
            # purchase proves entitlement, not settlement proceeds. Existing
            # schema mandates gross_amount_cents NOT NULL; 0 is a documented
            # unknown sentinel, currency 'xxx', and status remains PENDING until
            # verified App Store Connect financial reconciliation.
            pending_amount = {
                **summary, 'financial_state': 'unreconciled_not_revenue',
                'gross_amount_unknown': True, 'gross_zero_is_sentinel': True,
                'currency_unknown': True,
            }
            cur.execute("""INSERT INTO billing_transactions
                           (user_id, subscription_id, plan_code, provider,
                            transaction_kind, provider_invoice_id, currency,
                            gross_amount_cents, status, occurred_at, support_mode,
                            livemode, raw_summary_json)
                           VALUES (%s::uuid, %s::uuid, 'seeker', 'apple', %s, %s,
                                   'xxx', 0, 'pending', %s, 'monthly_recurring', %s,
                                   %s::jsonb) RETURNING id""",
                        (user_id, subscription_id,
                         'monthly_renewal' if owned_chain else 'monthly_initial',
                         receipt_id, facts.purchase_date, is_live, json.dumps(pending_amount)))
            receipt = cur.fetchone()
            if not receipt:
                raise AppleSettlementError('Apple monetary reconciliation placeholder failed')

            cur.execute("""INSERT INTO payment_events
                           (provider, provider_event_id, event_type, event_created_at,
                            object_type, object_id, user_id, subscription_id,
                            transaction_id, livemode, payload_json,
                            processing_status, processed_at, handler_version)
                           VALUES ('apple', %s, 'APPLE_VERIFIED_PURCHASE', %s,
                                   'transaction', %s, %s::uuid, %s::uuid, %s::uuid,
                                   %s, %s::jsonb, 'processed', NOW(), %s) RETURNING id""",
                        (event_id, facts.signed_date, facts.transaction_id, user_id,
                         subscription_id, receipt['id'], is_live, json.dumps(summary), _HANDLER))
            if not cur.fetchone():
                raise AppleSettlementError('Apple event receipt insert failed')

            cancel_end = owned_chain['cancel_at_period_end'] if owned_chain else False
            cur.execute("""UPDATE users SET plan_code='seeker', entitlement_status='active',
                           subscription_started_at=COALESCE(subscription_started_at,%s),
                           current_period_started_at=%s, subscription_renews_at=%s,
                           subscription_expires_at=%s, grace_period_ends_at=NULL, cancel_at_period_end=%s,
                           highest_paid_plan_ever=%s, last_paid_plan_code='seeker',
                           donor_floor_plan_code=%s, scroll_floor_plan_code=%s,
                           fallback_floor_plan_code=%s, renewal_offer_plan_code='seeker',
                           last_support_mode='monthly_recurring', last_support_ended_at=NULL,
                           updated_at=NOW() WHERE id=%s::uuid""",
                        (facts.purchase_date, facts.purchase_date, facts.expires_date,
                         facts.expires_date, cancel_end, highest, floors['donor'],
                         floors['scroll'], floors['fallback'], user_id))
            if cur.rowcount != 1:
                raise AppleSettlementError('Apple entitlement update failed')
        conn.commit()
        return AppleSettlementResult('granted', facts.transaction_id, str(subscription_id), True)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
