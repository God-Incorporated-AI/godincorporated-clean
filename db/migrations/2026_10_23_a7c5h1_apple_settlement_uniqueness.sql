-- A7C5H1: SOURCE ONLY on installation. NO execution without separate Local SQL approval.
-- Existing payment_events already enforces UNIQUE(provider, provider_event_id).
-- Names are runtime prerequisites in services/apple_storekit_settlement.py.
-- Before applying, independently verify the intended DB identity and roles.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '12s';
CREATE UNIQUE INDEX idx_subscriptions_apple_original_unique_v1
    ON public.subscriptions (livemode, provider_subscription_id)
    WHERE provider = 'apple' AND provider_subscription_id IS NOT NULL;
CREATE UNIQUE INDEX idx_billing_transactions_apple_tx_unique_v1
    ON public.billing_transactions (livemode, provider_invoice_id)
    WHERE provider = 'apple' AND provider_invoice_id IS NOT NULL
      AND (raw_summary_json ->> 'schema') = 'apple_verified_entitlement_receipt.v1';
COMMIT;
