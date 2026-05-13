-- Phase 11.1C — Provider-neutral product map
--
-- Purpose:
--   Add a provider-neutral catalog table for Stripe, Apple StoreKit, and
--   Google Play Billing product mappings.
--
-- Safety:
--   This migration is additive and defensive.
--   It does not alter existing Stripe checkout behavior.
--   stripe_price_map remains the active Stripe lookup table for web/PWA.
--
-- Operational note:
--   This file is not auto-applied by Render deploys.
--   Apply manually per environment DB after verifying that environment's
--   stripe_price_map and plan_catalog are correct.
--
-- Initial seed:
--   Copy existing active Stripe rows from stripe_price_map.
--   Apple/Google rows will be added only after their platform product records exist.

CREATE TABLE IF NOT EXISTS provider_product_map (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    plan_code TEXT NOT NULL,
    support_mode TEXT NOT NULL,

    provider TEXT NOT NULL,
    provider_product_id TEXT,
    provider_price_id TEXT,

    provider_plan_group TEXT,
    display_name_override TEXT,
    store_country_scope TEXT,

    livemode BOOLEAN NOT NULL DEFAULT FALSE,
    active BOOLEAN NOT NULL DEFAULT TRUE,

    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT provider_product_map_provider_check
        CHECK (provider IN ('stripe', 'apple', 'google')),

    CONSTRAINT provider_product_map_support_mode_check
        CHECK (support_mode IN ('monthly_recurring', 'annual_recurring', 'annual_prepaid', 'contribution'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_product_map_active_unique
ON provider_product_map (provider, plan_code, support_mode, livemode)
WHERE active = TRUE;

CREATE INDEX IF NOT EXISTS idx_provider_product_map_plan_mode
ON provider_product_map (plan_code, support_mode);

CREATE INDEX IF NOT EXISTS idx_provider_product_map_provider_product
ON provider_product_map (provider, provider_product_id);

CREATE INDEX IF NOT EXISTS idx_provider_product_map_provider_price
ON provider_product_map (provider, provider_price_id);

INSERT INTO provider_product_map (
    plan_code,
    support_mode,
    provider,
    provider_product_id,
    provider_price_id,
    livemode,
    active,
    metadata_json
)
SELECT
    spm.plan_code,
    spm.support_mode,
    'stripe' AS provider,
    spm.stripe_product_id AS provider_product_id,
    spm.stripe_price_id AS provider_price_id,
    spm.livemode,
    spm.active,
    jsonb_build_object(
        'source_table', 'stripe_price_map',
        'seeded_by', '006_phase11c_provider_product_map'
    ) AS metadata_json
FROM stripe_price_map spm
JOIN plan_catalog pc
  ON pc.plan_code = spm.plan_code
WHERE spm.active = TRUE
  AND spm.support_mode IN ('monthly_recurring', 'annual_recurring')
ON CONFLICT (provider, plan_code, support_mode, livemode)
WHERE active = TRUE
DO UPDATE SET
    provider_product_id = EXCLUDED.provider_product_id,
    provider_price_id = EXCLUDED.provider_price_id,
    metadata_json = provider_product_map.metadata_json || EXCLUDED.metadata_json,
    updated_at = NOW();
