import ast
import datetime
import pathlib
import unittest
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "services/stripe_billing.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
nodes = [
    node for node in tree.body
    if isinstance(node, ast.FunctionDef)
    and node.name == "recover_stripe_initial_one_case_v1"
]
if len(nodes) != 1:
    raise RuntimeError("R3 one-case recovery not uniquely installed")
namespace = {}
exec(compile(
    ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"
), namespace)
recover = namespace["recover_stripe_initial_one_case_v1"]

class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.sql = ""
        self.calls = []
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False
    def execute(self, statement, params=None):
        if statement.lstrip().upper().startswith((
            "INSERT", "UPDATE", "DELETE", "CREATE", "DROP"
        )):
            raise AssertionError("Recovery reader attempted a database write")
        self.sql = statement
        self.calls.append(statement)
    def fetchone(self):
        c = self.conn
        if "current_database()" in self.sql:
            return {
                "db": c.database,
                "local_socket": True,
                "ro": "on",
            }
        if "pg_try_advisory_lock" in self.sql:
            return {"locked": c.lock_available}
        if "FROM public.payment_events" in self.sql:
            c.event_reads += 1
            row = dict(c.event)
            if c.event_reads == 2 and c.change_after_audit:
                row["processing_status"] = "processed"
            return row
        if "FROM public.admin_action_logs" in self.sql:
            return {"n": 1 if c.audit_present else 0}
        raise AssertionError("Unexpected query")

class FakeConnection:
    def __init__(self):
        self.database = "godinc_dev"
        self.case = str(uuid.uuid4())
        self.actor = str(uuid.uuid4())
        self.target = str(uuid.uuid4())
        self.event_id = "evt_r3_manual_" + uuid.uuid4().hex
        self.lock_available = True
        self.audit_present = False
        self.change_after_audit = False
        self.event_reads = 0
        self.rollbacks = 0
        self.closed = False
        self.cursor_object = FakeCursor(self)
        self.event = {
            "case_id": self.case,
            "user_id": self.target,
            "provider": "stripe",
            "provider_event_id": self.event_id,
            "stripe_event_id": self.event_id,
            "event_type": "invoice.paid",
            "handler_version": "h4g17_stripe_initial_atomic_v1",
            "processing_status": "received",
            "livemode": False,
            "transaction_id": None,
            "subscription_id": None,
            "received_at": (
                datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(minutes=30)
            ),
            "payload_json": {
                "id": self.event_id,
                "created": 1700000000,
                "type": "invoice.paid",
                "livemode": False,
                "data": {"object": {
                    "object": "invoice",
                    "id": "in_synthetic_manual",
                    "livemode": False,
                    "billing_reason": "subscription_create",
                }},
            },
        }
    def set_session(self, readonly, autocommit):
        assert readonly is True and autocommit is False
    def cursor(self):
        return self.cursor_object
    def rollback(self):
        self.rollbacks += 1
    def close(self):
        self.closed = True

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.c = FakeConnection()
        self.audit_calls = 0
        self.dispatch_calls = 0
        self.audit_failure = False
        self.dispatch_status = "processed"

    def audit(self, **kwargs):
        self.audit_calls += 1
        if self.audit_failure:
            raise RuntimeError("Simulated audit failure")
        self.c.audit_present = True
        return {
            "status": "attempt_recorded",
            "case_id": self.c.case,
            "audit_id": str(uuid.uuid4()),
        }

    def dispatch(self, event):
        self.dispatch_calls += 1
        self.assertEqual(event, self.c.event["payload_json"])
        return {"ok": True, "initial_status": self.dispatch_status}

    def call(self):
        return recover(
            connection_factory=lambda: self.c,
            checked_audit=self.audit,
            verified_initial_dispatch=self.dispatch,
            expected_database="godinc_dev",
            expected_livemode=False,
            expected_local_socket=True,
            actor_user_id=self.c.actor,
            case_id=self.c.case,
            min_age_seconds=900,
        )

    def test_valid_recovery_dispatches_once_after_audit(self):
        result = self.call()
        self.assertEqual(result["status"], "settled")
        self.assertEqual(self.audit_calls, 1)
        self.assertEqual(self.dispatch_calls, 1)
        self.assertTrue(self.c.closed)

    def test_duplicate_is_not_reported_as_new_cash(self):
        self.dispatch_status = "duplicate_event"
        self.assertEqual(self.call()["status"], "already_settled")

    def test_financial_hold_is_not_reported_as_success(self):
        self.dispatch_status = "expired_service_period_hold"
        self.assertEqual(self.call()["status"], "held_for_review")

    def test_concurrent_attempt_is_blocked(self):
        self.c.lock_available = False
        with self.assertRaises(RuntimeError):
            self.call()
        self.assertEqual(self.audit_calls, 0)
        self.assertEqual(self.dispatch_calls, 0)
        self.assertTrue(self.c.closed)

    def test_wrong_database_is_blocked(self):
        self.c.database = "another_database"
        with self.assertRaises(RuntimeError):
            self.call()
        self.assertEqual(self.audit_calls, 0)

    def test_processed_event_is_blocked(self):
        self.c.event["processing_status"] = "processed"
        with self.assertRaises(ValueError):
            self.call()
        self.assertEqual(self.audit_calls, 0)

    def test_tampered_payload_is_blocked(self):
        self.c.event["payload_json"]["id"] = "evt_other"
        with self.assertRaises(ValueError):
            self.call()
        self.assertEqual(self.audit_calls, 0)

    def test_audit_failure_prevents_dispatch(self):
        self.audit_failure = True
        with self.assertRaises(RuntimeError):
            self.call()
        self.assertEqual(self.dispatch_calls, 0)
        self.assertTrue(self.c.closed)

    def test_case_changed_after_audit_prevents_dispatch(self):
        self.c.change_after_audit = True
        with self.assertRaises(ValueError):
            self.call()
        self.assertEqual(self.dispatch_calls, 0)

    def test_unverified_result_is_rejected(self):
        self.dispatch_status = "unrecognized"
        with self.assertRaises(RuntimeError):
            self.call()
        self.assertEqual(self.dispatch_calls, 1)

    def test_invalid_uuid_prevents_connections(self):
        def forbidden():
            raise AssertionError("Connection unexpectedly opened")
        with self.assertRaises(ValueError):
            recover(
                connection_factory=forbidden,
                checked_audit=self.audit,
                verified_initial_dispatch=self.dispatch,
                expected_database="godinc_dev",
                expected_livemode=False,
                expected_local_socket=True,
                actor_user_id="invalid",
                case_id=self.c.case,
            )

if __name__ == "__main__":
    unittest.main()
