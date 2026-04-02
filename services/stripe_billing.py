import os
from typing import Optional

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
        raise ValueError("Invalid support_mode. Use 'monthly_recurring', 'annual_prepaid', or 'annual_recurring'.")

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
                    f"No active Stripe price mapping found for plan_code={normalized_plan}, support_mode={normalized_mode}, livemode={get_stripe_livemode()}."
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
            return cur.fetchone()
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
                    stripe_customer_id,
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
                    %s::jsonb,
                    %s
                )
                ON CONFLICT (provider, user_id)
                DO UPDATE SET
                    stripe_customer_id = EXCLUDED.stripe_customer_id,
                    email_at_create = COALESCE(billing_customers.email_at_create, EXCLUDED.email_at_create),
                    default_payment_method_id = COALESCE(EXCLUDED.default_payment_method_id, billing_customers.default_payment_method_id),
                    livemode = EXCLUDED.livemode,
                    updated_at = NOW()
                RETURNING *
                """,
                (
                    user_id,
                    stripe_customer_id,
                    email_at_create,
                    default_payment_method_id,
                    '{"source":"phase8_checkout"}',
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
        "stripe_customer_id": billing_customer["stripe_customer_id"],
        "stripe_price_id": plan["stripe_price_id"],
        "stripe_product_id": plan["stripe_product_id"],
        "plan_code": plan["plan_code"],
        "support_mode": plan["support_mode"],
        "livemode": plan["livemode"],
    }
