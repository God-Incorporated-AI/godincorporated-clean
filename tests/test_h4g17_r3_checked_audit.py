import ast
import datetime
import json
import pathlib
import unittest
import uuid

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "services/stripe_billing.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
matches = [
    node for node in tree.body
    if isinstance(node, ast.FunctionDef)
    and node.name == "record_stripe_initial_recovery_attempt_audit_v1"
]
if len(matches) != 1:
    raise RuntimeError("Recovery audit not uniquely installed")
namespace = {}
exec(compile(ast.Module(body=matches, type_ignores=[]), str(SOURCE), "exec"), namespace)
audit = namespace["record_stripe_initial_recovery_attempt_audit_v1"]

class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.last_sql = ""
        self.last_params = None
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False
    def execute(self, statement, params=None):
        self.last_sql = statement
        self.last_params = params
        if "INSERT INTO public.admin_action_logs" in statement:
            if self.connection.fail_insert:
                raise RuntimeError("Audit insert rejected")
            self.connection.inserts += 1
    def fetchone(self):
        sql = self.last_sql
        c = self.connection
        if "current_database()" in sql:
            return {"db": c.database, "local_socket": True, "ro": "off"}
        if "FROM public.users" in sql:
            return {"role": c.role}
        if "FROM public.payment_events" in sql:
            return dict(c.event)
        if "RETURNING id" in sql:
            p = self.last_params
            return {
                "id": p[0], "actor_id": p[1], "target_id": p[2],
                "action_type": p[3], "action_payload": json.loads(p[4])
            }
        raise AssertionError("Unexpected fetch")

class FakeConnection:
    def __init__(self):
        self.database = "godinc_dev"
        self.role = "owner"
        self.actor = str(uuid.uuid4())
        self.case = str(uuid.uuid4())
        self.target = str(uuid.uuid4())
        self.fail_insert = False
        self.inserts = 0
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.cursor_instance = FakeCursor(self)
        eid = "evt_synthetic_r3_test"
        self.event = {
            "case_id": self.case, "user_id": self.target,
            "provider": "stripe", "provider_event_id": eid,
            "stripe_event_id": eid, "event_type": "invoice.paid",
            "handler_version": "h4g17_stripe_initial_atomic_v1",
            "processing_status": "received", "livemode": False,
            "transaction_id": None, "subscription_id": None,
            "received_at": datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(minutes=30),
            "payload_json": {
                "id": eid, "type": "invoice.paid", "livemode": False,
                "data": {"object": {
                    "object": "invoice", "livemode": False,
                    "billing_reason": "subscription_create",
                }},
            },
        }
    def set_session(self, readonly, autocommit):
        assert readonly is False and autocommit is False
    def cursor(self):
        return self.cursor_instance
    def commit(self):
        self.committed = True
    def rollback(self):
        self.rolled_back = True
    def close(self):
        self.closed = True

class RecoveryAuditTests(unittest.TestCase):
    def invoke(self, c):
        return audit(
            connection_factory=lambda: c,
            expected_database="godinc_dev",
            expected_livemode=False,
            expected_local_socket=True,
            actor_user_id=c.actor,
            case_id=c.case,
            min_age_seconds=900,
        )

    def test_strict_audit_commits_one_record(self):
        c = FakeConnection()
        self.assertEqual(self.invoke(c)["status"], "attempt_recorded")
        self.assertEqual(c.inserts, 1)
        self.assertTrue(c.committed)
        self.assertTrue(c.closed)

    def test_nonadministrator_fails_without_insert(self):
        c = FakeConnection()
        c.role = "user"
        with self.assertRaises(PermissionError):
            self.invoke(c)
        self.assertEqual(c.inserts, 0)
        self.assertTrue(c.rolled_back and c.closed)

    def test_wrong_database_fails_without_insert(self):
        c = FakeConnection()
        c.database = "another_database"
        with self.assertRaises(RuntimeError):
            self.invoke(c)
        self.assertEqual(c.inserts, 0)
        self.assertTrue(c.rolled_back and c.closed)

    def test_processed_event_is_not_replayed(self):
        c = FakeConnection()
        c.event["processing_status"] = "processed"
        with self.assertRaises(ValueError):
            self.invoke(c)
        self.assertEqual(c.inserts, 0)

    def test_mutated_provenance_fails_closed(self):
        c = FakeConnection()
        c.event["payload_json"]["id"] = "evt_wrong"
        with self.assertRaises(ValueError):
            self.invoke(c)
        self.assertEqual(c.inserts, 0)

    def test_audit_failure_propagates(self):
        c = FakeConnection()
        c.fail_insert = True
        with self.assertRaises(RuntimeError):
            self.invoke(c)
        self.assertFalse(c.committed)
        self.assertTrue(c.rolled_back and c.closed)

    def test_invalid_input_never_connects(self):
        def forbidden():
            raise AssertionError("Connection should not occur")
        with self.assertRaises(ValueError):
            audit(
                connection_factory=forbidden,
                expected_database="godinc_dev",
                expected_livemode=False,
                expected_local_socket=True,
                actor_user_id="not-a-uuid",
                case_id=str(uuid.uuid4()),
            )

if __name__ == "__main__":
    unittest.main()
