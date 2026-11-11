import ast
import pathlib
import unittest
import uuid

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "services/stripe_billing.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
selected = [
    node for node in tree.body
    if isinstance(node, ast.FunctionDef)
    and node.name == "inspect_stripe_initial_recovery_cases_v1"
]
if len(selected) != 1:
    raise RuntimeError("R3 monitor function not uniquely installed")
namespace = {}
exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), namespace)
monitor = namespace["inspect_stripe_initial_recovery_cases_v1"]

class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.calls = []
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False
    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if not sql.lstrip().startswith(("SELECT", "SET LOCAL")):
            raise AssertionError("Unexpected database operation")
    def fetchone(self):
        return {"db": self.conn.database, "ro": "on"}
    def fetchall(self):
        return self.conn.rows

class FakeConnection:
    def __init__(self, database="godinc_dev", rows=None):
        self.database = database
        self.rows = rows if rows is not None else []
        self.cursor_obj = FakeCursor(self)
        self.readonly = False
        self.closed = False
        self.rolled_back = False
    def set_session(self, readonly, autocommit):
        self.readonly = readonly
        assert autocommit is False
    def cursor(self):
        return self.cursor_obj
    def rollback(self):
        self.rolled_back = True
    def close(self):
        self.closed = True

class R3MonitorTests(unittest.TestCase):
    def call(self, conn):
        return monitor(
            connection_factory=lambda: conn,
            expected_database="godinc_dev",
            expected_livemode=False,
            older_than_seconds=900,
            max_cases=50,
        )

    def test_empty_scan_is_read_only(self):
        conn = FakeConnection()
        self.assertEqual(self.call(conn), [])
        self.assertTrue(conn.readonly)
        self.assertTrue(conn.rolled_back)
        self.assertTrue(conn.closed)
        self.assertEqual(len(conn.cursor_obj.calls), 3)

    def test_returns_only_sanitized_fields(self):
        uid = str(uuid.uuid4())
        conn = FakeConnection(rows=[{
            "case_id": uid,
            "classification": "received_overdue",
            "age_seconds": 1200,
            "provider_customer_id": "FORBIDDEN",
        }])
        self.assertEqual(self.call(conn), [{
            "case_id": uid,
            "classification": "received_overdue",
            "age_seconds": 1200,
        }])

    def test_wrong_database_fails_closed(self):
        conn = FakeConnection(database="wrong_database")
        with self.assertRaises(RuntimeError):
            self.call(conn)
        self.assertTrue(conn.closed)
        self.assertTrue(conn.rolled_back)
        self.assertEqual(len(conn.cursor_obj.calls), 2)

    def test_invalid_parameters_never_connect(self):
        def forbidden_connection():
            raise AssertionError("Connection should not occur")
        with self.assertRaises(ValueError):
            monitor(
                connection_factory=forbidden_connection,
                expected_database="godinc_dev",
                expected_livemode="false",
                older_than_seconds=900,
            )
        with self.assertRaises(ValueError):
            monitor(
                connection_factory=forbidden_connection,
                expected_database="godinc_dev",
                expected_livemode=False,
                older_than_seconds=0,
            )

if __name__ == "__main__":
    unittest.main()
