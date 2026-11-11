-- H4G17M3AD: Stripe ordinary and early-re-up invoice identity, version 1.
-- Prepared in Local Dev. SOURCE ONLY; DO NOT execute as part of source installation.
-- Apply separately per environment after a verified DB identity and migration gate.
-- This is not a backfill, financial reconciliation, or entitlement change.
-- Existing Apple and Stripe early-re-up specialized indexes remain untouched.
-- For Stripe invoices, provider_invoice_id and stripe_invoice_id must agree if both exist.
-- Identified Stripe invoices must be unique within livemode across both field conventions.

BEGIN;
SET LOCAL lock_timeout = '1500ms';
SET LOCAL statement_timeout = '12000ms';

DO $h4g17_invoice_identity_guard$
BEGIN
    IF to_regclass('public.billing_transactions') IS NULL THEN
        RAISE EXCEPTION 'H4G17 STOP: billing_transactions is missing';
    END IF;
    IF to_regclass('public.idx_billing_transactions_stripe_invoice_identity_v1') IS NOT NULL THEN
        RAISE EXCEPTION 'H4G17 STOP: general Stripe invoice index name already present';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint
        WHERE conrelid = 'public.billing_transactions'::regclass
          AND conname = 'billing_transactions_stripe_invoice_pair_v1'
    ) THEN
        RAISE EXCEPTION 'H4G17 STOP: Stripe paired-invoice constraint already present';
    END IF;
    IF (
        SELECT COUNT(*)
        FROM pg_catalog.pg_attribute
        WHERE attrelid = 'public.billing_transactions'::regclass
          AND attnum > 0 AND NOT attisdropped
          AND (
                (attname = 'provider' AND atttypid = 'text'::regtype AND attnotnull)
             OR (attname = 'livemode' AND atttypid = 'boolean'::regtype AND attnotnull)
             OR (attname = 'provider_invoice_id' AND atttypid = 'text'::regtype)
             OR (attname = 'stripe_invoice_id' AND atttypid = 'text'::regtype)
          )
    ) <> 4 THEN
        RAISE EXCEPTION 'H4G17 STOP: billing invoice-identity column contract changed';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.billing_transactions
        WHERE provider = 'stripe'
          AND provider_invoice_id IS NOT NULL
          AND stripe_invoice_id IS NOT NULL
          AND provider_invoice_id <> stripe_invoice_id
    ) THEN
        RAISE EXCEPTION 'H4G17 STOP: contradictory Stripe invoice identity columns';
    END IF;
    IF EXISTS (
        SELECT 1 FROM (
            SELECT livemode, COALESCE(provider_invoice_id, stripe_invoice_id) AS invoice_id
            FROM public.billing_transactions
            WHERE provider = 'stripe'
              AND COALESCE(provider_invoice_id, stripe_invoice_id) IS NOT NULL
            GROUP BY livemode, COALESCE(provider_invoice_id, stripe_invoice_id)
            HAVING COUNT(*) > 1
        ) AS conflicts
    ) THEN
        RAISE EXCEPTION 'H4G17 STOP: duplicate Stripe invoices must be reconciled';
    END IF;
END;
$h4g17_invoice_identity_guard$;

ALTER TABLE public.billing_transactions
    ADD CONSTRAINT billing_transactions_stripe_invoice_pair_v1
    CHECK (
        provider <> 'stripe'
        OR provider_invoice_id IS NULL
        OR stripe_invoice_id IS NULL
        OR provider_invoice_id = stripe_invoice_id
    );

CREATE UNIQUE INDEX idx_billing_transactions_stripe_invoice_identity_v1
    ON public.billing_transactions USING btree
    (livemode, (COALESCE(provider_invoice_id, stripe_invoice_id)))
    WHERE provider = 'stripe'
      AND COALESCE(provider_invoice_id, stripe_invoice_id) IS NOT NULL;

COMMIT;
