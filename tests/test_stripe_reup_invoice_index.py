"""A7C4D: source-only review of the narrow Stripe receipt uniqueness migration.

No SQL executed and no DB/Stripe modules imported. PostgreSQL behavior and
real concurrency are separate Local Dev acceptance gates.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SQL_FILE = ROOT / 'db/migrations/2026_10_22_a7c4d_stripe_reup_receipt_unique.sql'
WRITER = ROOT / 'services/stripe_reup_settlement.py'
INDEX_NAME = 'idx_billing_transactions_stripe_reup_receipt_v2_unique'
RECEIPT_SCHEMA = 'stripe_paid_reup_receipt.v2'


class A7C4DIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = SQL_FILE.read_text('utf-8')
        cls.writer = WRITER.read_text('utf-8')
        cls.ddl = cls.sql.split('CREATE UNIQUE INDEX ', 1)[1].split(';', 1)[0]

    def test_exact_one_unique_index(self):
        self.assertEqual(self.sql.count('CREATE UNIQUE INDEX '), 1)
        self.assertTrue(self.ddl.startswith(INDEX_NAME))
        self.assertIn('ON public.billing_transactions USING btree (provider_invoice_id)', self.ddl)

    def test_scope_requires_stripe_provider(self):
        self.assertIn("WHERE provider = 'stripe'", self.ddl)

    def test_scope_requires_real_invoice_key(self):
        self.assertIn('AND provider_invoice_id IS NOT NULL', self.ddl)

    def test_scope_requires_only_reup_receipts(self):
        self.assertIn("AND raw_summary_json ->> 'schema' = 'stripe_paid_reup_receipt.v2'", self.ddl)
        self.assertNotIn("AND status = 'succeeded'", self.ddl)

    def test_writer_receipt_uses_same_marker_and_key(self):
        self.assertIn("'schema': 'stripe_paid_reup_receipt.v2'", self.writer)
        self.assertIn("SET status = 'succeeded', provider_invoice_id = %s", self.writer)
        self.assertIn('pg_advisory_xact_lock', self.writer)

    def test_transaction_and_lock_bounds(self):
        non_comments = '\n'.join(line for line in self.sql.splitlines() if not line.lstrip().startswith('--'))
        self.assertRegex(non_comments, r'(?m)^BEGIN;$')
        self.assertRegex(non_comments, r'(?m)^COMMIT;$')
        self.assertIn("SET LOCAL lock_timeout = '1500ms'", non_comments)
        self.assertIn("SET LOCAL statement_timeout = '8000ms'", non_comments)

    def test_collision_guards_are_fail_closed(self):
        self.assertIn("to_regclass('public.idx_billing_transactions_stripe_reup_receipt_v2_unique')", self.sql)
        self.assertIn('HAVING COUNT(*) > 1', self.sql)
        self.assertIn('RAISE EXCEPTION', self.sql)
        self.assertNotIn('IF NOT EXISTS', self.ddl)

    def test_no_destructive_schema_or_unrelated_columns(self):
        executable = '\n'.join(line for line in self.sql.splitlines() if not line.lstrip().startswith('--'))
        self.assertNotRegex(executable, r'(?i)\b(DROP|TRUNCATE|DELETE|UPDATE|INSERT|ALTER)\s+(TABLE|FROM|INTO|INDEX|COLUMN)\b')
        self.assertNotIn('users.', executable)
        self.assertNotIn('subscriptions.', executable)

    def test_reup_specific_index_does_not_claim_global_uniqueness(self):
        self.assertNotIn('UNIQUE (provider, provider_invoice_id)', self.sql)
        self.assertNotIn('WHERE provider_invoice_id IS NOT NULL;', self.sql)
        self.assertIn('stripe_paid_reup_receipt.v2', self.ddl)

    def test_refunded_receipt_remains_indexed(self):
        self.assertNotIn('status = ', self.ddl)
        self.assertNotIn('transaction_kind', self.ddl)


if __name__ == '__main__':
    unittest.main(verbosity=2)
