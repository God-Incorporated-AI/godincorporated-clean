import hashlib
import json
import os
from typing import Optional
from uuid import UUID

import psycopg2
import stripe
from psycopg2.extras import RealDictCursor


def get_db_connection():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")

    if db_url.startswith("postgresql+psycopg2://"):
        db_url = db_url.replace("postgresql+psycopg2://", "postgresql://", 1)

    return psycopg2.connect(db_url, cursor_factory=RealDictCursor)


def get_stripe_livemode() -> bool:
    return os.getenv("STRIPE_LIVEMODE", "false").lower() == "true"


def init_stripe() -> None:
    secret_key = os.getenv("STRIPE_SECRET_KEY")
    if not secret_key:
        raise RuntimeError("STRIPE_SECRET_KEY not set")
    stripe.api_key = secret_key


def get_checkout_plan_and_price(plan_code: str, support_mode: str) -> dict:
    normalized_plan = (plan_code or "").strip().lower()
    normalized_mode = (support_mode or "").strip().lower()

    if normalized_mode not in {"monthly_recurring", "annual_prepaid", "annual_recurring"}:
        raise ValueError(
            "Invalid support_mode. Use 'monthly_recurring', 'annual_prepaid', or 'annual_recurring'."
        )

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    plan_code,
                    display_name,
                    is_free_plan,
                    monthly_enabled,
                    annual_enabled,
                    annual_term_days
                FROM plan_catalog
                WHERE plan_code = %s
                LIMIT 1
                """,
                (normalized_plan,)
            )
            plan_row = cur.fetchone()

            if not plan_row:
                raise ValueError(f"Unknown plan_code: {normalized_plan}")

            if plan_row["is_free_plan"]:
                raise ValueError("This plan is not purchasable through Stripe.")

            if normalized_mode == "monthly_recurring" and not plan_row["monthly_enabled"]:
                raise ValueError(f"Monthly support is not enabled for {normalized_plan}.")

            if normalized_mode in {"annual_prepaid", "annual_recurring"} and not plan_row["annual_enabled"]:
                raise ValueError(f"Annual support is not enabled for {normalized_plan}.")

            cur.execute(
                """
                SELECT
                    stripe_product_id,
                    stripe_price_id,
                    livemode,
                    active
                FROM stripe_price_map
                WHERE plan_code = %s
                  AND support_mode = %s
                  AND livemode = %s
                  AND active = TRUE
                ORDER BY id DESC
                LIMIT 1
                """,
                (normalized_plan, normalized_mode, get_stripe_livemode())
            )
            price_row = cur.fetchone()

            if not price_row:
                raise ValueError(
                    f"No active Stripe price mapping found for plan_code={normalized_plan}, "
                    f"support_mode={normalized_mode}, livemode={get_stripe_livemode()}."
                )

            return {
                "plan_code": plan_row["plan_code"],
                "display_name": plan_row["display_name"],
                "support_mode": normalized_mode,
                "annual_term_days": plan_row["annual_term_days"],
                "stripe_product_id": price_row["stripe_product_id"],
                "stripe_price_id": price_row["stripe_price_id"],
                "livemode": price_row["livemode"],
            }
    finally:
        conn.close()


def get_existing_billing_customer(user_id: str) -> Optional[dict]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM billing_customers
                WHERE provider = 'stripe'
                  AND user_id = %s
                LIMIT 1
                """,
                (user_id,)
            )
            row = cur.fetchone()
            if row and not row.get("stripe_customer_id") and row.get("provider_customer_id"):
                row["stripe_customer_id"] = row["provider_customer_id"]
            return row
    finally:
        conn.close()


def upsert_billing_customer(
    user_id: str,
    stripe_customer_id: str,
    email_at_create: Optional[str],
    default_payment_method_id: Optional[str] = None,
) -> dict:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO billing_customers (
                    user_id,
                    provider,
                    provider_customer_id,
                    stripe_customer_id,
                    email_snapshot,
                    email_at_create,
                    default_payment_method_id,
                    customer_metadata_json,
                    livemode
                )
                VALUES (
                    %s,
                    'stripe',
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s::jsonb,
                    %s
                )
                ON CONFLICT (provider, user_id)
                DO UPDATE SET
                    provider_customer_id = EXCLUDED.provider_customer_id,
                    stripe_customer_id = EXCLUDED.stripe_customer_id,
                    email_snapshot = COALESCE(billing_customers.email_snapshot, EXCLUDED.email_snapshot),
                    email_at_create = COALESCE(billing_customers.email_at_create, EXCLUDED.email_at_create),
                    default_payment_method_id = COALESCE(EXCLUDED.default_payment_method_id, billing_customers.default_payment_method_id),
                    customer_metadata_json = EXCLUDED.customer_metadata_json,
                    livemode = EXCLUDED.livemode,
                    updated_at = NOW()
                RETURNING *
                """,
                (
                    user_id,
                    stripe_customer_id,
                    stripe_customer_id,
                    email_at_create,
                    email_at_create,
                    default_payment_method_id,
                    json.dumps({"source": "phase8_checkout"}),
                    get_stripe_livemode(),
                )
            )
            row = cur.fetchone()
        conn.commit()
        return row
    finally:
        conn.close()


def get_or_create_billing_customer(
    user_id: str,
    user_email: str,
    display_name: Optional[str] = None,
) -> dict:
    if not user_email:
        raise ValueError("Authenticated user must have an email address for billing.")

    existing = get_existing_billing_customer(user_id)
    if existing and existing.get("stripe_customer_id"):
        return existing

    init_stripe()

    customer = stripe.Customer.create(
        email=user_email,
        name=display_name or None,
        metadata={
            "user_id": user_id,
        },
    )

    return upsert_billing_customer(
        user_id=user_id,
        stripe_customer_id=customer["id"],
        email_at_create=user_email,
        default_payment_method_id=None,
    )



def stripe_obj_to_plain(value):
    if isinstance(value, dict):
        return {k: stripe_obj_to_plain(v) for k, v in value.items()}

    if isinstance(value, list):
        return [stripe_obj_to_plain(v) for v in value]

    raw_data = getattr(value, "_data", None)
    if isinstance(raw_data, dict):
        return {k: stripe_obj_to_plain(v) for k, v in raw_data.items()}

    return value


def get_subscription_item_id(subscription_obj: dict) -> str:
    items = ((subscription_obj.get("items") or {}).get("data") or [])
    if not items:
        raise ValueError("Stripe subscription has no subscription items.")
    item_id = items[0].get("id")
    if not item_id:
        raise ValueError("Stripe subscription item id is missing.")
    return item_id


def validate_paid_stripe_reup_invoice(
    invoice: dict,
    *,
    expected_user_id: str,
    expected_subscription_id: str,
    expected_customer_id: str,
    expected_price_id: str,
    expected_livemode: bool,
    minimum_paid_cents: int,
    previous_period_start: int,
) -> dict:
    """Fail-closed evidence contract for one explicit paid Stripe same-tier re-up.

    IMPORTANT: This function does NOT verify a Stripe signature or contact Stripe.
    Its caller MUST supply an invoice from a validated Stripe webhook or a
    server-authenticated Stripe invoice retrieval, then serialize settlement
    and the existing subscription/entitlement/ledger writes atomically.

    Validation alone MUST NEVER grant access. This function is not wired to
    checkout, webhooks, usage quotas, or the database in A7C2.
    """
    def insist(ok, message):
        if not ok:
            raise ValueError("Stripe paid re-up: " + message)

    insist(isinstance(invoice, dict) and invoice.get("object") == "invoice", "invoice object required")
    insist(invoice.get("status") == "paid" and invoice.get("paid") is True, "invoice is not paid")
    insist(invoice.get("paid_out_of_band") is False, "manually paid or unverified payment origin")
    insist(invoice.get("billing_reason") == "subscription_update", "not a subscription-update invoice")
    insist(type(expected_livemode) is bool and type(invoice.get("livemode")) is bool
           and invoice["livemode"] == expected_livemode, "Stripe environment mismatch")
    insist(invoice.get("currency") == "usd", "only verified USD payments supported")
    insist(type(minimum_paid_cents) is int and minimum_paid_cents > 0, "invalid expected paid amount")
    insist(type(previous_period_start) is int and previous_period_start > 0, "prior paid period identity missing")

    ident = invoice.get("id")
    insist(isinstance(ident, str) and ident.startswith("in_") and len(ident) > 3, "invoice ID missing")
    for identity, label in (
        (expected_user_id, "user"),
        (expected_subscription_id, "subscription"),
        (expected_customer_id, "customer"),
        (expected_price_id, "price"),
    ):
        insist(isinstance(identity, str) and bool(identity.strip()), label + " expectation missing")

    def id_of(value):
        return value.get("id") if isinstance(value, dict) else value

    parent = invoice.get("parent") or {}
    insist(isinstance(parent, dict), "invalid invoice parent")
    details = parent.get("subscription_details") or invoice.get("subscription_details") or {}
    insist(isinstance(details, dict), "invalid subscription details")

    top_subscription = id_of(invoice.get("subscription"))
    nested_subscription = id_of(details.get("subscription"))
    insist(not (top_subscription is not None and nested_subscription is not None
                and top_subscription != nested_subscription), "contradictory invoice subscription IDs")
    actual_subscription = top_subscription or nested_subscription
    insist(actual_subscription == expected_subscription_id, "subscription ownership mismatch")
    insist(id_of(invoice.get("customer")) == expected_customer_id, "billing customer mismatch")
    meta = details.get("metadata") or {}
    insist(isinstance(meta, dict), "invalid subscription metadata")
    insist(meta.get("change_source") == "phase8_paid_reup", "missing explicit paid re-up marker")
    insist(meta.get("user_id") == expected_user_id, "account identity mismatch")

    paid = invoice.get("amount_paid")
    total = invoice.get("total")
    insist(type(paid) is int and paid >= minimum_paid_cents, "full paid amount not confirmed")
    insist(type(total) is int and total >= minimum_paid_cents, "invoice amount invalid")
    insist(paid <= total, "paid amount exceeds billed total")
    insist(invoice.get("amount_remaining") == 0, "invoice balance remains unpaid")

    lines = invoice.get("lines") or {}
    insist(isinstance(lines, dict) and lines.get("has_more") is False, "incomplete invoice line set")
    data = lines.get("data")
    insist(isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict),
           "one complete paid subscription line required")
    line = data[0]
    legacy_price = id_of(line.get("price"))
    pricing = line.get("pricing") or {}
    insist(isinstance(pricing, dict), "invalid Stripe pricing")
    price_details = pricing.get("price_details") or {}
    insist(isinstance(price_details, dict), "invalid Stripe price details")
    modern_price = id_of(price_details.get("price"))
    insist(not (legacy_price is not None and modern_price is not None
                and legacy_price != modern_price), "contradictory subscription price IDs")
    insist((legacy_price or modern_price) == expected_price_id, "subscription price mismatch")
    insist(line.get("currency", "usd") == "usd", "subscription line currency mismatch")
    insist(line.get("quantity", 1) == 1 and type(line.get("quantity", 1)) is int,
           "exactly one subscribed item expected")
    insist(type(line.get("amount")) is int and line["amount"] >= minimum_paid_cents,
           "subscription line does not cover required price")

    parent_line = line.get("parent") or {}
    insist(isinstance(parent_line, dict), "invalid invoice line parent")
    sub_details = parent_line.get("subscription_item_details") or {}
    insist(isinstance(sub_details, dict), "invalid subscription line details")
    linked_subscription = id_of(sub_details.get("subscription"))
    insist(linked_subscription in (None, expected_subscription_id), "invoice line ownership mismatch")
    insist(line.get("proration") is not True and sub_details.get("proration") is not True,
           "proration cannot replenish a monthly allocation")

    period = line.get("period") or {}
    insist(isinstance(period, dict), "invoice period missing")
    start, end = period.get("start"), period.get("end")
    insist(type(start) is int and type(end) is int and end > start,
           "invalid verified billing period")
    insist(start > previous_period_start, "invoice does not open a new paid period")
    duration = end - start
    insist(27 * 86400 <= duration <= 32 * 86400, "expected one calendar month of support")

    return {
        "invoice_id": ident,
        "user_id": expected_user_id,
        "subscription_id": expected_subscription_id,
        "customer_id": expected_customer_id,
        "price_id": expected_price_id,
        "paid_cents": paid,
        "currency": "usd",
        "livemode": expected_livemode,
        "period_start": start,
        "period_end": end,
    }


def extract_stripe_single_item_period_v1(subscription_obj, *,
                                         expected_price_id=None,
                                         allow_legacy=False):
    """DORMANT, PURE: normalize one Stripe subscription item's billing period.

    For Stripe API >=2025-03-31, use items.data[0].current_period_start/end.
    Historical top-level periods are supported ONLY if an audited caller
    explicitly passes allow_legacy=True, and NEVER override item-level dates.
    Both representations, when present, must agree. The extracted period is
    NOT evidence of collected payment or authorization to grant entitlement.
    Requires exactly one complete priced item; no provider/DB calls or writes.
    """
    def require(condition, detail):
        if not condition:
            raise ValueError('Stripe subscription-item period: ' + detail)

    def pref(value):
        return value.get('id') if isinstance(value, dict) else value

    def checked_period(start, end, label):
        if start is None and end is None:
            return None
        require(type(start) is int and type(end) is int
                and 0 < start < end,
                label + ' missing, partial or invalid')
        return (start, end)

    require(type(allow_legacy) is bool, 'legacy policy not boolean')
    require(isinstance(subscription_obj, dict), 'subscription not normalized')
    require(subscription_obj.get('object') in (None, 'subscription'),
            'not a subscription object')
    subscription_id = pref(subscription_obj.get('id'))
    require(isinstance(subscription_id, str)
            and subscription_id.startswith('sub_') and len(subscription_id) > 4,
            'subscription identity missing')
    live = subscription_obj.get('livemode')
    require(type(live) is bool, 'subscription environment missing')

    container = subscription_obj.get('items')
    require(isinstance(container, dict)
            and container.get('object') in (None, 'list')
            and (container.get('has_more') is None
                 or container.get('has_more') is False),
            'subscription item collection incomplete')
    data = container.get('data')
    require(isinstance(data, list) and len(data) == 1
            and isinstance(data[0], dict),
            'exactly one complete subscription item required')
    item = data[0]
    require(item.get('object') in (None, 'subscription_item'),
            'subscription item type invalid')
    item_id = item.get('id')
    require(isinstance(item_id, str) and item_id.startswith('si_')
            and len(item_id) > 3, 'subscription item identity missing')
    quantity = item.get('quantity')
    require(quantity is None or (type(quantity) is int and quantity == 1),
            'subscription item quantity unsupported')
    price_id = pref(item.get('price'))
    require(isinstance(price_id, str) and price_id.startswith('price_')
            and len(price_id) > 6, 'subscription item Price missing')
    if expected_price_id is not None:
        require(isinstance(expected_price_id, str)
                and expected_price_id.startswith('price_')
                and expected_price_id == price_id,
                'Price conflicts with authoritative expectation')

    modern = checked_period(item.get('current_period_start'),
                            item.get('current_period_end'), 'item period')
    legacy = checked_period(subscription_obj.get('current_period_start'),
                            subscription_obj.get('current_period_end'),
                            'legacy subscription period')
    require(modern is not None or (allow_legacy and legacy is not None),
            'authoritative item period missing; legacy fallback not authorized')
    if modern is not None and legacy is not None:
        require(modern == legacy, 'item and legacy period conflict')
    start, end = modern if modern is not None else legacy
    return {
        'subscription_id': subscription_id,
        'price_id': price_id,
        'livemode': live,
        'period_start_unix': start,
        'period_end_unix': end,
        'period_source': ('subscription_item' if modern is not None
                          else 'legacy_subscription'),
    }

def change_existing_subscription_plan(
    user_id: str,
    user_email: str,
    display_name: Optional[str],
    current_subscription_id: str,
    plan_code: str,
    support_mode: str,
    *,
    force_paid_reup: bool = False,
    reup_intent_id: Optional[str] = None,
) -> dict:
    """Change a Stripe plan, or explicitly request a paid same-tier cycle.

    Existing callers retain their original plan-change behavior. A paid re-up
    requires invoice confirmation before the application grants new questions;
    this helper never changes user entitlements.
    """
    init_stripe()

    plan = get_checkout_plan_and_price(plan_code=plan_code, support_mode=support_mode)
    billing_customer = get_or_create_billing_customer(
        user_id=user_id,
        user_email=user_email,
        display_name=display_name,
    )

    current_subscription = stripe.Subscription.retrieve(current_subscription_id)
    current_subscription = stripe_obj_to_plain(current_subscription)

    if current_subscription.get("customer") != billing_customer["stripe_customer_id"]:
        raise ValueError("Existing Stripe subscription does not belong to this billing customer.")

    current_status = (current_subscription.get("status") or "").lower()
    if current_status not in {"active", "trialing", "past_due", "unpaid"}:
        raise ValueError(f"Subscription is not eligible for plan change from status={current_status}.")

    current_item_id = get_subscription_item_id(current_subscription)
    current_price_id = None
    try:
        current_price_id = current_subscription["items"]["data"][0]["price"]["id"]
    except Exception:
        pass

    same_price = current_price_id == plan["stripe_price_id"]
    reup_idempotency_key = None
    if force_paid_reup:
        if (
            not same_price
            or plan["support_mode"] != "monthly_recurring"
            or current_status != "active"
            or current_subscription.get("cancel_at_period_end")
        ):
            raise ValueError("Paid re-up requires an active uncancelled same-price monthly Stripe subscription.")
        period_contract = extract_stripe_single_item_period_v1(
            current_subscription, expected_price_id=plan["stripe_price_id"]
        )
        period_start = period_contract["period_start_unix"]
        try:
            intent = UUID(str(reup_intent_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Paid re-up requires a durable canonical UUIDv4 purchase intent.") from exc
        if intent.version != 4 or str(intent) != reup_intent_id:
            raise ValueError("Paid re-up requires a durable canonical UUIDv4 purchase intent.")
        reup_identity = (
            f"{user_id}|{current_subscription_id}|{intent}|{plan['stripe_price_id']}"
        )
        reup_idempotency_key = (
            "godinc-paid-reup-v2-"
            + hashlib.sha256(reup_identity.encode("utf-8")).hexdigest()
        )
    elif same_price and not current_subscription.get("cancel_at_period_end"):
        return {
            "changed_subscription": False,
            "message": f"You are already on active {plan['display_name']} recurring support.",
            "plan_code": plan["plan_code"],
            "support_mode": plan["support_mode"],
            "livemode": bool(current_subscription.get("livemode")),
            "subscription_obj": current_subscription,
        }

    metadata = dict(current_subscription.get("metadata") or {})
    metadata.update({
        "user_id": user_id,
        "plan_code": plan["plan_code"],
        "support_mode": plan["support_mode"],
        "change_source": "phase8_paid_reup" if force_paid_reup else "phase8_change_plan",
    })
    if force_paid_reup:
        metadata["reup_intent_id"] = reup_intent_id
    else:
        metadata.pop("reup_intent_id", None)

    modify_options = {
        "cancel_at_period_end": False,
        "billing_cycle_anchor": "now",
        "proration_behavior": "none",
        "payment_behavior": "error_if_incomplete",
        "items": [{"id": current_item_id, "price": plan["stripe_price_id"]}],
        "metadata": metadata,
    }
    if force_paid_reup:
        modify_options["idempotency_key"] = reup_idempotency_key
    updated_subscription = stripe.Subscription.modify(
        current_subscription_id,
        **modify_options,
    )
    updated_subscription = stripe_obj_to_plain(updated_subscription)

    return {
        "changed_subscription": True,
        "message": (
            "Paid re-up requested. Additional questions are pending a verified new invoice."
            if force_paid_reup else
            f"Support updated to {plan['display_name']}. The new billing cycle starts today."
        ),
        "pending_paid_invoice": bool(force_paid_reup),
        "plan_code": plan["plan_code"],
        "support_mode": plan["support_mode"],
        "livemode": bool(updated_subscription.get("livemode")),
        "subscription_obj": updated_subscription,
    }


def create_checkout_session_for_user(
    user_id: str,
    user_email: str,
    display_name: Optional[str],
    plan_code: str,
    support_mode: str,
    success_url: str,
    cancel_url: str,
) -> dict:
    init_stripe()

    plan = get_checkout_plan_and_price(plan_code=plan_code, support_mode=support_mode)
    billing_customer = get_or_create_billing_customer(
        user_id=user_id,
        user_email=user_email,
        display_name=display_name,
    )

    metadata = {
        "user_id": user_id,
        "plan_code": plan["plan_code"],
        "support_mode": plan["support_mode"],
    }

    params = {
        "customer": billing_customer["stripe_customer_id"],
        "client_reference_id": user_id,
        "line_items": [
            {
                "price": plan["stripe_price_id"],
                "quantity": 1,
            }
        ],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata": metadata,
    }

    if plan["support_mode"] in {"monthly_recurring", "annual_recurring"}:
        params["mode"] = "subscription"
        params["subscription_data"] = {
            "metadata": metadata,
        }
    else:
        params["mode"] = "payment"
        params["payment_intent_data"] = {
            "metadata": metadata,
        }

    session = stripe.checkout.Session.create(**params)

    return {
        "checkout_session_id": session["id"],
        "checkout_url": session["url"],
        "plan_code": plan["plan_code"],
        "support_mode": plan["support_mode"],
        "livemode": session["livemode"],
    }


def settle_ordinary_paid_existing_path(event, *, connection, bind, parsers, writers,
                                       expected_livemode):
    """Settle a signed, durable ordinary Stripe invoice in one transaction.

    Called only after event signature validation and durable inbox insertion.
    Legacy event versions remain held for manual reconciliation.
    """
    import datetime
    if type(expected_livemode) is not bool or event.get('livemode') is not expected_livemode:
        raise ValueError('Stripe environment mismatch; abort before database access')
    conn = connection()
    try:
        with conn.cursor() as cur:
            # Signed inbox is persisted before the business transaction.
            # Classify handler provenance before financial/account work.
            eid = event['id']
            cur.execute("""SELECT user_id::text AS user_id, event_type, processing_status,
                                  livemode, payload_json, handler_version
                           FROM payment_events
                           WHERE provider='stripe' AND provider_event_id=%s
                           FOR UPDATE""", (eid,))
            event_row = cur.fetchone()
            if not event_row or (event_row['event_type'] != 'invoice.paid' or
                 event_row['livemode'] is not event['livemode'] or
                 event_row['payload_json'] != event):
                raise ValueError('Signed inbox conflicts with delivered event')
            if event_row['handler_version'] != 'h4g17_stripe_invoice_atomic_v1':
                conn.rollback()
                return {'status':'legacy_event_hold'}
            if event_row['processing_status'] in {'failed','ignored'}:
                conn.rollback()
                return {'status':'manual_review'}
            if event_row['processing_status'] not in {'received','processed'}:
                raise ValueError('Unrecognized new-handler event state')
            b = bind(event, cursor=cur, helpers=parsers)
            if event_row['user_id'] != b['user_id']:
                raise ValueError('Signed event user binding differs')
            obj = event['data']['object']
            eid = event['id']
            invoice_id = obj['id']
            # Explicit account lock protects paid-through from an older invoice.
            cur.execute("""SELECT plan_code, entitlement_status, subscription_expires_at,
                                  last_support_mode
                           FROM users WHERE id=%s::uuid FOR UPDATE""", (b['user_id'],))
            account = cur.fetchone()
            if account is None:
                raise ValueError('Authenticated seeker account no longer exists')
            # An ambiguous simultaneous Apple subscription must not be rewritten by Stripe.
            cur.execute("""SELECT COUNT(*)::int AS collisions FROM subscriptions
                           WHERE provider='apple' AND user_id=%s::uuid
                             AND internal_status IN ('active','grace')
                             AND (current_period_end IS NULL OR current_period_end > NOW())""",
                        (b['user_id'],))
            if cur.fetchone()['collisions']:
                raise ValueError('Active Apple purchase collision: protect existing entitlement')
            cur.execute("""SELECT id, user_id::text AS user_id,
                                  subscription_id::text AS subscription_id,
                                  provider_invoice_id, stripe_event_id,
                                  livemode, status, gross_amount_cents,
                                  currency, plan_code, support_mode
                           FROM billing_transactions
                           WHERE provider='stripe' AND COALESCE(provider_invoice_id, stripe_invoice_id)=%s
                             AND livemode=%s FOR UPDATE""",
                        (invoice_id, event['livemode']))
            receipts = cur.fetchall()
            if len(receipts) > 1:
                raise ValueError('Ambiguous provider invoice receipt')
            receipt = receipts[0] if receipts else None
            if receipt is not None:
                if (receipt['user_id'] != b['user_id'] or
                    receipt['subscription_id'] != b['subscription_row_id'] or
                    receipt['livemode'] is not event['livemode'] or
                    receipt['plan_code'] != b['plan_code'] or
                    receipt['support_mode'] != b['support_mode'] or
                    receipt['currency'] != obj['currency']):
                    raise ValueError('Cross-account or cross-price receipt conflict')
            if event_row['processing_status'] == 'processed':
                if receipt is None or receipt['status'] != 'succeeded' or (
                    receipt['gross_amount_cents'] != obj['total']):
                    raise ValueError('Processed payment event lacks correct receipt')
                conn.rollback()
                return {'status':'duplicate_event'}
            if receipt is not None and receipt['status'] == 'succeeded':
                if receipt['gross_amount_cents'] != obj['total']:
                    raise ValueError('Repeated invoice has conflicting paid amount')
                # A historical receipt may predate atomic entitlement updates.
                # Do not acknowledge it as fulfilled without paid-through evidence.
                existing_expiry = account['subscription_expires_at']
                if (not isinstance(existing_expiry, datetime.datetime)
                    or existing_expiry.tzinfo is None
                    or existing_expiry < b['period_end']):
                    raise ValueError('Paid invoice receipt lacks verified paid-through; manual reconciliation required')
                writers['processed'](eid, 'duplicate_stripe_invoice_paid', cursor=cur)
                conn.commit()
                return {'status':'duplicate_invoice'}
            if receipt is not None and receipt['status'] == 'failed':
                cur.execute("""UPDATE billing_transactions SET status='succeeded',
                              gross_amount_cents=%s, net_amount_cents=%s,
                              amount_subtotal=%s, amount_total=%s, currency=%s,
                              stripe_event_id=%s, occurred_at=to_timestamp(%s)
                              WHERE id=%s AND status='failed' """,
                            (obj['total'], obj.get('subtotal') or obj['total'],
                             obj.get('subtotal'), obj['total'], obj['currency'],
                             eid, event['created'], receipt['id']))
                if cur.rowcount != 1:
                    raise ValueError('Failed-to-paid receipt transition lost ownership')
            elif receipt is None:
                writers['receipt'](event_id=eid, invoice_obj=obj,
                    user_id=b['user_id'], subscription_row_id=b['subscription_row_id'],
                    plan_code=b['plan_code'], support_mode=b['support_mode'],
                    transaction_kind=('annual_renewal' if b['support_mode']=='annual_recurring'
                                      else 'monthly_renewal'),
                    status='succeeded', cursor=cur)
            else:
                raise ValueError('Unsupported non-succeeded receipt status')
            expiry = account['subscription_expires_at']
            if expiry is not None:
                if not isinstance(expiry, datetime.datetime) or expiry.tzinfo is None:
                    raise ValueError('Unverifiable existing paid-through boundary')
            should_extend = expiry is None or expiry < b['period_end']
            if should_extend:
                writers['renewal'](b['user_id'], b['plan_code'],
                    period_start=b['period_start'], period_end=b['period_end'],
                    support_mode=b['support_mode'], cursor=cur)
                writers['fallback'](b['user_id'], cursor=cur)
            writers['processed'](eid, 'apply_subscription_renewal_success:'+b['support_mode'],
                                 cursor=cur)
        conn.commit()
        return {'status': 'processed' if should_extend else 'processed_no_regression'}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()



def bind_signed_ordinary_invoice_to_existing_account(event, *, cursor, helpers):
    """Bind a signed, durably received Stripe renewal invoice to one stored seeker.

    This function never verifies signatures or grants access. It must run only
    under the existing owner's transaction after signed-inbox provenance checks.
    Only current mapped prices and fully paid automatic subscription-cycle
    invoices qualify; historical/exceptional payments require separate review.
    """
    import datetime

    def require(ok, why):
        if not ok:
            raise ValueError('Stripe ordinary invoice binder: ' + why)

    def ref(v):
        return v.get('id') if isinstance(v, dict) else v

    def record(v):
        return v if isinstance(v, dict) else {}

    require(isinstance(event, dict) and event.get('type') == 'invoice.paid',
            'signed invoice.paid event required')
    require(isinstance(event.get('id'), str) and event['id'].startswith('evt_'),
            'event ID missing')
    mode = event.get('livemode')
    require(type(mode) is bool, 'event environment missing')
    require(type(event.get('created')) is int and event['created'] > 0,
            'event timestamp invalid')
    envelope = record(event.get('data'))
    invoice = envelope.get('object')
    require(isinstance(invoice, dict) and invoice.get('object') == 'invoice',
            'invoice payload required')
    invoice_id = invoice.get('id')
    require(isinstance(invoice_id, str) and invoice_id.startswith('in_'),
            'invoice ID missing')
    require(type(invoice.get('livemode')) is bool
            and invoice['livemode'] is mode, 'invoice environment conflict')
    require(invoice.get('status') == 'paid' and invoice.get('paid') is True,
            'successful invoice payment not established')
    require(invoice.get('paid_out_of_band') is False,
            'manual or unverified payment origin')
    require(invoice.get('billing_reason') == 'subscription_cycle',
            'not an ordinary subscription-cycle renewal')
    require(invoice.get('collection_method') == 'charge_automatically',
            'automatic collection proof absent')
    require(invoice.get('currency') == 'usd', 'USD-only invoice required')
    total = invoice.get('total')
    paid = invoice.get('amount_paid')
    require(type(total) is int and 0 < total <= 2147483647
            and type(paid) is int and paid == total
            and type(invoice.get('amount_remaining')) is int
            and invoice['amount_remaining'] == 0,
            'exact positive collected amount not established')
    require(ref(invoice.get('customer')) is not None,
            'invoice customer missing')
    customer = ref(invoice['customer'])
    require(isinstance(customer, str) and customer.startswith('cus_'),
            'invalid invoice customer')

    lines = record(invoice.get('lines'))
    data = lines.get('data')
    require(lines.get('has_more') is False and isinstance(data, list)
            and len(data) == 1 and isinstance(data[0], dict),
            'one complete subscription line required')
    line = data[0]
    line_parent = record(line.get('parent'))
    item_details = record(line_parent.get('subscription_item_details'))
    require(line.get('type') == 'subscription' or bool(item_details),
            'not a subscription item')
    require(line.get('proration') is not True
            and item_details.get('proration') is not True,
            'proration requires separate handling')
    require(type(line.get('quantity')) is int and line['quantity'] == 1,
            'one subscription unit required')
    require(line.get('currency') == 'usd'
            and type(line.get('amount')) is int and line['amount'] == total,
            'subscription line does not match fully paid total')
    legacy_price = ref(line.get('price'))
    pricing = record(line.get('pricing'))
    modern_price = ref(record(pricing.get('price_details')).get('price'))
    offered_prices = [x for x in (legacy_price, modern_price) if x is not None]
    require(offered_prices and len(set(offered_prices)) == 1
            and isinstance(offered_prices[0], str)
            and offered_prices[0].startswith('price_'),
            'subscription price missing or contradictory')
    price = offered_prices[0]

    parent_details = record(record(invoice.get('parent')).get('subscription_details'))
    parent_fallback = record(invoice.get('subscription_details'))
    subscription_refs = [ref(x) for x in (
        invoice.get('subscription'),
        parent_details.get('subscription'),
        parent_fallback.get('subscription'),
        item_details.get('subscription')) if x is not None]
    require(subscription_refs and len(set(subscription_refs)) == 1
            and isinstance(subscription_refs[0], str)
            and subscription_refs[0].startswith('sub_'),
            'invoice subscription missing or contradictory')
    subscription = subscription_refs[0]
    period = record(line.get('period'))
    start, end = period.get('start'), period.get('end')
    require(type(start) is int and type(end) is int
            and start > 0 and end > start,
            'invalid subscription service period')
    try:
        period_start = datetime.datetime.fromtimestamp(start, tz=datetime.timezone.utc)
        period_end = datetime.datetime.fromtimestamp(end, tz=datetime.timezone.utc)
    except (OverflowError, ValueError, OSError):
        raise ValueError('Stripe ordinary invoice binder: impossible service timestamp')

    cursor.execute("""SELECT s.id::text AS subscription_row_id, s.user_id::text AS user_id,
                          s.plan_code, s.support_mode, s.livemode, s.internal_status,
                          s.stripe_subscription_id, s.provider_subscription_id,
                          s.stripe_price_id, s.provider_price_id,
                          bc.user_id::text AS customer_user_id,
                          bc.provider_customer_id, bc.stripe_customer_id,
                          u.is_banned
                   FROM public.subscriptions s
                   JOIN public.billing_customers bc ON bc.id=s.billing_customer_id
                   JOIN public.users u ON u.id=s.user_id
                   WHERE s.provider='stripe' AND s.livemode=%s
                     AND (s.stripe_subscription_id=%s
                          OR s.provider_subscription_id=%s)
                     AND bc.provider='stripe' AND bc.livemode=%s
                     AND bc.provider_customer_id=%s AND bc.stripe_customer_id=%s
                   FOR UPDATE OF s, bc""",
                   (mode, subscription, subscription, mode, customer, customer))
    owners = cursor.fetchall()
    require(isinstance(owners, list) and len(owners) == 1,
            'existing Stripe owner missing or ambiguous')
    owner = owners[0]
    require(owner['customer_user_id'] == owner['user_id']
            and owner['is_banned'] is False
            and owner['internal_status'] in ('active', 'grace'),
            'account or stored subscription ineligible')
    for key, expected in (
        ('stripe_subscription_id', subscription),
        ('provider_subscription_id', subscription),
        ('stripe_price_id', price), ('provider_price_id', price)):
        require(owner[key] in (None, expected), 'stored ' + key + ' conflicts')
    require(owner['stripe_subscription_id'] == subscription
            or owner['provider_subscription_id'] == subscription,
            'stored Stripe subscription ID absent')
    require(owner['stripe_price_id'] == price or owner['provider_price_id'] == price,
            'stored Stripe price ID absent')
    mode_name = owner['support_mode']
    require(mode_name in ('monthly_recurring', 'annual_recurring'),
            'ordinary renewal support mode unsupported')
    days = (end - start) / 86400
    require((27 <= days <= 32) if mode_name == 'monthly_recurring'
            else (364 <= days <= 367),
            'billing duration does not match stored support mode')
    for metadata in (record(invoice.get('metadata')),
                     record(parent_details.get('metadata')),
                     record(parent_fallback.get('metadata')),
                     record(line.get('metadata'))):
        for field, actual in (('user_id', owner['user_id']),
                              ('plan_code', owner['plan_code'])):
            require(field not in metadata or metadata[field] == actual,
                    'untrusted metadata conflicts with stored ' + field)

    cursor.execute("""SELECT m.stripe_price_id, m.stripe_product_id,
                          p.active AS plan_active, p.is_free_plan,
                          p.monthly_enabled, p.annual_enabled,
                          p.monthly_price_cents, p.annual_prepaid_price_cents
                   FROM public.stripe_price_map m
                   JOIN public.plan_catalog p ON p.plan_code=m.plan_code
                   WHERE m.plan_code=%s AND m.support_mode=%s
                     AND m.livemode=%s AND m.stripe_price_id=%s
                     AND m.active=TRUE
                   FOR SHARE OF m, p""",
                   (owner['plan_code'], mode_name, mode, price))
    prices = cursor.fetchall()
    require(isinstance(prices, list) and len(prices) == 1,
            'matching current Stripe price mapping missing or ambiguous')
    mapping = prices[0]
    require(mapping['plan_active'] is True and mapping['is_free_plan'] is False
            and (mapping['monthly_enabled'] is True if mode_name == 'monthly_recurring'
                 else mapping['annual_enabled'] is True),
            'mapped paid plan not eligible')
    expected_cents = (mapping['monthly_price_cents'] if mode_name == 'monthly_recurring'
                      else mapping['annual_prepaid_price_cents'])
    require(type(expected_cents) is int and expected_cents == total,
            'collected amount differs from mapped catalog price')
    embedded_price = line.get('price')
    if isinstance(embedded_price, dict) and embedded_price.get('product') is not None:
        require(ref(embedded_price['product']) == mapping['stripe_product_id'],
                'expanded invoice price product conflicts')
    return {
        'user_id': owner['user_id'],
        'subscription_row_id': owner['subscription_row_id'],
        'plan_code': owner['plan_code'],
        'support_mode': mode_name,
        'period_start': period_start,
        'period_end': period_end,
        'invoice_id': invoice_id,
        'price_id': price,
        'paid_cents': paid,
        'livemode': mode,
    }


def bind_initial_stripe_cash_proof_to_account_v1(event, proof, *, cursor,
                                                  expected_livemode):
    """DORMANT: bind separately authenticated initial cash evidence to Local authority.

    Caller must first validate Stripe's signed event plus authenticated provider
    InvoicePayment/PaymentIntent using validate_initial_stripe_cash_proof_v1.
    Runs only inside the future settlement owner's shared transaction; does not
    commit, create subscriptions, grant entitlement, or call Stripe. A missing
    local subscription is a recoverable hold, not a reason to trust metadata.
    """
    def require(ok, message):
        if not ok:
            raise ValueError('Initial Stripe account binding: ' + message)

    def record(obj):
        return obj if isinstance(obj, dict) else {}

    def embedded_ref(value):
        return value.get('id') if isinstance(value, dict) else value

    def valid_ref(value, prefix):
        return (isinstance(value, str) and value.startswith(prefix)
                and len(value) > len(prefix))

    require(type(expected_livemode) is bool and cursor is not None,
            'environment or shared cursor missing')
    require(isinstance(proof, dict) and isinstance(event, dict),
            'verified cash proof and signed event required')
    for key, prefix in (('event_id', 'evt_'), ('invoice_id', 'in_'),
                        ('invoice_payment_id', 'inpay_'),
                        ('payment_intent_id', 'pi_'), ('customer_id', 'cus_'),
                        ('subscription_id', 'sub_'), ('price_id', 'price_')):
        require(valid_ref(proof.get(key), prefix), 'cash-proof identity missing: ' + key)
    require(proof.get('livemode') is expected_livemode
            and proof.get('currency') == 'usd'
            and type(proof.get('paid_cents')) is int
            and 0 < proof['paid_cents'] <= 100000000,
            'cash-proof environment or amount invalid')
    start, end = proof.get('period_start_unix'), proof.get('period_end_unix')
    require(type(start) is int and type(end) is int and 0 < start < end,
            'cash-proof service period invalid')
    mode = proof.get('support_mode_from_period')
    days = (end - start) / 86400
    require((mode == 'monthly_recurring' and 27 <= days <= 32)
            or (mode == 'annual_recurring' and 364 <= days <= 367),
            'cash-proof billing interval inconsistent')

    invoice = record(record(event.get('data')).get('object'))
    require(event.get('type') == 'invoice.paid'
            and event.get('id') == proof['event_id']
            and event.get('livemode') is expected_livemode
            and invoice.get('id') == proof['invoice_id']
            and embedded_ref(invoice.get('customer')) == proof['customer_id']
            and invoice.get('livemode') is expected_livemode
            and invoice.get('billing_reason') == 'subscription_create',
            'signed initial invoice and normalized proof disagree')

    cursor.execute("""SELECT bc.id::text AS billing_customer_id,
                             bc.user_id::text AS user_id,
                             bc.provider_customer_id, bc.stripe_customer_id,
                             bc.livemode, u.is_banned
                      FROM public.billing_customers bc
                      JOIN public.users u ON u.id = bc.user_id
                      WHERE bc.provider='stripe'
                        AND bc.provider_customer_id=%s
                        AND bc.stripe_customer_id=%s
                        AND bc.livemode=%s
                      FOR UPDATE OF bc, u""",
                   (proof['customer_id'], proof['customer_id'], expected_livemode))
    owners = cursor.fetchall()
    require(isinstance(owners, list) and len(owners) == 1,
            'authoritative Stripe customer missing or ambiguous')
    owner = owners[0]
    require(isinstance(owner, dict)
            and owner.get('is_banned') is False
            and owner.get('livemode') is expected_livemode
            and owner.get('provider_customer_id') == proof['customer_id']
            and owner.get('stripe_customer_id') == proof['customer_id']
            and owner.get('user_id')
            and owner.get('billing_customer_id'),
            'stored customer identity or eligibility conflicts')

    cursor.execute("""SELECT m.plan_code, m.support_mode,
                             m.stripe_product_id,
                             p.active AS plan_active, p.is_free_plan,
                             p.monthly_enabled, p.annual_enabled,
                             p.monthly_price_cents, p.annual_prepaid_price_cents
                      FROM public.stripe_price_map m
                      JOIN public.plan_catalog p ON p.plan_code=m.plan_code
                      WHERE m.stripe_price_id=%s AND m.livemode=%s
                        AND m.active=TRUE
                      FOR SHARE OF m, p""",
                   (proof['price_id'], expected_livemode))
    maps = cursor.fetchall()
    require(isinstance(maps, list) and len(maps) == 1,
            'active Stripe Price mapping missing or ambiguous')
    mapped = maps[0]
    require(isinstance(mapped, dict), 'Stripe Price mapping malformed')
    mapped_mode = mapped.get('support_mode')
    expected_cents = (mapped.get('monthly_price_cents')
                      if mapped_mode == 'monthly_recurring'
                      else mapped.get('annual_prepaid_price_cents'))
    require(mapped_mode == mode
            and mapped.get('plan_active') is True
            and mapped.get('is_free_plan') is False
            and (mapped.get('monthly_enabled') is True
                 if mode == 'monthly_recurring'
                 else mapped.get('annual_enabled') is True)
            and isinstance(mapped.get('plan_code'), str)
            and bool(mapped['plan_code'])
            and valid_ref(mapped.get('stripe_product_id'), 'prod_')
            and type(expected_cents) is int
            and expected_cents == proof['paid_cents'],
            'Stripe Price/catalog/period/cash amount mismatch')

    lines = record(invoice.get('lines')).get('data')
    require(isinstance(lines, list) and len(lines) == 1,
            'initial invoice line inventory conflicts')
    line = record(lines[0])
    metadata_sources = (
        record(invoice.get('metadata')),
        record(record(record(invoice.get('parent')).get('subscription_details')).get('metadata')),
        record(line.get('metadata')),
        record(record(record(line.get('parent')).get('subscription_item_details')).get('metadata')),
    )
    for metadata in metadata_sources:
        for key, authoritative in (
            ('user_id', str(owner['user_id'])),
            ('plan_code', mapped['plan_code']),
            ('support_mode', mode),
        ):
            require(key not in metadata or metadata[key] == authoritative,
                    'unsigned metadata conflicts with authoritative ' + key)

    # User lock serializes settlement decisions for this account. A different
    # active provider rail is a hard hold, not a reason to modify Apple/Google.
    cursor.execute("""SELECT count(*)::int AS collisions
                      FROM public.subscriptions
                      WHERE user_id=%s::uuid
                        AND provider IN ('apple','google','stripe')
                        AND internal_status IN ('active','grace')
                        AND (current_period_end IS NULL
                             OR current_period_end > NOW())
                        AND (provider <> 'stripe' OR
                             COALESCE(stripe_subscription_id,
                                      provider_subscription_id)
                             IS DISTINCT FROM %s)""",
                   (str(owner['user_id']), proof['subscription_id']))
    collision = cursor.fetchone()
    require(isinstance(collision, dict)
            and type(collision.get('collisions')) is int
            and collision['collisions'] == 0,
            'another active paid rail conflicts')

    cursor.execute("""SELECT id::text AS subscription_row_id,
                             user_id::text AS user_id,
                             billing_customer_id::text AS billing_customer_id,
                             livemode, plan_code, support_mode,
                             stripe_price_id, provider_price_id
                      FROM public.subscriptions
                      WHERE provider='stripe'
                        AND (stripe_subscription_id=%s
                             OR provider_subscription_id=%s)
                      FOR UPDATE""",
                   (proof['subscription_id'], proof['subscription_id']))
    rows = cursor.fetchall()
    require(isinstance(rows, list) and len(rows) <= 1,
            'provider subscription identity ambiguous')
    subscription_id = None
    if rows:
        stored = rows[0]
        require(isinstance(stored, dict)
                and str(stored.get('user_id')) == str(owner['user_id'])
                and str(stored.get('billing_customer_id'))
                    == str(owner['billing_customer_id'])
                and stored.get('livemode') is expected_livemode
                and stored.get('plan_code') == mapped['plan_code']
                and stored.get('support_mode') == mode
                and stored.get('stripe_price_id') in (None, proof['price_id'])
                and stored.get('provider_price_id') in (None, proof['price_id'])
                and stored.get('subscription_row_id'),
                'existing subscription owner, plan or Price conflicts')
        subscription_id = str(stored['subscription_row_id'])

    return {
        'status': ('bound' if subscription_id is not None
                   else 'awaiting_subscription_row'),
        'user_id': str(owner['user_id']),
        'billing_customer_id': str(owner['billing_customer_id']),
        'subscription_row_id': subscription_id,
        'plan_code': mapped['plan_code'],
        'support_mode': mode,
        'stripe_product_id': mapped['stripe_product_id'],
        'stripe_price_id': proof['price_id'],
        'stripe_subscription_id': proof['subscription_id'],
        'invoice_id': proof['invoice_id'],
        'event_id': proof['event_id'],
        'invoice_payment_id': proof['invoice_payment_id'],
        'payment_intent_id': proof['payment_intent_id'],
        'period_start_unix': start, 'period_end_unix': end,
        'paid_cents': proof['paid_cents'],
        'livemode': expected_livemode,
    }


def fetch_verified_stripe_initial_cash_bundle_v1(event, *, provider,
                                                  to_plain, expected_livemode):
    """DORMANT: read independent first-payment evidence from authenticated Stripe.

    Caller MUST first verify the HTTP Stripe signature and isolate early re-ups.
    `provider` is an initialized, trusted stripe-python SDK API object, not
    untrusted request data. Nothing here authenticates event signatures, sets
    credentials, writes database rows, grants access, or schedules retries.
    Provider read failures propagate for the durable inbox owner to retry.
    Do not persist/log the returned raw provider objects or mutate the signed
    event. Only a later locked and atomic owner may persist a receipt or grant.
    """
    def require(ok, reason):
        if not ok:
            raise ValueError('Initial Stripe provider reads: ' + reason)

    def ref(value, prefix):
        if isinstance(value, dict):
            value = value.get('id')
        require(isinstance(value, str) and value.startswith(prefix)
                and len(value) > len(prefix), 'required provider identity missing')
        return value

    def plain(response, label):
        try:
            data = to_plain(response)
        except Exception as exc:
            raise ValueError('Initial Stripe provider reads: invalid ' + label
                             + ' conversion') from exc
        require(isinstance(data, dict), label + ' is not a plain object')
        return data

    require(type(expected_livemode) is bool, 'expected Stripe environment missing')
    require(isinstance(event, dict) and event.get('type') == 'invoice.paid',
            'verified initial invoice.paid required')
    require(type(event.get('livemode')) is bool
            and event['livemode'] is expected_livemode,
            'signed event environment mismatch')
    require(type(event.get('created')) is int and event['created'] > 0,
            'signed event timestamp missing')
    ref(event.get('id'), 'evt_')
    data = event.get('data')
    require(isinstance(data, dict), 'signed event data missing')
    signed = data.get('object')
    require(isinstance(signed, dict) and signed.get('object') == 'invoice'
            and signed.get('billing_reason') == 'subscription_create',
            'signed initial invoice required')
    require(type(signed.get('livemode')) is bool
            and signed['livemode'] is expected_livemode,
            'signed invoice environment mismatch')
    invoice_id = ref(signed.get('id'), 'in_')
    customer_id = ref(signed.get('customer'), 'cus_')
    require(callable(to_plain), 'trusted Stripe object converter missing')
    invoice_api = getattr(getattr(provider, 'Invoice', None), 'retrieve', None)
    payments_api = getattr(getattr(provider, 'InvoicePayment', None), 'list', None)
    intent_api = getattr(getattr(provider, 'PaymentIntent', None), 'retrieve', None)
    require(callable(invoice_api) and callable(payments_api) and callable(intent_api),
            'supported Stripe API readers missing')

    verified = plain(invoice_api(invoice_id), 'Invoice')
    require(verified.get('object') == 'invoice'
            and verified.get('id') == invoice_id
            and verified.get('livemode') is expected_livemode
            and ref(verified.get('customer'), 'cus_') == customer_id
            and verified.get('billing_reason') == 'subscription_create',
            'authenticated provider Invoice identity conflicts')
    require(verified.get('status') == 'paid'
            and verified.get('collection_method') == 'charge_automatically'
            and type(verified.get('total')) is int
            and 0 < verified['total'] <= 100000000,
            'provider invoice is not an eligible paid purchase')

    listed = plain(payments_api(invoice=invoice_id, limit=2), 'InvoicePayment list')
    entries = listed.get('data')
    require(listed.get('object') == 'list'
            and listed.get('has_more') is False
            and isinstance(entries, list) and len(entries) == 1,
            'exactly one complete provider invoice-payment record required')
    payment = plain(entries[0], 'InvoicePayment')
    require(payment.get('object') == 'invoice_payment'
            and payment.get('status') == 'paid'
            and payment.get('livemode') is expected_livemode
            and ref(payment.get('invoice'), 'in_') == invoice_id,
            'provider invoice payment identity or paid state conflicts')
    ref(payment.get('id'), 'inpay_')
    payment_link = payment.get('payment')
    require(isinstance(payment_link, dict)
            and payment_link.get('type') == 'payment_intent',
            'out-of-band or unsupported invoice payment')
    intent_id = ref(payment_link.get('payment_intent'), 'pi_')

    intent = plain(intent_api(intent_id), 'PaymentIntent')
    require(intent.get('object') == 'payment_intent'
            and intent.get('id') == intent_id
            and intent.get('livemode') is expected_livemode
            and intent.get('status') == 'succeeded'
            and ref(intent.get('customer'), 'cus_') == customer_id,
            'authenticated PaymentIntent state or identity conflicts')
    proof = validate_initial_stripe_cash_proof_v1(
        event, invoice_payment=payment, payment_intent=intent,
        verified_invoice=verified, expected_livemode=expected_livemode)
    require(isinstance(proof, dict)
            and proof.get('invoice_id') == invoice_id
            and proof.get('invoice_payment_id') == payment['id']
            and proof.get('payment_intent_id') == intent_id
            and proof.get('customer_id') == customer_id
            and proof.get('livemode') is expected_livemode,
            'normalized paid evidence differs from provider objects')
    return {'proof': proof, 'verified_invoice': verified,
            'invoice_payment': payment, 'payment_intent': intent}

def validate_initial_stripe_cash_proof_v1(event, *, invoice_payment,
                                         payment_intent, expected_livemode,
                                         verified_invoice=None):
    """Validate a first subscription invoice against a separate Stripe payment pair.

    PURE/DORMANT. Caller must verify Stripe signature and obtain the independent
    Invoice, InvoicePayment and PaymentIntent through authenticated Stripe reads.
    This function cannot verify the objects' provenance, contact Stripe, bind
    a seeker, or grant access. Older signed invoices without provider Invoice
    may be accepted only with explicit legacy on-Stripe paid booleans.
    Modern provider Invoice evidence must agree with immutable signed event.
    Customer/price/rail ownership is enforced by the separate account binder.
    """
    def require(ok, reason):
        if not ok:
            raise ValueError('Initial Stripe cash proof: ' + reason)

    def record(obj):
        return obj if isinstance(obj, dict) else {}

    def ref(value, prefix):
        if isinstance(value, dict):
            value = value.get('id')
        require(isinstance(value, str) and value.startswith(prefix)
                and len(value) > len(prefix), 'provider identifier missing')
        return value

    require(type(expected_livemode) is bool, 'expected environment missing')
    require(isinstance(event, dict) and event.get('type') == 'invoice.paid',
            'signed invoice.paid event required')
    eid = ref(event.get('id'), 'evt_')
    require(type(event.get('livemode')) is bool
            and event['livemode'] is expected_livemode,
            'event environment mismatch')
    require(type(event.get('created')) is int and event['created'] > 0,
            'event creation timestamp invalid')
    signed = record(record(event.get('data')).get('object'))
    require(signed.get('object') == 'invoice', 'signed invoice missing')
    if verified_invoice is None:
        require(signed.get('paid') is True
                and signed.get('paid_out_of_band') is False,
                'legacy event requires explicit on-Stripe paid flags')
        invoice = signed
    else:
        require(isinstance(verified_invoice, dict)
                and verified_invoice is not signed,
                'separately authenticated provider Invoice required')
        invoice = verified_invoice
        for key in ('id', 'object', 'billing_reason', 'collection_method',
                    'status', 'currency', 'total', 'amount_paid',
                    'amount_remaining'):
            if signed.get(key) is not None:
                require(signed[key] == invoice.get(key),
                        'signed/provider invoice conflicts: ' + key)
        require(ref(signed.get('customer'), 'cus_')
                == ref(invoice.get('customer'), 'cus_')
                and signed.get('livemode') is invoice.get('livemode'),
                'signed/provider customer or environment conflicts')
        if signed.get('paid') is not None:
            require(signed['paid'] is True,
                    'signed event has incompatible paid status')
        if signed.get('paid_out_of_band') is not None:
            require(signed['paid_out_of_band'] is False,
                    'signed event indicates off-Stripe payment')
    require(invoice.get('object') == 'invoice', 'invoice object missing')
    iid = ref(invoice.get('id'), 'in_')
    customer = ref(invoice.get('customer'), 'cus_')
    require(type(invoice.get('livemode')) is bool
            and invoice['livemode'] is expected_livemode,
            'invoice environment mismatch')
    require(invoice.get('billing_reason') == 'subscription_create',
            'initial subscription invoice required')
    require(invoice.get('collection_method') == 'charge_automatically',
            'automatic collection required')
    require(invoice.get('status') == 'paid', 'invoice must be paid')
    if invoice.get('paid') is not None:
        require(invoice['paid'] is True, 'legacy paid status conflicts')
    if invoice.get('paid_out_of_band') is not None:
        require(invoice['paid_out_of_band'] is False,
                'legacy off-Stripe payment conflicts')
    total = invoice.get('total')
    require(type(total) is int and 0 < total <= 100000000,
            'positive invoice total invalid')
    require(type(invoice.get('amount_paid')) is int
            and invoice['amount_paid'] == total
            and type(invoice.get('amount_remaining')) is int
            and invoice['amount_remaining'] == 0,
            'invoice not paid in full')
    require(invoice.get('currency') == 'usd', 'invoice currency unsupported')
    if invoice.get('amount_due') is not None:
        require(type(invoice['amount_due']) is int
                and invoice['amount_due'] == total,
                'invoice balance-credit or adjusted due unsupported')
    for key in ('starting_balance', 'amount_overpaid',
                'amount_paid_off_stripe'):
        if invoice.get(key) is not None:
            require(type(invoice[key]) is int and invoice[key] == 0,
                    'non-cash invoice balance unsupported')

    lines = record(invoice.get('lines'))
    rows = lines.get('data')
    require(isinstance(rows, list) and len(rows) == 1
            and lines.get('has_more') is False,
            'exactly one complete subscription invoice line required')
    line = record(rows[0])
    require(line.get('currency') == 'usd'
            and type(line.get('amount')) is int
            and line['amount'] == total
            and type(line.get('quantity')) is int
            and line['quantity'] == 1,
            'subscription line amount, currency or quantity invalid')
    parent = record(line.get('parent'))
    details = record(parent.get('subscription_item_details'))
    require(details.get('proration') is not True and line.get('proration') is not True,
            'proration not eligible for initial invoice')
    period = record(line.get('period'))
    start, end = period.get('start'), period.get('end')
    require(type(start) is int and type(end) is int
            and start > 0 and end > start,
            'initial subscription service period missing')
    days = (end - start) / 86400
    if 27 <= days <= 32:
        mode = 'monthly_recurring'
    elif 364 <= days <= 367:
        mode = 'annual_recurring'
    else:
        raise ValueError('Initial Stripe cash proof: unsupported billing duration')

    refs = []
    for value in (invoice.get('subscription'),
                  record(record(invoice.get('parent')).get('subscription_details')).get('subscription'),
                  details.get('subscription')):
        if value is not None:
            refs.append(ref(value, 'sub_'))
    require(bool(refs) and len(set(refs)) == 1,
            'subscription references missing or contradictory')
    subscription = refs[0]

    price_refs = []
    for value in (line.get('price'),
                  record(record(line.get('pricing')).get('price_details')).get('price')):
        if value is not None:
            price_refs.append(ref(value, 'price_'))
    require(bool(price_refs) and len(set(price_refs)) == 1,
            'price references missing or contradictory')
    price = price_refs[0]

    if verified_invoice is not None:
        original_lines = record(signed.get('lines'))
        original_rows = original_lines.get('data')
        require(isinstance(original_rows, list) and len(original_rows) == 1
                and original_lines.get('has_more') is False,
                'immutable signed invoice line incomplete')
        original_line = record(original_rows[0])
        original_parent = record(original_line.get('parent'))
        original_details = record(original_parent.get('subscription_item_details'))
        original_subs = [ref(value, 'sub_') for value in (
            signed.get('subscription'),
            record(record(signed.get('parent')).get('subscription_details')).get('subscription'),
            original_details.get('subscription')) if value is not None]
        require(original_subs and set(original_subs) == {subscription},
                'signed/provider subscription identity conflicts')
        original_prices = [ref(value, 'price_') for value in (
            original_line.get('price'),
            record(record(original_line.get('pricing')).get('price_details')).get('price'))
            if value is not None]
        require(original_prices and set(original_prices) == {price},
                'signed/provider Price identity conflicts')
        for field in ('currency', 'amount', 'quantity'):
            if original_line.get(field) is not None:
                require(original_line[field] == line.get(field),
                        'signed/provider line value conflicts: ' + field)
        original_period = record(original_line.get('period'))
        require(original_period.get('start') == start
                and original_period.get('end') == end,
                'signed/provider service period conflicts')

    payment = record(invoice_payment)
    payment_id = ref(payment.get('id'), 'inpay_')
    require(payment.get('object') == 'invoice_payment'
            and payment.get('status') == 'paid'
            and payment.get('currency') == 'usd'
            and type(payment.get('livemode')) is bool
            and payment['livemode'] is expected_livemode
            and ref(payment.get('invoice'), 'in_') == iid,
            'invoice payment missing or mismatched')
    require(type(payment.get('amount_paid')) is int
            and payment['amount_paid'] == total
            and type(payment.get('amount_requested')) is int
            and payment['amount_requested'] == total,
            'invoice payment amount does not match total')
    pm = record(payment.get('payment'))
    require(pm.get('type') == 'payment_intent',
            'a paid Stripe PaymentIntent is required')
    pi_ref = ref(pm.get('payment_intent'), 'pi_')

    intent = record(payment_intent)
    require(intent.get('object') == 'payment_intent'
            and ref(intent.get('id'), 'pi_') == pi_ref
            and intent.get('status') == 'succeeded'
            and intent.get('currency') == 'usd'
            and type(intent.get('livemode')) is bool
            and intent['livemode'] is expected_livemode
            and ref(intent.get('customer'), 'cus_') == customer,
            'settled PaymentIntent identity or environment conflicts')
    require(type(intent.get('amount_received')) is int
            and intent['amount_received'] == total
            and type(intent.get('amount')) is int
            and intent['amount'] == total,
            'PaymentIntent collected amount differs')
    if intent.get('invoice') is not None:
        require(ref(intent['invoice'], 'in_') == iid,
                'PaymentIntent invoice reference conflicts')
    if invoice.get('payment_intent') is not None:
        require(ref(invoice['payment_intent'], 'pi_') == pi_ref,
                'invoice PaymentIntent reference conflicts')
    embedded = invoice.get('payments')
    if embedded is not None:
        embedded = record(embedded)
        data = embedded.get('data')
        require(isinstance(data, list) and len(data) == 1
                and embedded.get('has_more') is False
                and ref(record(data[0]).get('id'), 'inpay_') == payment_id,
                'embedded payment list conflicts with settled payment')

    return {'event_id': eid, 'invoice_id': iid, 'invoice_payment_id': payment_id,
            'payment_intent_id': pi_ref, 'customer_id': customer,
            'subscription_id': subscription, 'price_id': price,
            'period_start_unix': start, 'period_end_unix': end,
            'support_mode_from_period': mode, 'paid_cents': total,
            'currency': 'usd', 'livemode': expected_livemode}


def bind_signed_initial_invoice_to_customer_inbox_v1(event, *, cursor, helpers):
    """Bind signed initial invoice to existing billing customer; no cash or grants.

    Called only by the versioned inbox after webhook signature verification.
    A stored Stripe customer exists before a Checkout subscription is created.
    This binding does not require a local subscription row or trust metadata.
    """
    def require(ok, reason):
        if not ok:
            raise ValueError('Initial Stripe inbox binding: ' + reason)

    def ref(value, prefix):
        if isinstance(value, dict):
            value = value.get('id')
        require(isinstance(value, str) and value.startswith(prefix)
                and len(value) > len(prefix), 'provider identity missing')
        return value

    require(isinstance(event, dict) and cursor is not None and helpers == {},
            'signed event, shared cursor and fixed helper contract required')
    require(event.get('type') == 'invoice.paid'
            and type(event.get('livemode')) is bool,
            'signed paid invoice identity missing')
    require(isinstance(event.get('data'), dict), 'invoice event data missing')
    invoice = event['data'].get('object')
    require(isinstance(invoice, dict) and invoice.get('object') == 'invoice'
            and invoice.get('billing_reason') == 'subscription_create'
            and type(invoice.get('livemode')) is bool
            and invoice['livemode'] is event['livemode'],
            'initial invoice object or environment conflicts')
    invoice_id = ref(invoice.get('id'), 'in_')
    customer_id = ref(invoice.get('customer'), 'cus_')

    cursor.execute("""SELECT bc.id::text AS billing_customer_id,
                             bc.user_id::text AS user_id,
                             bc.provider_customer_id, bc.stripe_customer_id,
                             bc.livemode, u.is_banned
                      FROM public.billing_customers bc
                      JOIN public.users u ON u.id=bc.user_id
                      WHERE bc.provider='stripe'
                        AND bc.provider_customer_id=%s
                        AND bc.stripe_customer_id=%s
                        AND bc.livemode=%s
                      FOR UPDATE OF bc, u""",
                   (customer_id, customer_id, event['livemode']))
    owners = cursor.fetchall()
    require(isinstance(owners, list) and len(owners) == 1,
            'stored Stripe customer missing or ambiguous')
    owner = owners[0]
    require(isinstance(owner, dict)
            and owner.get('user_id') and owner.get('billing_customer_id')
            and owner.get('livemode') is event['livemode']
            and owner.get('is_banned') is False
            and owner.get('provider_customer_id') == customer_id
            and owner.get('stripe_customer_id') == customer_id,
            'stored billing customer or seeker eligibility conflicts')

    # Metadata may corroborate identity but is never an authority source.
    parent = invoice.get('parent') or {}
    require(isinstance(parent, dict), 'invoice parent metadata malformed')
    subscription_details = parent.get('subscription_details') or {}
    require(isinstance(subscription_details, dict), 'subscription metadata malformed')
    metadata_sources = [invoice.get('metadata'), subscription_details.get('metadata')]
    lines = invoice.get('lines') or {}
    require(isinstance(lines, dict), 'invoice line metadata malformed')
    line_items = lines.get('data') or []
    require(isinstance(line_items, list), 'invoice line inventory malformed')
    for item in line_items:
        require(isinstance(item, dict), 'invoice line malformed')
        metadata_sources.append(item.get('metadata'))
        line_parent = item.get('parent') or {}
        require(isinstance(line_parent, dict), 'line parent metadata malformed')
        details = line_parent.get('subscription_item_details') or {}
        require(isinstance(details, dict), 'subscription item metadata malformed')
        metadata_sources.append(details.get('metadata'))
    for metadata in metadata_sources:
        if metadata is None:
            continue
        require(isinstance(metadata, dict), 'untrusted metadata malformed')
        require('user_id' not in metadata
                or metadata['user_id'] == str(owner['user_id']),
                'untrusted metadata user conflicts with stored customer')

    return {'user_id': str(owner['user_id']), 'invoice_id': invoice_id,
            'livemode': event['livemode']}


def receive_verified_initial_invoice_inbox_v1(event, *, connection,
                                               expected_livemode):
    """Persist signed initial invoice for later independent cash verification.

    DORMANT. Caller must validate the Stripe HTTP signature first. The inbox
    stores authenticated provenance, not payment proof or entitlement.
    """
    if (not isinstance(event, dict) or event.get('type') != 'invoice.paid'
        or not isinstance(event.get('data'), dict)
        or not isinstance(event['data'].get('object'), dict)
        or event['data']['object'].get('billing_reason') != 'subscription_create'):
        raise ValueError('Initial Stripe inbox requires subscription_create invoice.paid')
    return receive_verified_ordinary_invoice_inbox(
        event, connection=connection,
        bind=bind_signed_initial_invoice_to_customer_inbox_v1,
        parsers={}, expected_livemode=expected_livemode,
        handler_version='h4g17_stripe_initial_atomic_v1')

def receive_verified_ordinary_invoice_inbox(event, *, connection, bind, parsers,
                                           expected_livemode,
                                           handler_version='h4g17_stripe_invoice_atomic_v1'):
    """Persist an authenticated supported Stripe invoice into a versioned inbox.

    Caller MUST first verify Stripe HTTP signature and classify early re-ups.
    This function does not validate signatures, grant access, or dispatch work.
    Only a subsequent independent transaction may call the paid owner.
    Older handler versions remain immutable and are never replayed here.
    """
    import json

    if handler_version not in ('h4g17_stripe_invoice_atomic_v1',
                               'h4g17_stripe_initial_atomic_v1'):
        raise ValueError('Stripe invoice inbox handler version not supported')
    version = handler_version
    def require(ok, reason):
        if not ok:
            raise ValueError('Ordinary Stripe inbox: ' + reason)

    require(type(expected_livemode) is bool, 'expected environment missing')
    require(isinstance(event, dict) and event.get('type') == 'invoice.paid',
            'ordinary signed invoice.paid required')
    eid = event.get('id')
    require(isinstance(eid, str) and eid.startswith('evt_'), 'event ID missing')
    require(type(event.get('livemode')) is bool
            and event['livemode'] is expected_livemode,
            'event environment mismatch')
    require(type(event.get('created')) is int and event['created'] > 0,
            'event creation time missing')
    data = event.get('data')
    require(isinstance(data, dict), 'event data missing')
    obj = data.get('object')
    require(isinstance(obj, dict) and obj.get('object') == 'invoice',
            'invoice object missing')
    invoice_id = obj.get('id')
    require(isinstance(invoice_id, str) and invoice_id.startswith('in_'),
            'invoice ID missing')
    require(type(obj.get('livemode')) is bool
            and obj['livemode'] is expected_livemode,
            'invoice environment mismatch')
    try:
        serialized = json.dumps(event, sort_keys=True, separators=(',', ':'),
                                allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError('Ordinary Stripe inbox: invalid JSON event') from exc

    def classify(row):
        require(isinstance(row, dict), 'recorded event missing after conflict')
        require(row.get('event_type') == 'invoice.paid'
                and row.get('livemode') is expected_livemode
                and row.get('payload_json') == event,
                'event ID conflicts with immutable signed payload')
        if row.get('handler_version') != version:
            return {'status': 'legacy_hold', 'user_id': None}
        status = row.get('processing_status')
        if status in ('failed', 'ignored'):
            return {'status': 'manual_review', 'user_id': None}
        require(status in ('received', 'processed'),
                'new-handler state not recognized')
        bound = bind(event, cursor=cur, helpers=parsers)
        require(isinstance(bound, dict) and bound.get('user_id')
                and str(row.get('user_id')) == str(bound['user_id']),
                'existing event owner differs from trusted binder')
        return {'status': ('already_received' if status == 'received'
                           else 'already_processed'),
                'user_id': bound['user_id']}

    conn = connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT user_id::text AS user_id, event_type, livemode,
                                  payload_json, handler_version, processing_status
                           FROM public.payment_events
                           WHERE provider='stripe' AND provider_event_id=%s
                           FOR UPDATE""", (eid,))
            existing = cur.fetchone()
            if existing is not None:
                result = classify(existing)
                conn.rollback()
                return result

            bound = bind(event, cursor=cur, helpers=parsers)
            require(isinstance(bound, dict) and bound.get('user_id')
                    and isinstance(bound.get('invoice_id'), str)
                    and bound['invoice_id'] == invoice_id
                    and bound.get('livemode') is expected_livemode,
                    'trusted account binding incomplete')
            cur.execute("""INSERT INTO public.payment_events (
                              provider, provider_event_id, stripe_event_id,
                              event_type, event_created_at, object_type,
                              object_id, user_id, livemode, api_version,
                              payload_json, processing_status, handler_version
                           ) VALUES ('stripe', %s, %s, 'invoice.paid',
                                     to_timestamp(%s), 'invoice', %s,
                                     %s::uuid, %s, %s, %s::jsonb, 'received', %s)
                           ON CONFLICT (provider, provider_event_id) DO NOTHING
                           RETURNING id""",
                        (eid, eid, event['created'], invoice_id,
                         bound['user_id'], expected_livemode,
                         event.get('api_version'), serialized, version))
            inserted = cur.fetchone()
            if inserted is None:
                # A second backend committed an identical event during our insert.
                cur.execute("""SELECT user_id::text AS user_id, event_type,
                                      livemode, payload_json, handler_version,
                                      processing_status
                               FROM public.payment_events
                               WHERE provider='stripe' AND provider_event_id=%s
                               FOR UPDATE""", (eid,))
                result = classify(cur.fetchone())
                conn.rollback()
                return result
        conn.commit()  # Signed received row survives future business rollback.
        return {'status': 'received', 'user_id': bound['user_id']}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def settle_initial_stripe_cash_atomic_v1(event, cash_bundle, *, connection,
                                         writers, expected_livemode):
    """DORMANT. Settle one signed, separately authenticated first Stripe payment.

    Caller MUST verify the HTTP webhook signature and durably receive the event
    under h4g17_stripe_initial_atomic_v1 BEFORE authenticated Stripe reads.
    This owner performs no provider/network calls, never manufactures a missing
    subscription, and keeps the receipt, entitlement/floor and event completion
    in ONE PostgreSQL transaction. The received event survives a failed settle.
    """
    import datetime

    def require(ok, reason):
        if not ok:
            raise ValueError('Initial Stripe atomic cash owner: ' + reason)

    require(type(expected_livemode) is bool,
            'expected Stripe environment missing')
    require(isinstance(event, dict) and event.get('type') == 'invoice.paid'
            and event.get('livemode') is expected_livemode
            and isinstance(event.get('data'), dict)
            and isinstance(event['data'].get('object'), dict)
            and event['data']['object'].get('billing_reason') == 'subscription_create',
            'signed first-payment event or environment missing')
    require(isinstance(cash_bundle, dict), 'authenticated cash bundle required')
    invoice = cash_bundle.get('verified_invoice')
    payment = cash_bundle.get('invoice_payment')
    intent = cash_bundle.get('payment_intent')
    require(isinstance(invoice, dict) and isinstance(payment, dict)
            and isinstance(intent, dict), 'three authenticated provider objects required')
    proof = validate_initial_stripe_cash_proof_v1(
        event, invoice_payment=payment, payment_intent=intent,
        verified_invoice=invoice, expected_livemode=expected_livemode)
    require(isinstance(proof, dict) and cash_bundle.get('proof') == proof,
            'cash evidence normalization or provenance differs')
    require(proof.get('event_id') == event.get('id')
            and proof.get('invoice_id') == event['data']['object'].get('id')
            and proof.get('livemode') is expected_livemode,
            'signed event and cash evidence differ')
    start = datetime.datetime.fromtimestamp(proof['period_start_unix'],
                                             tz=datetime.timezone.utc)
    end = datetime.datetime.fromtimestamp(proof['period_end_unix'],
                                           tz=datetime.timezone.utc)
    require(end > start, 'first-payment service period invalid')
    expected_kind = ('monthly_initial' if proof['support_mode_from_period']
                     == 'monthly_recurring' else 'annual_initial')
    require(isinstance(writers, dict) and set(writers) ==
            {'receipt', 'renewal', 'fallback', 'processed'}
            and all(callable(v) for v in writers.values()),
            'one set of shared-cursor financial writers required')
    require(callable(connection), 'controlled PostgreSQL connection required')

    def guard_legacy_completion_scope(cur, eid):
        # The existing shared writer updates by either event-id alias. Refuse
        # to invoke it if that historical OR predicate could affect two rows.
        cur.execute('''SELECT provider, provider_event_id, stripe_event_id
                       FROM public.payment_events
                       WHERE provider_event_id=%s OR stripe_event_id=%s
                       FOR UPDATE''', (eid, eid))
        matches = cur.fetchall()
        require(isinstance(matches, list) and len(matches) == 1
                and matches[0].get('provider') == 'stripe'
                and matches[0].get('provider_event_id') == eid
                and matches[0].get('stripe_event_id') == eid,
                'legacy completion writer event alias is not unique')

    def attach_event(cur, eid, recorded, receipt_id, bound):
        require(recorded.get('linked_transaction_id') in (None, receipt_id)
                and recorded.get('linked_subscription_id') in
                    (None, bound['subscription_row_id']),
                'signed event financial provenance already bound differently')
        cur.execute('''UPDATE public.payment_events
                       SET transaction_id=%s::uuid, subscription_id=%s::uuid
                       WHERE provider='stripe' AND provider_event_id=%s
                         AND handler_version='h4g17_stripe_initial_atomic_v1'
                         AND user_id=%s::uuid AND processing_status='processed'
                         AND (transaction_id IS NULL OR transaction_id=%s::uuid)
                         AND (subscription_id IS NULL OR subscription_id=%s::uuid)''',
                    (receipt_id, bound['subscription_row_id'], eid,
                     bound['user_id'], receipt_id, bound['subscription_row_id']))
        require(cur.rowcount == 1,
                'completed event receipt/subscription link not uniquely persisted')

    conn = connection()
    try:
        with conn.cursor() as cur:
            eid = proof['event_id']
            cur.execute('''SELECT user_id::text AS user_id, event_type,
                                  processing_status, livemode, payload_json,
                                  handler_version,
                                  subscription_id::text AS linked_subscription_id,
                                  transaction_id::text AS linked_transaction_id
                           FROM public.payment_events
                           WHERE provider='stripe' AND provider_event_id=%s
                           FOR UPDATE''', (eid,))
            recorded = cur.fetchone()
            require(isinstance(recorded, dict)
                    and recorded.get('event_type') == 'invoice.paid'
                    and recorded.get('livemode') is expected_livemode
                    and recorded.get('payload_json') == event,
                    'signed durable inbox missing or changed')
            if recorded.get('handler_version') != 'h4g17_stripe_initial_atomic_v1':
                conn.rollback()
                return {'status': 'legacy_event_hold'}
            if recorded.get('processing_status') in ('failed', 'ignored'):
                conn.rollback()
                return {'status': 'manual_review'}
            require(recorded.get('processing_status') in ('received', 'processed'),
                    'durable initial event state invalid')

            # The accepted binder locks the stored customer/user and Price catalog.
            # A missing local subscription is retriable, never inferred from metadata.
            bound = bind_initial_stripe_cash_proof_to_account_v1(
                event, proof, cursor=cur, expected_livemode=expected_livemode)
            require(isinstance(bound, dict)
                    and bound.get('event_id') == eid
                    and bound.get('invoice_id') == proof['invoice_id']
                    and bound.get('payment_intent_id') == proof['payment_intent_id']
                    and bound.get('invoice_payment_id') == proof['invoice_payment_id']
                    and bound.get('stripe_subscription_id') == proof['subscription_id']
                    and bound.get('stripe_price_id') == proof['price_id']
                    and bound.get('paid_cents') == proof['paid_cents']
                    and bound.get('period_start_unix') == proof['period_start_unix']
                    and bound.get('period_end_unix') == proof['period_end_unix']
                    and bound.get('support_mode') == proof['support_mode_from_period']
                    and bound.get('livemode') is expected_livemode
                    and isinstance(bound.get('user_id'), str)
                    and bool(bound['user_id'])
                    and str(recorded.get('user_id')) == bound['user_id'],
                    'signed inbox and authoritative cash binding differ')
            if bound.get('status') == 'awaiting_subscription_row':
                require(bound.get('subscription_row_id') is None,
                        'missing subscription reported with an existing row')
                conn.rollback()
                return {'status': 'awaiting_subscription_row'}
            require(bound.get('status') == 'bound'
                    and isinstance(bound.get('subscription_row_id'), str)
                    and bool(bound['subscription_row_id'])
                    and isinstance(bound.get('plan_code'), str)
                    and bool(bound['plan_code']),
                    'existing Local subscription binding missing')

            cur.execute('''SELECT plan_code, entitlement_status,
                                  subscription_expires_at, last_support_mode,
                                  is_banned
                           FROM public.users WHERE id=%s::uuid FOR UPDATE''',
                        (bound['user_id'],))
            account = cur.fetchone()
            require(isinstance(account, dict) and account.get('is_banned') is False,
                    'authenticated seeker record missing or banned')
            expiry = account.get('subscription_expires_at')
            require(expiry is None or (isinstance(expiry, datetime.datetime)
                    and expiry.tzinfo is not None and expiry.utcoffset() is not None),
                    'stored paid-through timestamp unverifiable')

            # Serialize the same PaymentIntent across DIFFERENT invoice IDs and
            # DIFFERENT users. The invoice uniqueness index alone cannot do this.
            for lock_key in (proof['payment_intent_id'], proof['invoice_payment_id']):
                cur.execute('''SELECT pg_advisory_xact_lock(
                                hashtextextended(%s::text, 0))''',
                            ('godinc:initial-stripe-cash:' +
                             ('live:' if expected_livemode else 'test:') +
                             lock_key,))
            cur.execute('''SELECT COALESCE(provider_invoice_id, stripe_invoice_id)
                                  AS invoice_id, id::text AS receipt_id
                           FROM public.billing_transactions
                           WHERE provider='stripe' AND livemode=%s
                             AND (provider_payment_intent_id=%s
                                  OR stripe_payment_intent_id=%s
                                  OR raw_summary_json->>'invoice_payment_id'=%s)
                           FOR UPDATE''',
                        (expected_livemode, proof['payment_intent_id'],
                         proof['payment_intent_id'], proof['invoice_payment_id']))
            payment_rows = cur.fetchall()
            require(isinstance(payment_rows, list) and len(payment_rows) <= 1
                    and all(isinstance(row, dict)
                            and row.get('invoice_id') == proof['invoice_id']
                            for row in payment_rows),
                    'settled cash payment reused by different invoices')

            cur.execute('''SELECT id::text AS receipt_id, user_id::text AS user_id,
                                  subscription_id::text AS subscription_id,
                                  provider_invoice_id, stripe_invoice_id,
                                  provider_payment_intent_id, stripe_payment_intent_id,
                                  stripe_event_id, livemode, status,
                                  gross_amount_cents, currency, plan_code,
                                  support_mode, transaction_kind
                           FROM public.billing_transactions
                           WHERE provider='stripe' AND livemode=%s
                             AND COALESCE(provider_invoice_id, stripe_invoice_id)=%s
                           FOR UPDATE''',
                        (expected_livemode, proof['invoice_id']))
            receipts = cur.fetchall()
            require(isinstance(receipts, list) and len(receipts) <= 1,
                    'duplicate Stripe invoice financial identities')
            receipt = receipts[0] if receipts else None
            cur.execute('''SELECT id::text AS receipt_id,
                                  COALESCE(provider_invoice_id, stripe_invoice_id)
                                      AS invoice_id
                           FROM public.billing_transactions
                           WHERE provider='stripe' AND livemode=%s
                             AND subscription_id=%s::uuid
                             AND transaction_kind IN ('monthly_initial','annual_initial')
                             AND status='succeeded'
                           FOR UPDATE''',
                        (expected_livemode, bound['subscription_row_id']))
            prior_initial = cur.fetchall()
            require(isinstance(prior_initial, list) and len(prior_initial) <= 1,
                    'multiple succeeded first payments for one subscription')
            if prior_initial and prior_initial[0].get('invoice_id') != proof['invoice_id']:
                conn.rollback()
                return {'status': 'second_initial_invoice_hold'}
            require(not prior_initial or (receipt is not None
                    and prior_initial[0].get('receipt_id') == receipt.get('receipt_id')),
                    'succeeded first-payment receipt identity missing')
            if receipt is not None:
                require(isinstance(receipt, dict)
                        and receipt.get('user_id') == bound['user_id']
                        and receipt.get('subscription_id') == bound['subscription_row_id']
                        and receipt.get('livemode') is expected_livemode
                        and receipt.get('plan_code') == bound['plan_code']
                        and receipt.get('support_mode') == bound['support_mode']
                        and receipt.get('transaction_kind') == expected_kind
                        and receipt.get('currency') == 'usd'
                        and receipt.get('gross_amount_cents') == proof['paid_cents']
                        and receipt.get('provider_invoice_id') == proof['invoice_id']
                        and receipt.get('stripe_invoice_id') == proof['invoice_id']
                        and receipt.get('provider_payment_intent_id') == proof['payment_intent_id']
                        and receipt.get('stripe_payment_intent_id') == proof['payment_intent_id']
                        and receipt.get('status') == 'succeeded'
                        and recorded.get('linked_transaction_id') in
                            (None, receipt.get('receipt_id'))
                        and recorded.get('linked_subscription_id') in
                            (None, bound['subscription_row_id']),
                        'existing Stripe receipt cash, owner or event link conflicts')
                require(payment_rows and
                        payment_rows[0]['receipt_id'] == receipt['receipt_id'],
                        'existing receipt not matched by cash identity')
                require(expiry is not None and expiry >= end,
                        'existing cash receipt lacks recorded paid-through; hold')
                if recorded['processing_status'] == 'processed':
                    conn.rollback()
                    return {'status': 'duplicate_event'}
                guard_legacy_completion_scope(cur, eid)
                writers['processed'](
                    eid, 'initial_cash_atomic_v1_duplicate_invoice', cursor=cur)
                attach_event(cur, eid, recorded, receipt['receipt_id'], bound)
                conn.commit()
                return {'status': 'duplicate_invoice'}
            require(not payment_rows, 'PaymentIntent already recorded without invoice receipt')
            require(recorded['processing_status'] == 'received',
                    'processed initial event lacks succeeded financial receipt')
            if expiry is not None and expiry >= end:
                conn.rollback()
                return {'status': 'existing_paid_through_hold'}
            if end <= datetime.datetime.now(datetime.timezone.utc):
                conn.rollback()
                return {'status': 'expired_service_period_hold'}

            writers['receipt'](
                event_id=eid, invoice_obj=event['data']['object'],
                user_id=bound['user_id'],
                subscription_row_id=bound['subscription_row_id'],
                plan_code=bound['plan_code'], support_mode=bound['support_mode'],
                transaction_kind=expected_kind, status='succeeded',
                verified_cash_bundle=cash_bundle, cursor=cur)
            writers['renewal'](
                bound['user_id'], bound['plan_code'],
                period_start=start, period_end=end,
                support_mode=bound['support_mode'], cursor=cur)
            writers['fallback'](bound['user_id'], cursor=cur)
            guard_legacy_completion_scope(cur, eid)
            writers['processed'](
                eid, 'initial_cash_atomic_v1:' + bound['support_mode'], cursor=cur)
            # Re-read the shared transaction BEFORE committing. A mocked or
            # miswired writer cannot silently acknowledge a partial settlement.
            cur.execute('''SELECT id::text AS receipt_id, status, user_id::text AS user_id,
                                  subscription_id::text AS subscription_id,
                                  transaction_kind, gross_amount_cents,
                                  provider_payment_intent_id, provider_invoice_id
                           FROM public.billing_transactions
                           WHERE provider='stripe' AND livemode=%s
                             AND COALESCE(provider_invoice_id, stripe_invoice_id)=%s
                           FOR UPDATE''',
                        (expected_livemode, proof['invoice_id']))
            persisted = cur.fetchall()
            require(isinstance(persisted, list) and len(persisted) == 1
                    and persisted[0].get('status') == 'succeeded'
                    and persisted[0].get('user_id') == bound['user_id']
                    and persisted[0].get('subscription_id') == bound['subscription_row_id']
                    and persisted[0].get('transaction_kind') == expected_kind
                    and persisted[0].get('gross_amount_cents') == proof['paid_cents']
                    and persisted[0].get('provider_payment_intent_id') == proof['payment_intent_id']
                    and persisted[0].get('provider_invoice_id') == proof['invoice_id'],
                    'financial receipt absent after shared-cursor write')
            cur.execute('''SELECT plan_code, entitlement_status,
                                  subscription_expires_at, last_support_mode,
                                  last_paid_plan_code
                           FROM public.users WHERE id=%s::uuid''',
                        (bound['user_id'],))
            updated_user = cur.fetchone()
            require(isinstance(updated_user, dict)
                    and updated_user.get('plan_code') == bound['plan_code']
                    and updated_user.get('entitlement_status') == 'active'
                    and updated_user.get('last_support_mode') == bound['support_mode']
                    and updated_user.get('last_paid_plan_code') == bound['plan_code']
                    and isinstance(updated_user.get('subscription_expires_at'),
                                   datetime.datetime)
                    and updated_user['subscription_expires_at'] >= end,
                    'paid entitlement not recorded by shared-cursor writer')
            attach_event(cur, eid, recorded, persisted[0]['receipt_id'], bound)
            cur.execute('''SELECT processing_status, helper_name,
                                  transaction_id::text AS linked_transaction_id,
                                  subscription_id::text AS linked_subscription_id
                           FROM public.payment_events
                           WHERE provider='stripe' AND provider_event_id=%s''', (eid,))
            completed = cur.fetchone()
            require(isinstance(completed, dict)
                    and completed.get('processing_status') == 'processed'
                    and completed.get('helper_name') ==
                    'initial_cash_atomic_v1:' + bound['support_mode']
                    and completed.get('linked_transaction_id') == persisted[0]['receipt_id']
                    and completed.get('linked_subscription_id') == bound['subscription_row_id'],
                    'durable signed event not completed in shared transaction')
        conn.commit()
        return {'status': 'processed'}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def orchestrate_verified_initial_stripe_cash_v1(event, *, expected_livemode,
                                 classify_reup, durable_receive,
                                 read_authenticated_cash, atomic_settle):
    """DORMANT: orchestrate a previously signature-verified initial Stripe invoice.

    The signed webhook must isolate early re-ups before invoking this function.
    Inject only trusted inbox, provider-reader and atomic-owner callbacks.
    This function never verifies signatures, calls providers or opens a DB.
    Do not route until legacy grants and durable retry are corrected.
    """
    def require(ok, reason):
        if not ok:
            raise ValueError('Initial cash routing: ' + reason)

    require(type(expected_livemode) is bool,
            'configured Stripe mode missing')
    require(isinstance(event, dict)
            and event.get('type') == 'invoice.paid'
            and event.get('livemode') is expected_livemode,
            'signed initial invoice environment/type mismatch')

    obj = event.get('data')
    obj = obj.get('object') if isinstance(obj, dict) else None
    require(isinstance(obj, dict)
            and obj.get('object') == 'invoice'
            and obj.get('billing_reason') == 'subscription_create'
            and obj.get('livemode') is expected_livemode,
            'not a supported initial Stripe invoice')

    require(all(callable(x) for x in
            (classify_reup, durable_receive,
             read_authenticated_cash, atomic_settle)),
            'trusted route dependency missing')

    require(classify_reup(event) is None,
            'explicit re-up must remain separate')

    inbox = durable_receive(event)
    require(isinstance(inbox, dict),
            'durable inbox returned malformed state')

    status = inbox.get('status')
    if status in ('legacy_hold', 'manual_review'):
        return {'status': status, 'held_for_review': True}

    require(status in ('received', 'already_received',
                       'already_processed')
            and isinstance(inbox.get('user_id'), str)
            and bool(inbox['user_id']),
            'durable received state/owner missing')

    # Independent reads occur AFTER durable receipt.
    cash = read_authenticated_cash(event)
    require(isinstance(cash, dict)
            and isinstance(cash.get('proof'), dict),
            'authenticated cash evidence incomplete')

    settlement = atomic_settle(event, cash)
    require(isinstance(settlement, dict),
            'atomic owner returned malformed state')

    outcome = settlement.get('status')

    if outcome == 'awaiting_subscription_row':
        # Until a separate retry worker is installed, propagate
        # a retryable error rather than silently acknowledging
        # unfulfilled payment. The received row remains durable.
        raise RuntimeError(
            'Initial cash routing: durable retry required for missing subscription'
        )

    if outcome in (
        'processed', 'duplicate_event', 'duplicate_invoice'
    ):
        return {
            'status': outcome,
            'duplicate': outcome != 'processed',
        }

    if outcome in (
        'legacy_event_hold', 'manual_review',
        'second_initial_invoice_hold',
        'existing_paid_through_hold',
        'expired_service_period_hold'
    ):
        return {'status': outcome, 'held_for_review': True}

    raise ValueError(
        'Initial cash routing: unexpected atomic owner status'
    )


def inspect_stripe_initial_recovery_cases_v1(
    *, connection_factory, expected_database, expected_livemode,
    older_than_seconds, max_cases=50
):
    """Read-only, manually invoked current-handler Stripe case inventory.

    No provider access, scheduling, economic writes or automatic recovery.
    Returns only internal case UUID, classification and age.
    """
    if not callable(connection_factory):
        raise ValueError("R3 monitor: connection factory required")
    if not isinstance(expected_database, str) or not expected_database:
        raise ValueError("R3 monitor: database identity required")
    if type(expected_livemode) is not bool:
        raise ValueError("R3 monitor: explicit livemode required")
    if type(older_than_seconds) is not int or not 1 <= older_than_seconds <= 86400:
        raise ValueError("R3 monitor: invalid age threshold")
    if type(max_cases) is not int or not 1 <= max_cases <= 100:
        raise ValueError("R3 monitor: invalid case limit")

    conn = connection_factory()
    try:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '5000ms'")
            cur.execute(
                "SELECT current_database() AS db, "
                "current_setting('transaction_read_only') AS ro"
            )
            identity = cur.fetchone()
            if (not isinstance(identity, dict)
                    or identity.get("db") != expected_database
                    or identity.get("ro") != "on"):
                raise RuntimeError("R3 monitor: database safety guard failed")

            cur.execute("""
                SELECT pe.id::text AS case_id,
                       CASE
                         WHEN pe.processing_status = 'received'
                           THEN 'received_overdue'
                         WHEN pe.processing_status IN ('failed', 'ignored')
                           THEN 'manual_review'
                         ELSE 'processed_link_mismatch'
                       END AS classification,
                       GREATEST(0, COALESCE(
                           EXTRACT(EPOCH FROM
                             (CURRENT_TIMESTAMP - pe.received_at))::bigint,
                           0
                       )) AS age_seconds
                FROM public.payment_events pe
                LEFT JOIN public.billing_transactions bt
                  ON bt.id = pe.transaction_id
                LEFT JOIN public.subscriptions s
                  ON s.id = pe.subscription_id
                WHERE pe.provider = 'stripe'
                  AND pe.event_type = 'invoice.paid'
                  AND pe.handler_version = 'h4g17_stripe_initial_atomic_v1'
                  AND pe.livemode = %s
                  AND (
                    (pe.processing_status = 'received'
                     AND pe.received_at <
                         CURRENT_TIMESTAMP - (%s * INTERVAL '1 second'))
                    OR pe.processing_status IN ('failed', 'ignored')
                    OR (
                      pe.processing_status = 'processed'
                      AND (
                        pe.transaction_id IS NULL
                        OR pe.subscription_id IS NULL
                        OR bt.id IS NULL
                        OR s.id IS NULL
                        OR bt.user_id IS DISTINCT FROM pe.user_id
                        OR s.user_id IS DISTINCT FROM pe.user_id
                        OR bt.subscription_id IS DISTINCT FROM s.id
                        OR bt.status IS DISTINCT FROM 'succeeded'
                      )
                    )
                  )
                ORDER BY pe.received_at ASC NULLS FIRST
                LIMIT %s
            """, (expected_livemode, older_than_seconds, max_cases))
            rows = cur.fetchall()

        if not isinstance(rows, list):
            raise RuntimeError("R3 monitor: invalid database result")

        result = []
        allowed = {
            "received_overdue",
            "manual_review",
            "processed_link_mismatch",
        }
        import uuid
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError("R3 monitor: malformed case")
            case_id = str(uuid.UUID(str(row.get("case_id"))))
            classification = row.get("classification")
            age = row.get("age_seconds")
            if classification not in allowed or type(age) is not int or age < 0:
                raise RuntimeError("R3 monitor: invalid classification")
            result.append({
                "case_id": case_id,
                "classification": classification,
                "age_seconds": age,
            })
        return result
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()


def record_stripe_initial_recovery_attempt_audit_v1(
    *, connection_factory, expected_database, expected_livemode,
    expected_local_socket, actor_user_id, case_id, min_age_seconds=900
):
    """Strict audit BEFORE a manually authorized initial-payment recovery.

    Must only be invoked by a trusted, authenticated administrative caller.
    This does not authenticate the caller, verify a webhook signature,
    contact Stripe, recover funds, or grant entitlement.
    """
    import datetime
    import json
    import uuid

    if not callable(connection_factory):
        raise ValueError("R3 audit: connection factory required")
    if not isinstance(expected_database, str) or not expected_database:
        raise ValueError("R3 audit: database identity required")
    if type(expected_livemode) is not bool:
        raise ValueError("R3 audit: explicit Stripe environment required")
    if type(expected_local_socket) is not bool:
        raise ValueError("R3 audit: explicit network boundary required")
    if type(min_age_seconds) is not int or not 1 <= min_age_seconds <= 86400:
        raise ValueError("R3 audit: invalid minimum case age")

    try:
        actor = str(uuid.UUID(actor_user_id))
        case = str(uuid.UUID(case_id))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("R3 audit: invalid UUID identity") from None

    conn = connection_factory()
    try:
        conn.set_session(readonly=False, autocommit=False)
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '5000ms'")
            cur.execute("SET LOCAL lock_timeout = '2000ms'")
            cur.execute("""
                SELECT current_database() AS db,
                       inet_server_addr() IS NULL AS local_socket,
                       current_setting('transaction_read_only') AS ro
            """)
            identity = cur.fetchone()
            if (
                not isinstance(identity, dict)
                or identity.get("db") != expected_database
                or identity.get("local_socket") is not expected_local_socket
                or identity.get("ro") != "off"
            ):
                raise RuntimeError("R3 audit: database boundary mismatch")

            cur.execute("""
                SELECT lower(role::text) AS role
                FROM public.users
                WHERE id=%s::uuid
                FOR SHARE
            """, (actor,))
            authorized = cur.fetchone()
            if (
                not isinstance(authorized, dict)
                or authorized.get("role") not in ("admin", "owner")
            ):
                raise PermissionError("R3 audit: administrator not verified")

            cur.execute("""
                SELECT id::text AS case_id, user_id::text AS user_id,
                       provider, provider_event_id, stripe_event_id,
                       event_type, handler_version, processing_status,
                       livemode, payload_json, received_at,
                       transaction_id, subscription_id
                FROM public.payment_events
                WHERE id=%s::uuid
                FOR UPDATE
            """, (case,))
            event = cur.fetchone()

            if not isinstance(event, dict):
                raise ValueError("R3 audit: case absent")

            payload = event.get("payload_json")
            obj = (
                payload.get("data", {}).get("object")
                if isinstance(payload, dict)
                and isinstance(payload.get("data"), dict)
                else None
            )

            if not (
                event.get("case_id") == case
                and isinstance(event.get("user_id"), str)
                and event.get("provider") == "stripe"
                and event.get("event_type") == "invoice.paid"
                and event.get("handler_version")
                    == "h4g17_stripe_initial_atomic_v1"
                and event.get("processing_status") == "received"
                and event.get("livemode") is expected_livemode
                and event.get("transaction_id") is None
                and event.get("subscription_id") is None
                and isinstance(payload, dict)
                and payload.get("type") == "invoice.paid"
                and payload.get("livemode") is expected_livemode
                and payload.get("id") == event.get("provider_event_id")
                and event.get("stripe_event_id") == event.get("provider_event_id")
                and isinstance(obj, dict)
                and obj.get("object") == "invoice"
                and obj.get("livemode") is expected_livemode
                and obj.get("billing_reason") == "subscription_create"
            ):
                raise ValueError("R3 audit: case provenance or state mismatch")

            received = event.get("received_at")
            if (
                not isinstance(received, datetime.datetime)
                or received.tzinfo is None
                or received.utcoffset() is None
                or (
                    datetime.datetime.now(datetime.timezone.utc) - received
                ).total_seconds() < min_age_seconds
            ):
                raise ValueError("R3 audit: case not overdue or timestamp invalid")

            audit_id = str(uuid.uuid4())
            audit_type = "stripe_initial_recovery_attempt_v1"
            audit_payload = {
                "schema": "stripe_initial_recovery_attempt.v1",
                "case_id": case,
                "livemode": expected_livemode,
                "handler_version": "h4g17_stripe_initial_atomic_v1",
            }
            cur.execute("""
                INSERT INTO public.admin_action_logs (
                    id, admin_user_id, target_user_id,
                    action_type, action_payload
                )
                VALUES (%s, %s::uuid, %s::uuid, %s, %s::jsonb)
                RETURNING id,
                          admin_user_id::text AS actor_id,
                          target_user_id::text AS target_id,
                          action_type, action_payload
            """, (
                audit_id, actor, event["user_id"], audit_type,
                json.dumps(audit_payload, sort_keys=True),
            ))
            recorded = cur.fetchone()
            if not (
                isinstance(recorded, dict)
                and recorded.get("id") == audit_id
                and recorded.get("actor_id") == actor
                and recorded.get("target_id") == event["user_id"]
                and recorded.get("action_type") == audit_type
                and recorded.get("action_payload") == audit_payload
            ):
                raise RuntimeError("R3 audit: committed record not verified")

        conn.commit()
        return {
            "status": "attempt_recorded",
            "audit_id": audit_id,
            "case_id": case,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def recover_stripe_initial_one_case_v1(
    *, connection_factory, checked_audit, verified_initial_dispatch,
    expected_database, expected_livemode, expected_local_socket,
    actor_user_id, case_id, min_age_seconds=900
):
    """Dormant, one-case recovery of a stored signature-verified initial invoice.

    A trusted authenticated operator must supply the existing checked audit
    and initial cash dispatcher. The dispatcher alone obtains independent
    Stripe payment evidence and invokes the existing atomic financial writer.

    Never expose this callable to arbitrary client-supplied event payloads.
    No automatic scheduling, legacy replay, or unverified cash settlement.
    """
    import datetime
    import hashlib
    import json
    import uuid

    if not all(callable(x) for x in (
        connection_factory, checked_audit, verified_initial_dispatch
    )):
        raise ValueError("R3 recovery: trusted dependencies required")
    if not isinstance(expected_database, str) or not expected_database:
        raise ValueError("R3 recovery: explicit database identity required")
    if type(expected_livemode) is not bool:
        raise ValueError("R3 recovery: explicit Stripe environment required")
    if type(expected_local_socket) is not bool:
        raise ValueError("R3 recovery: explicit socket boundary required")
    if type(min_age_seconds) is not int or not 1 <= min_age_seconds <= 86400:
        raise ValueError("R3 recovery: invalid age threshold")

    try:
        actor = str(uuid.UUID(actor_user_id))
        case = str(uuid.UUID(case_id))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("R3 recovery: invalid operator or case UUID") from None

    def frozen(value):
        return hashlib.sha256(json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")).hexdigest()

    def validate_event(row):
        if not isinstance(row, dict):
            raise ValueError("R3 recovery: case missing")
        payload = row.get("payload_json")
        obj = (
            payload.get("data", {}).get("object")
            if isinstance(payload, dict)
            and isinstance(payload.get("data"), dict)
            else None
        )
        eid = row.get("provider_event_id")
        if not (
            row.get("case_id") == case
            and isinstance(row.get("user_id"), str)
            and row.get("provider") == "stripe"
            and row.get("event_type") == "invoice.paid"
            and row.get("handler_version")
                == "h4g17_stripe_initial_atomic_v1"
            and row.get("processing_status") == "received"
            and row.get("livemode") is expected_livemode
            and row.get("transaction_id") is None
            and row.get("subscription_id") is None
            and isinstance(eid, str) and eid.startswith("evt_")
            and row.get("stripe_event_id") == eid
            and isinstance(payload, dict)
            and payload.get("id") == eid
            and payload.get("type") == "invoice.paid"
            and payload.get("livemode") is expected_livemode
            and type(payload.get("created")) is int
            and payload["created"] > 0
            and isinstance(obj, dict)
            and obj.get("object") == "invoice"
            and isinstance(obj.get("id"), str)
            and obj["id"].startswith("in_")
            and obj.get("billing_reason") == "subscription_create"
            and obj.get("livemode") is expected_livemode
        ):
            raise ValueError("R3 recovery: event provenance/state mismatch")

        received = row.get("received_at")
        if (
            not isinstance(received, datetime.datetime)
            or received.tzinfo is None
            or received.utcoffset() is None
            or (
                datetime.datetime.now(datetime.timezone.utc) - received
            ).total_seconds() < min_age_seconds
        ):
            raise ValueError("R3 recovery: case is not overdue")
        return payload

    event_sql = """
        SELECT id::text AS case_id,
               user_id::text AS user_id,
               provider, provider_event_id, stripe_event_id,
               event_type, handler_version, processing_status,
               livemode, payload_json, received_at,
               transaction_id::text AS transaction_id,
               subscription_id::text AS subscription_id
        FROM public.payment_events
        WHERE id=%s::uuid
    """

    conn = connection_factory()
    try:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '5000ms'")
            cur.execute("""
                SELECT current_database() AS db,
                       inet_server_addr() IS NULL AS local_socket,
                       current_setting('transaction_read_only') AS ro
            """)
            identity = cur.fetchone()
            if not (
                isinstance(identity, dict)
                and identity.get("db") == expected_database
                and identity.get("local_socket") is expected_local_socket
                and identity.get("ro") == "on"
            ):
                raise RuntimeError("R3 recovery: database boundary mismatch")

            # Session lock spans audit and provider settlement. Closing this
            # connection releases it even when either operation raises.
            lock_key = (
                "godinc:r3-initial:"
                + ("live:" if expected_livemode else "test:")
                + case
            )
            cur.execute("""
                SELECT pg_try_advisory_lock(
                    hashtextextended(%s::text, 0)
                ) AS locked
            """, (lock_key,))
            locked = cur.fetchone()
            if not isinstance(locked, dict) or locked.get("locked") is not True:
                raise RuntimeError("R3 recovery: case already in progress")

            cur.execute(event_sql, (case,))
            initial = cur.fetchone()
            signed_event = validate_event(initial)
            initial_hash = frozen(signed_event)
            target_user = initial["user_id"]

        conn.rollback()

        # This existing checked-audit function commits independently and
        # propagates failures. No Stripe calls precede successful audit.
        audit = checked_audit(
            connection_factory=connection_factory,
            expected_database=expected_database,
            expected_livemode=expected_livemode,
            expected_local_socket=expected_local_socket,
            actor_user_id=actor,
            case_id=case,
            min_age_seconds=min_age_seconds,
        )
        if not (
            isinstance(audit, dict)
            and audit.get("status") == "attempt_recorded"
            and audit.get("case_id") == case
        ):
            raise RuntimeError("R3 recovery: checked audit not confirmed")

        try:
            audit_id = str(uuid.UUID(audit.get("audit_id")))
        except (ValueError, TypeError, AttributeError):
            raise RuntimeError("R3 recovery: invalid audit identity") from None

        # Recheck the immutable saved event and independently committed audit
        # after the audit transaction, before any provider request.
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '5000ms'")
            cur.execute(event_sql, (case,))
            current = cur.fetchone()
            payload = validate_event(current)
            if (
                current["user_id"] != target_user
                or frozen(payload) != initial_hash
            ):
                raise RuntimeError("R3 recovery: event changed after audit")

            cur.execute("""
                SELECT COUNT(*) AS n
                FROM public.admin_action_logs
                WHERE id=%s
                  AND admin_user_id=%s::uuid
                  AND target_user_id=%s::uuid
                  AND action_type='stripe_initial_recovery_attempt_v1'
                  AND action_payload @> %s::jsonb
            """, (
                audit_id, actor, target_user,
                json.dumps({
                    "schema": "stripe_initial_recovery_attempt.v1",
                    "case_id": case,
                    "livemode": expected_livemode,
                    "handler_version": "h4g17_stripe_initial_atomic_v1",
                }, sort_keys=True),
            ))
            recorded = cur.fetchone()
            if (
                not isinstance(recorded, dict)
                or recorded.get("n") != 1
            ):
                raise RuntimeError("R3 recovery: durable audit not found")

        conn.rollback()

        # Only the trusted existing adapter may retrieve Stripe cash proof
        # and call the previously accepted locked atomic writer.
        outcome = verified_initial_dispatch(signed_event)
        accepted = {
            "processed",
            "duplicate_event",
            "duplicate_invoice",
            "legacy_event_hold",
            "manual_review",
            "second_initial_invoice_hold",
            "existing_paid_through_hold",
            "expired_service_period_hold",
        }
        if not (
            isinstance(outcome, dict)
            and outcome.get("ok") is True
            and outcome.get("initial_status") in accepted
        ):
            raise RuntimeError("R3 recovery: unverified settlement result")

        status = outcome["initial_status"]
        if status == "processed":
            category = "settled"
        elif status in ("duplicate_event", "duplicate_invoice"):
            category = "already_settled"
        else:
            category = "held_for_review"

        return {
            "status": category,
            "atomic_status": status,
            "audit_id": audit_id,
            "case_id": case,
        }
    finally:
        try:
            conn.rollback()
        finally:
            # Releases PostgreSQL session advisory lock on all paths.
            conn.close()
