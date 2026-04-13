#!/usr/bin/env python3
import os
import sys

import psycopg2
import stripe
from psycopg2.extras import RealDictCursor

CATALOG = [
    ("seeker", "Seeker", 199, 1999),
    ("magister", "Magister", 499, 4999),
    ("sovereign", "Sovereign", 999, 9999),
    ("philosophus", "Philosophus", 1999, 14999),
    ("theoricus", "Theoricus", 3300, 19999),
]


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def get_db_connection():
    db_url = require_env("DATABASE_URL")
    if db_url.startswith("postgresql+psycopg2://"):
        db_url = db_url.replace("postgresql+psycopg2://", "postgresql://", 1)
    return psycopg2.connect(db_url, cursor_factory=RealDictCursor)


def get_stripe_livemode() -> bool:
    return os.getenv("STRIPE_LIVEMODE", "false").lower() == "true"


def init_stripe() -> None:
    secret_key = require_env("STRIPE_SECRET_KEY")
    if not secret_key.startswith("sk_test_"):
        raise SystemExit("Refusing to run with a non-test Stripe secret key.")
    if get_stripe_livemode():
        raise SystemExit("Refusing to seed livemode=true catalog from this script.")
    stripe.api_key = secret_key


def verify_plan_catalog():
    expected = [row[0] for row in CATALOG]
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT plan_code
                FROM plan_catalog
                WHERE plan_code = ANY(%s)
                """,
                (expected,),
            )
            found = {row["plan_code"] for row in cur.fetchall()}
    finally:
        conn.close()

    missing = [code for code in expected if code not in found]
    if missing:
        raise SystemExit(f"Missing plan_catalog rows for: {', '.join(missing)}")


def create_catalog_rows():
    rows = []
    print("Creating fresh Stripe test catalog...")

    for plan_code, display_name, monthly_cents, annual_cents in CATALOG:
        product = stripe.Product.create(
            name=display_name,
            metadata={
                "plan_code": plan_code,
                "source": "stripe_catalog_seed_v2",
            },
        )
        product_id = product["id"]
        print(f"[product] {plan_code:<12} CREATED {product_id}")

        monthly_price = stripe.Price.create(
            product=product_id,
            currency="usd",
            unit_amount=monthly_cents,
            recurring={"interval": "month"},
            metadata={
                "plan_code": plan_code,
                "support_mode": "monthly_recurring",
                "source": "stripe_catalog_seed_v2",
            },
        )
        monthly_price_id = monthly_price["id"]
        print(f"[price]   {plan_code:<12} monthly_recurring CREATED {monthly_price_id}  ${monthly_cents / 100:.2f}")

        annual_price = stripe.Price.create(
            product=product_id,
            currency="usd",
            unit_amount=annual_cents,
            recurring={"interval": "year"},
            metadata={
                "plan_code": plan_code,
                "support_mode": "annual_recurring",
                "source": "stripe_catalog_seed_v2",
            },
        )
        annual_price_id = annual_price["id"]
        print(f"[price]   {plan_code:<12} annual_recurring  CREATED {annual_price_id}  ${annual_cents / 100:.2f}")

        rows.append((plan_code, "monthly_recurring", product_id, monthly_price_id))
        rows.append((plan_code, "annual_recurring", product_id, annual_price_id))

    return rows


def rebuild_price_map(rows):
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM stripe_price_map WHERE livemode = FALSE")
                for plan_code, support_mode, product_id, price_id in rows:
                    cur.execute(
                        """
                        INSERT INTO stripe_price_map (
                            plan_code,
                            support_mode,
                            livemode,
                            stripe_product_id,
                            stripe_price_id,
                            active
                        )
                        VALUES (%s, %s, FALSE, %s, %s, TRUE)
                        """,
                        (plan_code, support_mode, product_id, price_id),
                    )
    finally:
        conn.close()


def main():
    init_stripe()
    verify_plan_catalog()
    rows = create_catalog_rows()

    print("\nRebuilding staging stripe_price_map...")
    rebuild_price_map(rows)

    print("\nDone. Final rows:")
    for plan_code, support_mode, product_id, price_id in rows:
        print(f"  {plan_code:<12} {support_mode:<17} {product_id} {price_id}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
