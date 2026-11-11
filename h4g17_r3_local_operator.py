"""Manual Local Development Stripe TEST recovery; no public web route."""

import argparse
import hashlib
import importlib
import os
import pathlib
import pwd
import subprocess
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parent

EXPECTED = {
    "main.py": "3c3d06daea9a456e3087bfe1ff8653256e4268745818457224a0dcb44dce1472",
    "services/stripe_billing.py": "7cbc3eb46ee3bc87b465c1f0dddf920303dba911a238782a6b179d4851f775f8",
}

def validate_environment(env):
    if env.get("GODINC_R3_LOCAL_RECOVERY_MODE") != "manual_test_only":
        raise RuntimeError("R3 operator: explicit manual mode required")
    if env.get("STRIPE_LIVEMODE") != "false":
        raise RuntimeError("R3 operator: exact Stripe TEST mode required")

    secret = env.get("STRIPE_SECRET_KEY", "")
    if not (
        isinstance(secret, str)
        and len(secret) >= 20
        and secret.startswith(("sk_test_", "rk_test_"))
        and not any(ch.isspace() for ch in secret)
    ):
        raise RuntimeError("R3 operator: Stripe TEST secret required")

    if env.get("DATABASE_URL") != "dbname=godinc_dev":
        raise RuntimeError("R3 operator: exact Local database DSN required")

    for name in ("PGHOST", "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        if env.get(name):
            raise RuntimeError("R3 operator: external PostgreSQL routing refused")

    if pwd.getpwuid(os.geteuid()).pw_name != "AIServer":
        raise RuntimeError("R3 operator: unexpected Local operating user")

def validate_source():
    for filename, expected in EXPECTED.items():
        path = ROOT / filename
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError("R3 operator: source fingerprint mismatch")

    branch = subprocess.check_output(
        ["git", "branch", "--show-current"],
        cwd=ROOT, text=True
    ).strip()
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT, text=True
    ).strip()

    if branch != "feature/app-store-integration":
        raise RuntimeError("R3 operator: unexpected Git branch")
    if head != "d43efef66b58741891ef9fea127d2c39e471ce83":
        raise RuntimeError("R3 operator: unexpected Git HEAD")

def canonical_uuid(value):
    try:
        normalized = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("R3 operator: invalid UUID") from None
    if value != normalized:
        raise ValueError("R3 operator: noncanonical UUID")
    return normalized

def run_case(actor_id, case_id, env, module_loader=importlib.import_module):
    actor = canonical_uuid(actor_id)
    case = canonical_uuid(case_id)

    validate_environment(env)
    validate_source()

    # This import occurs only AFTER the Local, TEST and source guards.
    app = module_loader("main")
    billing = module_loader("services.stripe_billing")

    if pathlib.Path(app.__file__).resolve() != ROOT / "main.py":
        raise RuntimeError("R3 operator: unexpected application module")
    if pathlib.Path(billing.__file__).resolve() != ROOT / "services/stripe_billing.py":
        raise RuntimeError("R3 operator: unexpected billing module")

    # Detect environment changes introduced during module loading.
    validate_environment(env)

    if app.get_stripe_livemode() is not False:
        raise RuntimeError("R3 operator: application mode mismatch")
    if billing.get_stripe_livemode() is not False:
        raise RuntimeError("R3 operator: billing mode mismatch")

    outcome = billing.recover_stripe_initial_one_case_v1(
        connection_factory=app.get_db_connection,
        checked_audit=billing.record_stripe_initial_recovery_attempt_audit_v1,
        verified_initial_dispatch=app.dispatch_verified_initial_invoice_atomic_v1,
        expected_database="godinc_dev",
        expected_livemode=False,
        expected_local_socket=True,
        actor_user_id=actor,
        case_id=case,
        min_age_seconds=900,
    )

    if not (
        isinstance(outcome, dict)
        and outcome.get("case_id") == case
        and outcome.get("status")
            in ("settled", "already_settled", "held_for_review")
    ):
        raise RuntimeError("R3 operator: invalid recovery result")

    return outcome

def main():
    parser = argparse.ArgumentParser(
        description="Explicit Local-only Stripe TEST case recovery"
    )
    parser.add_argument("--actor-id", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument(
        "--authorize-test-recovery",
        action="store_true"
    )
    args = parser.parse_args()

    if not args.authorize_test_recovery:
        raise RuntimeError("R3 operator: authorization flag required")

    result = run_case(
        args.actor_id, args.case_id, os.environ
    )

    print("R3 LOCAL OPERATOR RESULT:", result["status"])
    print("ATOMIC STATUS:", result["atomic_status"])
    print("CHECKED AUDIT ID:", result["audit_id"])

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("R3 LOCAL OPERATOR STOP:", type(exc).__name__)
        sys.exit(1)
