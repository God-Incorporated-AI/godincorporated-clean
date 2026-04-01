BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS idx_plan_catalog_plan_code_unique
ON plan_catalog(plan_code);

ALTER TABLE plan_catalog
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TABLE IF NOT EXISTS stripe_price_map (
    id BIGSERIAL PRIMARY KEY,
    plan_code TEXT NOT NULL REFERENCES plan_catalog(plan_code),
    support_mode TEXT NOT NULL CHECK (support_mode IN ('monthly_recurring', 'annual_prepaid')),
    livemode BOOLEAN NOT NULL DEFAULT FALSE,
    stripe_product_id TEXT NOT NULL,
    stripe_price_id TEXT NOT NULL UNIQUE,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stripe_price_map_lookup
ON stripe_price_map(plan_code, support_mode, livemode, active);

CREATE TABLE IF NOT EXISTS billing_customers (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL DEFAULT 'stripe',
    stripe_customer_id TEXT NOT NULL UNIQUE,
    email_at_create TEXT,
    default_payment_method_id TEXT,
    customer_metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    livemode BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider, user_id)
);

CREATE INDEX IF NOT EXISTS idx_billing_customers_user_id
ON billing_customers(user_id);

CREATE TABLE IF NOT EXISTS subscriptions (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    billing_customer_id BIGINT REFERENCES billing_customers(id) ON DELETE SET NULL,
    provider TEXT NOT NULL DEFAULT 'stripe',
    stripe_subscription_id TEXT UNIQUE,
    stripe_checkout_session_id TEXT,
    stripe_product_id TEXT,
    stripe_price_id TEXT,
    plan_code TEXT REFERENCES plan_catalog(plan_code),
    support_mode TEXT NOT NULL CHECK (support_mode IN ('monthly_recurring', 'annual_prepaid')),
    status TEXT NOT NULL DEFAULT 'incomplete',
    current_period_start TIMESTAMPTZ,
    current_period_end TIMESTAMPTZ,
    cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
    canceled_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    latest_invoice_id TEXT,
    livemode BOOLEAN NOT NULL DEFAULT FALSE,
    subscription_metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id
ON subscriptions(user_id);

CREATE INDEX IF NOT EXISTS idx_subscriptions_customer_id
ON subscriptions(billing_customer_id);

CREATE INDEX IF NOT EXISTS idx_subscriptions_price_id
ON subscriptions(stripe_price_id);

CREATE TABLE IF NOT EXISTS billing_transactions (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    billing_customer_id BIGINT REFERENCES billing_customers(id) ON DELETE SET NULL,
    subscription_id BIGINT REFERENCES subscriptions(id) ON DELETE SET NULL,
    provider TEXT NOT NULL DEFAULT 'stripe',
    stripe_event_id TEXT,
    stripe_checkout_session_id TEXT,
    stripe_invoice_id TEXT,
    stripe_payment_intent_id TEXT,
    stripe_charge_id TEXT,
    plan_code TEXT REFERENCES plan_catalog(plan_code),
    support_mode TEXT CHECK (support_mode IN ('monthly_recurring', 'annual_prepaid')),
    transaction_kind TEXT CHECK (transaction_kind IN ('monthly_initial', 'monthly_renewal', 'annual_prepaid', 'refund', 'dispute')),
    status TEXT NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed', 'refunded')),
    amount_subtotal BIGINT,
    amount_total BIGINT,
    currency TEXT,
    occurred_at TIMESTAMPTZ,
    livemode BOOLEAN NOT NULL DEFAULT FALSE,
    raw_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_billing_transactions_user_id
ON billing_transactions(user_id);

CREATE INDEX IF NOT EXISTS idx_billing_transactions_subscription_id
ON billing_transactions(subscription_id);

CREATE INDEX IF NOT EXISTS idx_billing_transactions_invoice_id
ON billing_transactions(stripe_invoice_id);

CREATE INDEX IF NOT EXISTS idx_billing_transactions_payment_intent_id
ON billing_transactions(stripe_payment_intent_id);

CREATE TABLE IF NOT EXISTS payment_events (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT 'stripe',
    stripe_event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    object_type TEXT,
    object_id TEXT,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    livemode BOOLEAN NOT NULL DEFAULT FALSE,
    api_version TEXT,
    payload_json JSONB NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processing_status TEXT NOT NULL DEFAULT 'received'
        CHECK (processing_status IN ('received', 'processing', 'processed', 'ignored', 'error')),
    helper_name TEXT,
    helper_applied_at TIMESTAMPTZ,
    processed_at TIMESTAMPTZ,
    error_text TEXT,
    handler_version TEXT
);

CREATE INDEX IF NOT EXISTS idx_payment_events_event_type
ON payment_events(event_type);

CREATE INDEX IF NOT EXISTS idx_payment_events_object_id
ON payment_events(object_id);

CREATE INDEX IF NOT EXISTS idx_payment_events_user_id
ON payment_events(user_id);

COMMIT;
