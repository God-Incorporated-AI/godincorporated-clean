BEGIN;

ALTER TABLE billing_customers
    ADD COLUMN IF NOT EXISTS provider TEXT,
    ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT,
    ADD COLUMN IF NOT EXISTS email_at_create TEXT,
    ADD COLUMN IF NOT EXISTS default_payment_method_id TEXT,
    ADD COLUMN IF NOT EXISTS customer_metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS livemode BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

UPDATE billing_customers
SET provider = 'stripe'
WHERE provider IS NULL;

ALTER TABLE billing_customers
    ALTER COLUMN provider SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_billing_customers_provider_user_unique
ON billing_customers(provider, user_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_billing_customers_stripe_customer_unique
ON billing_customers(stripe_customer_id)
WHERE stripe_customer_id IS NOT NULL;

COMMIT;
