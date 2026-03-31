BEGIN;

CREATE TABLE IF NOT EXISTS plan_catalog (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_code TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    rank_order INTEGER NOT NULL,
    is_free_plan BOOLEAN NOT NULL DEFAULT FALSE,

    monthly_price_cents INTEGER,
    annual_prepaid_price_cents INTEGER,
    annual_term_days INTEGER NOT NULL DEFAULT 365,

    monthly_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    annual_enabled BOOLEAN NOT NULL DEFAULT FALSE,

    question_limit INTEGER,
    memory_depth INTEGER,
    is_unlimited_questions BOOLEAN NOT NULL DEFAULT FALSE,

    stripe_monthly_price_id TEXT,
    stripe_annual_price_id TEXT,

    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS billing_customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_customer_id TEXT NOT NULL,
    email_snapshot TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider, provider_customer_id),
    UNIQUE (user_id, provider)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    billing_customer_id UUID REFERENCES billing_customers(id) ON DELETE SET NULL,

    plan_code TEXT NOT NULL,
    provider TEXT NOT NULL,

    provider_subscription_id TEXT,
    provider_price_id TEXT,

    support_mode TEXT NOT NULL CHECK (
        support_mode IN ('monthly_recurring', 'annual_prepaid')
    ),

    provider_status TEXT,
    internal_status TEXT NOT NULL CHECK (
        internal_status IN ('pending', 'active', 'grace', 'expired', 'cancelled')
    ),

    started_at TIMESTAMPTZ,
    current_period_start TIMESTAMPTZ,
    current_period_end TIMESTAMPTZ,
    grace_period_ends_at TIMESTAMPTZ,

    cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
    auto_renews BOOLEAN NOT NULL DEFAULT FALSE,

    canceled_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    last_payment_at TIMESTAMPTZ,
    last_failed_payment_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id
    ON subscriptions(user_id);

CREATE INDEX IF NOT EXISTS idx_subscriptions_provider_subscription_id
    ON subscriptions(provider_subscription_id);

CREATE INDEX IF NOT EXISTS idx_subscriptions_internal_status
    ON subscriptions(internal_status);

CREATE TABLE IF NOT EXISTS billing_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subscription_id UUID REFERENCES subscriptions(id) ON DELETE SET NULL,

    plan_code TEXT,
    provider TEXT NOT NULL,

    transaction_kind TEXT NOT NULL CHECK (
        transaction_kind IN (
            'monthly_initial',
            'monthly_renewal',
            'annual_prepaid',
            'manual_adjustment',
            'refund',
            'dispute'
        )
    ),

    provider_invoice_id TEXT,
    provider_payment_intent_id TEXT,
    provider_charge_id TEXT,
    provider_checkout_session_id TEXT,

    currency TEXT NOT NULL DEFAULT 'usd',
    gross_amount_cents INTEGER NOT NULL,
    net_amount_cents INTEGER,

    status TEXT NOT NULL CHECK (
        status IN ('pending', 'succeeded', 'failed', 'refunded', 'disputed')
    ),

    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_billing_transactions_user_id
    ON billing_transactions(user_id);

CREATE INDEX IF NOT EXISTS idx_billing_transactions_subscription_id
    ON billing_transactions(subscription_id);

CREATE INDEX IF NOT EXISTS idx_billing_transactions_status
    ON billing_transactions(status);

CREATE TABLE IF NOT EXISTS payment_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider TEXT NOT NULL,
    provider_event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_created_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    object_type TEXT,
    object_id TEXT,

    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    subscription_id UUID REFERENCES subscriptions(id) ON DELETE SET NULL,
    transaction_id UUID REFERENCES billing_transactions(id) ON DELETE SET NULL,

    payload_json JSONB NOT NULL,
    processing_status TEXT NOT NULL CHECK (
        processing_status IN ('received', 'processed', 'ignored', 'failed')
    ) DEFAULT 'received',
    processed_at TIMESTAMPTZ,
    processing_error TEXT,

    UNIQUE (provider, provider_event_id)
);

CREATE INDEX IF NOT EXISTS idx_payment_events_user_id
    ON payment_events(user_id);

CREATE INDEX IF NOT EXISTS idx_payment_events_subscription_id
    ON payment_events(subscription_id);

CREATE INDEX IF NOT EXISTS idx_payment_events_processing_status
    ON payment_events(processing_status);

COMMIT;
