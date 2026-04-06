BEGIN;

-- =========================
-- subscriptions alignment
-- =========================
ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT,
    ADD COLUMN IF NOT EXISTS stripe_checkout_session_id TEXT,
    ADD COLUMN IF NOT EXISTS stripe_product_id TEXT,
    ADD COLUMN IF NOT EXISTS stripe_price_id TEXT,
    ADD COLUMN IF NOT EXISTS status TEXT,
    ADD COLUMN IF NOT EXISTS canceled_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS latest_invoice_id TEXT,
    ADD COLUMN IF NOT EXISTS livemode BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS subscription_metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb;

UPDATE subscriptions
SET
    stripe_subscription_id = COALESCE(stripe_subscription_id, provider_subscription_id),
    stripe_price_id = COALESCE(stripe_price_id, provider_price_id),
    status = COALESCE(status, provider_status, internal_status)
WHERE provider = 'stripe';

CREATE UNIQUE INDEX IF NOT EXISTS idx_subscriptions_stripe_subscription_unique
ON subscriptions(stripe_subscription_id)
WHERE stripe_subscription_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_subscriptions_stripe_lookup
ON subscriptions(provider, stripe_subscription_id);

-- =========================
-- payment_events alignment
-- =========================
ALTER TABLE payment_events
    ADD COLUMN IF NOT EXISTS stripe_event_id TEXT,
    ADD COLUMN IF NOT EXISTS livemode BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS api_version TEXT,
    ADD COLUMN IF NOT EXISTS helper_name TEXT,
    ADD COLUMN IF NOT EXISTS helper_applied_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS error_text TEXT,
    ADD COLUMN IF NOT EXISTS handler_version TEXT;

UPDATE payment_events
SET
    stripe_event_id = COALESCE(stripe_event_id, provider_event_id),
    error_text = COALESCE(error_text, processing_error)
WHERE provider = 'stripe';

CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_events_stripe_event_unique
ON payment_events(stripe_event_id)
WHERE stripe_event_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_payment_events_type_received
ON payment_events(event_type, received_at DESC);

-- =========================
-- billing_transactions alignment
-- =========================
ALTER TABLE billing_transactions
    ADD COLUMN IF NOT EXISTS stripe_event_id TEXT,
    ADD COLUMN IF NOT EXISTS stripe_invoice_id TEXT,
    ADD COLUMN IF NOT EXISTS stripe_payment_intent_id TEXT,
    ADD COLUMN IF NOT EXISTS stripe_charge_id TEXT,
    ADD COLUMN IF NOT EXISTS amount_subtotal INTEGER,
    ADD COLUMN IF NOT EXISTS amount_total INTEGER,
    ADD COLUMN IF NOT EXISTS livemode BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS raw_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb;

UPDATE billing_transactions
SET
    stripe_invoice_id = COALESCE(stripe_invoice_id, provider_invoice_id),
    stripe_payment_intent_id = COALESCE(stripe_payment_intent_id, provider_payment_intent_id),
    stripe_charge_id = COALESCE(stripe_charge_id, provider_charge_id),
    amount_total = COALESCE(amount_total, gross_amount_cents),
    amount_subtotal = COALESCE(amount_subtotal, net_amount_cents, gross_amount_cents)
WHERE provider = 'stripe';

CREATE INDEX IF NOT EXISTS idx_billing_transactions_stripe_event
ON billing_transactions(stripe_event_id);

CREATE INDEX IF NOT EXISTS idx_billing_transactions_stripe_invoice
ON billing_transactions(stripe_invoice_id);

COMMIT;
