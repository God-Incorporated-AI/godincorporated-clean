import importlib.util
import pathlib
import unittest
import uuid
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "r3_local_operator_contract",
    ROOT / "h4g17_r3_local_operator.py",
)
operator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operator)

class OperatorTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "GODINC_R3_LOCAL_RECOVERY_MODE": "manual_test_only",
            "STRIPE_LIVEMODE": "false",
            "STRIPE_SECRET_KEY": "sk_test_" + "x" * 32,
            "DATABASE_URL": "dbname=godinc_dev",
        }
        self.actor = str(uuid.uuid4())
        self.case = str(uuid.uuid4())

    def test_valid_test_configuration(self):
        operator.validate_environment(self.env)
        operator.validate_source()

    def test_live_key_rejected(self):
        self.env["STRIPE_SECRET_KEY"] = "sk_live_" + "x" * 32
        with self.assertRaises(RuntimeError):
            operator.validate_environment(self.env)

    def test_live_mode_rejected(self):
        self.env["STRIPE_LIVEMODE"] = "true"
        with self.assertRaises(RuntimeError):
            operator.validate_environment(self.env)

    def test_ambiguous_mode_rejected(self):
        self.env["STRIPE_LIVEMODE"] = "not_true"
        with self.assertRaises(RuntimeError):
            operator.validate_environment(self.env)

    def test_wrong_database_rejected(self):
        self.env["DATABASE_URL"] = "dbname=postgres"
        with self.assertRaises(RuntimeError):
            operator.validate_environment(self.env)

    def test_external_postgres_host_rejected(self):
        self.env["PGHOST"] = "example.invalid"
        with self.assertRaises(RuntimeError):
            operator.validate_environment(self.env)

    def test_manual_enable_required(self):
        self.env.pop("GODINC_R3_LOCAL_RECOVERY_MODE")
        with self.assertRaises(RuntimeError):
            operator.validate_environment(self.env)

    def test_invalid_case_identity_rejected(self):
        with self.assertRaises(ValueError):
            operator.run_case(
                self.actor, "invalid", self.env,
                module_loader=lambda _: self.fail("Module loaded")
            )

    def test_default_off_prevents_application_import(self):
        self.env.pop("GODINC_R3_LOCAL_RECOVERY_MODE")
        with self.assertRaises(RuntimeError):
            operator.run_case(
                self.actor, self.case, self.env,
                module_loader=lambda _: self.fail("Module loaded")
            )

    def test_trusted_adapter_binding_offline(self):
        calls = []

        def recover(**kwargs):
            calls.append(kwargs)
            return {
                "status": "held_for_review",
                "atomic_status": "manual_review",
                "case_id": self.case,
                "audit_id": str(uuid.uuid4()),
            }

        app = SimpleNamespace(
            __file__=str(ROOT / "main.py"),
            get_stripe_livemode=lambda: False,
            get_db_connection=lambda: None,
            dispatch_verified_initial_invoice_atomic_v1=lambda event: event,
        )
        billing = SimpleNamespace(
            __file__=str(ROOT / "services/stripe_billing.py"),
            get_stripe_livemode=lambda: False,
            recover_stripe_initial_one_case_v1=recover,
            record_stripe_initial_recovery_attempt_audit_v1=lambda **kw: kw,
        )

        def loader(name):
            return app if name == "main" else billing

        result = operator.run_case(
            self.actor, self.case, self.env,
            module_loader=loader,
        )

        self.assertEqual(result["status"], "held_for_review")
        self.assertEqual(len(calls), 1)
        self.assertIs(
            calls[0]["verified_initial_dispatch"],
            app.dispatch_verified_initial_invoice_atomic_v1,
        )
        self.assertEqual(calls[0]["expected_database"], "godinc_dev")
        self.assertIs(calls[0]["expected_livemode"], False)
        self.assertIs(calls[0]["expected_local_socket"], True)

if __name__ == "__main__":
    unittest.main()
