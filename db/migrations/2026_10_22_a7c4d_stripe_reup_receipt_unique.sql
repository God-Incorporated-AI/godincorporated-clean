-- A7C4D: Stripe early re-up invoice receipt uniqueness, version 2.
-- Prepared in Local Dev only. NOT applied during the source-only installation.
-- Apply separately per environment under an explicitly approved DB identity gate.
-- This does not alter ordinary Stripe renewals, failures, refunds, or Apple records.
-- The source writer uses one billing_transactions row for staged intent and
-- final paid receipt; schema stripe_paid_reup_receipt.v2 is its terminal receipt.

BEGIN;
SET LOCAL lock_timeout = '1500ms';
SET LOCAL statement_timeout = '8000ms';

DO $a7c4d_guard$
BEGIN
    IF to_regclass('public.billing_transactions') IS NULL THEN
        RAISE EXCEPTION 'A7C4D STOP: billing_transactions table unavailable';
    END IF;
    IF to_regclass('public.idx_billing_transactions_stripe_reup_receipt_v2_unique') IS NOT NULL THEN
        RAISE EXCEPTION 'A7C4D STOP: index name already exists; verify definition instead of silently skipping';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM public.billing_transactions
        WHERE provider = 'stripe'
          AND provider_invoice_id IS NOT NULL
          AND raw_summary_json ->> 'schema' = 'stripe_paid_reup_receipt.v2'
        GROUP BY provider_invoice_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'A7C4D STOP: duplicate Stripe re-up invoice receipts require reconciliation';
    END IF;
END;
$a7c4d_guard$;

CREATE UNIQUE INDEX idx_billing_transactions_stripe_reup_receipt_v2_unique
ON public.billing_transactions USING btree (provider_invoice_id)
WHERE provider = 'stripe'
  AND provider_invoice_id IS NOT NULL
  AND raw_summary_json ->> 'schema' = 'stripe_paid_reup_receipt.v2';

COMMIT;
