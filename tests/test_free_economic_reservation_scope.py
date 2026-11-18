import ast
import copy
import unittest
from pathlib import Path


class CaptureCursor:
    def __init__(self):
        self.statements = []

    def execute(self, query, parameters):
        self.statements.append((query, tuple(parameters)))

    def fetchone(self):
        return {"total": 0}


class FreeEconomicReservationScopeTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        source = Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))

        def original(name):
            matches = [
                n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == name
            ]
            if len(matches) != 1:
                raise AssertionError(
                    "Expected one function: " + name
                )
            return matches[0]

        cls.reservation_ast = original(
            "create_oracle_question_reservation"
        )

        functions = []
        for name in (
            "_count_active_question_reservations_locked",
            "_derive_free_question_quota"
        ):
            fn = copy.deepcopy(original(name))
            if fn.decorator_list:
                raise AssertionError("Unexpected function decorator")
            fn.returns = None
            for arg in (
                fn.args.posonlyargs
                + fn.args.args
                + fn.args.kwonlyargs
            ):
                arg.annotation = None
            functions.append(fn)

        namespace = {
            "__builtins__": {
                "ValueError": ValueError,
                "RuntimeError": RuntimeError,
                "str": str,
                "int": int,
                "tuple": tuple,
                "isinstance": isinstance,
                "any": any
            },
            "FREE_ECONOMIC_POLICY_VERSION":
                "__SYNTHETIC_EXPECTED_POLICY__"
        }
        module = ast.fix_missing_locations(
            ast.Module(body=functions, type_ignores=[])
        )
        exec(
            compile(module, "<isolated-financial-helpers>", "exec"),
            namespace, namespace
        )
        cls.counter = staticmethod(
            namespace["_count_active_question_reservations_locked"]
        )
        cls.quota = staticmethod(
            namespace["_derive_free_question_quota"]
        )

    def capture(self, owner_kind, owner_id, bucket,
                economic_identity=None, window=None):
        cur = CaptureCursor()
        result = self.counter(
            cur,
            owner_kind=owner_kind,
            owner_id=owner_id,
            quota_bucket=bucket,
            quota_window_started_at=window,
            economic_identity_id=economic_identity
        )
        self.assertEqual(result, 0)
        self.assertEqual(len(cur.statements), 1)
        return cur.statements[0]

    def test_shared_identity_across_all_owner_types(self):
        eid = "synthetic-shared-identity"
        first = self.capture(
            "user", "synthetic-user-a", "synthetic-free", eid
        )
        second = self.capture(
            "user", "synthetic-user-b", "synthetic-free", eid
        )
        anonymous = self.capture(
            "anonymous", eid, "synthetic-free", eid
        )

        self.assertEqual(first, second)
        self.assertEqual(second, anonymous)
        sql, parameters = first
        self.assertEqual(
            parameters, (eid, eid, eid, "synthetic-free")
        )
        self.assertEqual(sql.count("%s"), 4)
        self.assertIn("anonymous_user_id = %s", sql)
        self.assertIn(
            "WHERE free_economic_identity_id = %s", sql
        )
        self.assertIn(
            "prepared_state->'question_quota'->>"
            "'economic_identity_id' = %s", sql
        )
        self.assertNotIn(eid, sql)

    def test_paid_account_remains_owner_scoped(self):
        sql, parameters = self.capture(
            "user", "synthetic-paid-owner", "plan_window"
        )
        self.assertEqual(
            parameters, ("synthetic-paid-owner", "plan_window")
        )
        self.assertIn("user_id = %s::uuid", sql)
        self.assertNotIn(
            "free_economic_identity_id = %s", sql
        )
        self.assertEqual(sql.count("%s"), 2)

    def test_legacy_anonymous_owner_scope(self):
        sql, parameters = self.capture(
            "anonymous", "synthetic-anon", "synthetic-free"
        )
        self.assertEqual(
            parameters, ("synthetic-anon", "synthetic-free")
        )
        self.assertIn("anonymous_user_id = %s", sql)
        self.assertNotIn(
            "free_economic_identity_id = %s", sql
        )

    def test_legacy_missing_bucket_counted(self):
        sql, _ = self.capture(
            "user", "synthetic-user", "synthetic-free",
            "synthetic-identity"
        )
        self.assertIn(
            "prepared_state->'question_quota'->>'bucket'"
            " IS NULL", sql
        )
        self.assertIn(
            "prepared_state->'question_quota'->>'bucket'"
            " = %s", sql
        )

    def test_window_filter_and_parameter_order(self):
        window = "2026-11-18T00:00:00+00:00"
        eid = "synthetic-identity"
        sql, parameters = self.capture(
            "user", "synthetic-user", "synthetic-daily",
            eid, window
        )
        self.assertEqual(
            parameters,
            (eid, eid, eid, "synthetic-daily", window)
        )
        self.assertEqual(sql.count("%s"), 5)
        self.assertIn(
            "prepared_state->'question_quota'->>"
            "'window_started_at'", sql
        )

    def test_active_reservation_filters_retained(self):
        sql, _ = self.capture(
            "user", "synthetic-user", "synthetic-free",
            "synthetic-identity"
        )
        for expected in (
            "'browser_realtime'",
            "'device_inference'",
            "'server_inference'",
            "'prepared'",
            "'completing'",
            "expires_at > NOW()"
        ):
            self.assertIn(expected, sql)

    def test_invalid_owner_and_blank_identity_fail_closed(self):
        with self.assertRaises(ValueError):
            self.capture(
                "other", "synthetic-user", "synthetic-free"
            )
        with self.assertRaises(ValueError):
            self.capture(
                "user", "synthetic-user", "synthetic-free",
                "   "
            )

    def test_missing_policy_does_not_grant_quota(self):
        for row in (
            {},
            {"eligibility_policy_version": None},
            {"eligibility_policy_version": ""}
        ):
            result = self.quota(row, authenticated=True)
            self.assertIs(result["initialized"], False)
            self.assertTrue(all(
                value is None
                for key, value in result.items()
                if key != "initialized"
            ))

    def test_invalid_policy_and_missing_counters_fail(self):
        with self.assertRaises(RuntimeError):
            self.quota(
                {"eligibility_policy_version": "__INVALID__"},
                authenticated=True
            )
        with self.assertRaises(RuntimeError):
            self.quota(
                {"eligibility_policy_version":
                    "__SYNTHETIC_EXPECTED_POLICY__"},
                authenticated=True
            )

    def test_caller_wires_shared_identity_only_for_free(self):
        calls = [
            n for n in ast.walk(self.reservation_ast)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id ==
                "_count_active_question_reservations_locked"
        ]
        self.assertEqual(len(calls), 1)
        options = {
            item.arg: item.value
            for item in calls[0].keywords
        }
        expression = options["economic_identity_id"]
        self.assertIsInstance(expression, ast.IfExp)
        self.assertIsInstance(expression.test, ast.Name)
        self.assertEqual(expression.test.id, "free_economic")
        self.assertIsInstance(expression.body, ast.Name)
        self.assertEqual(
            expression.body.id, "economic_identity_id"
        )
        self.assertIsInstance(expression.orelse, ast.Constant)
        self.assertIsNone(expression.orelse.value)
