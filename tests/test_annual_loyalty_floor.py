import ast
import datetime
import unittest
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "main.py").read_text(encoding="utf-8")
FUNCTIONS = {
    "normalize_plan_code",
    "plan_rank",
    "max_plan_code",
    "compute_scroll_floor_plan",
    "compute_donor_floor_plan",
    "compute_annual_loyalty_floor_plan",
    "compute_fallback_floor_plan",
    "get_user_entitlement_snapshot",
}


def load_contracts():
    tree = ast.parse(SOURCE)
    selected = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in FUNCTIONS
    ]
    module = ast.Module(body=selected, type_ignores=[])
    namespace = {
        "Optional": Optional,
        "datetime": datetime,
        "timezone": datetime.timezone,
        "PLAN_LIMITS": {
            "anon": 0,
            "pilgrim": 9,
            "seeker": 33,
            "magister": 99,
            "sovereign": 999,
            "philosophus": 9999,
            "theoricus": None,
        },
        "PLAN_RANKS": {
            "anon": 0,
            "pilgrim": 1,
            "seeker": 3,
            "magister": 5,
            "sovereign": 7,
            "philosophus": 9,
            "theoricus": 10,
        },
    }
    exec(compile(module, str(ROOT / "main.py"), "exec"), namespace)
    return namespace


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.sql = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return dict(self.row)


class FakeConnection:
    def __init__(self, row):
        self.cur = FakeCursor(row)
        self.closed = False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


class AnnualLoyaltyFloorTests(unittest.TestCase):
    def setUp(self):
        self.contracts = load_contracts()
        self.clock = datetime.datetime.now(datetime.timezone.utc)

    def row(self, **changes):
        end = self.clock - datetime.timedelta(days=1)
        row = {
            "raw_plan_code": "magister",
            "entitlement_status": "active",
            "subscription_started_at": end - datetime.timedelta(days=365),
            "current_period_started_at": end - datetime.timedelta(days=365),
            "subscription_renews_at": end,
            "subscription_expires_at": end,
            "cancel_at_period_end": False,
            "highest_paid_plan_ever": None,
            "last_paid_plan_code": "magister",
            "donor_floor_plan_code": None,
            "scroll_floor_plan_code": None,
            "fallback_floor_plan_code": None,
            "renewal_offer_plan_code": "magister",
            "last_support_mode": "annual_recurring",
            "last_support_ended_at": None,
            "verified_paid_annual_term": True,
        }
        row.update(changes)
        return row

    def snapshot(self, row):
        conn = FakeConnection(row)
        self.contracts["get_db_connection"] = lambda: conn
        result = self.contracts["get_user_entitlement_snapshot"]("synthetic-user")
        self.assertTrue(conn.closed)
        return result, conn.cur

    def test_qualifying_completed_annual_high_tiers_receive_seeker(self):
        for plan in ("magister", "sovereign", "philosophus", "theoricus"):
            with self.subTest(plan=plan):
                result, _ = self.snapshot(self.row(
                    raw_plan_code=plan,
                    last_paid_plan_code=plan,
                    renewal_offer_plan_code=plan,
                ))
                self.assertEqual(result["entitlement_status"], "expired")
                self.assertEqual(result["effective_plan_code"], "seeker")

    def test_unverified_or_unpaid_annual_term_does_not_earn_loyalty(self):
        result, cursor = self.snapshot(self.row(verified_paid_annual_term=False))
        self.assertEqual(result["effective_plan_code"], "pilgrim")
        proof_sql = " ".join(cursor.sql.split())
        for required in (
            "bt.status = 'succeeded'",
            "bt.gross_amount_cents > 0",
            "bt.support_mode = 'annual_recurring'",
            "bt.transaction_kind IN ('annual_initial', 'annual_renewal')",
            "pe.processing_status = 'processed'",
            "h4g17_stripe_initial_atomic_v1",
            "h4g17_stripe_invoice_atomic_v1",
            "phase8_webhook_v2",
            "s.current_period_start = u.current_period_started_at",
            "s.current_period_end = u.subscription_expires_at",
        ):
            self.assertIn(required, proof_sql)

    def test_monthly_expiration_does_not_earn_annual_loyalty(self):
        end = self.clock - datetime.timedelta(days=1)
        result, _ = self.snapshot(self.row(
            current_period_started_at=end - datetime.timedelta(days=30),
            last_support_mode="monthly_recurring",
            verified_paid_annual_term=True,
        ))
        self.assertEqual(result["effective_plan_code"], "pilgrim")

    def test_seeker_annual_expiration_returns_to_free_floor(self):
        result, _ = self.snapshot(self.row(
            raw_plan_code="seeker",
            last_paid_plan_code="seeker",
            renewal_offer_plan_code="seeker",
        ))
        self.assertEqual(result["effective_plan_code"], "pilgrim")

    def test_paid_annual_renewal_remains_active_without_loyalty_downgrade(self):
        start = self.clock - datetime.timedelta(days=1)
        end = start + datetime.timedelta(days=365)
        result, _ = self.snapshot(self.row(
            current_period_started_at=start,
            subscription_renews_at=end,
            subscription_expires_at=end,
        ))
        self.assertEqual(result["entitlement_status"], "active")
        self.assertEqual(result["effective_plan_code"], "magister")

    def test_legacy_donor_and_grandfathered_fallbacks_are_preserved(self):
        donor, _ = self.snapshot(self.row(
            last_support_mode="monthly_recurring",
            verified_paid_annual_term=False,
            highest_paid_plan_ever="sovereign",
        ))
        self.assertEqual(donor["donor_floor_plan_code"], "seeker")
        self.assertEqual(donor["effective_plan_code"], "seeker")

        grandfathered, _ = self.snapshot(self.row(
            last_support_mode="monthly_recurring",
            verified_paid_annual_term=False,
            fallback_floor_plan_code="magister",
        ))
        self.assertEqual(grandfathered["effective_plan_code"], "magister")


if __name__ == "__main__":
    unittest.main()
