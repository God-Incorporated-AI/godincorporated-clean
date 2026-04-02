BEGIN;

DO $$
DECLARE c_name text;
BEGIN
    SELECT conname INTO c_name
    FROM pg_constraint
    WHERE conrelid = 'stripe_price_map'::regclass
      AND pg_get_constraintdef(oid) ILIKE '%support_mode%';
    IF c_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE stripe_price_map DROP CONSTRAINT %I', c_name);
    END IF;
END $$;

ALTER TABLE stripe_price_map
ADD CONSTRAINT stripe_price_map_support_mode_check
CHECK (support_mode IN ('monthly_recurring', 'annual_prepaid', 'annual_recurring'));

DO $$
DECLARE c_name text;
BEGIN
    SELECT conname INTO c_name
    FROM pg_constraint
    WHERE conrelid = 'subscriptions'::regclass
      AND pg_get_constraintdef(oid) ILIKE '%support_mode%';
    IF c_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE subscriptions DROP CONSTRAINT %I', c_name);
    END IF;
END $$;

ALTER TABLE subscriptions
ADD CONSTRAINT subscriptions_support_mode_check
CHECK (support_mode IN ('monthly_recurring', 'annual_prepaid', 'annual_recurring'));

ALTER TABLE billing_transactions
    ADD COLUMN IF NOT EXISTS support_mode TEXT,
    ADD COLUMN IF NOT EXISTS transaction_kind TEXT;

DO $$
DECLARE c_name text;
BEGIN
    SELECT conname INTO c_name
    FROM pg_constraint
    WHERE conrelid = 'billing_transactions'::regclass
      AND pg_get_constraintdef(oid) ILIKE '%support_mode%';
    IF c_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE billing_transactions DROP CONSTRAINT %I', c_name);
    END IF;
END $$;

ALTER TABLE billing_transactions
ADD CONSTRAINT billing_transactions_support_mode_check
CHECK (support_mode IN ('monthly_recurring', 'annual_prepaid', 'annual_recurring'));

DO $$
DECLARE c_name text;
BEGIN
    SELECT conname INTO c_name
    FROM pg_constraint
    WHERE conrelid = 'billing_transactions'::regclass
      AND pg_get_constraintdef(oid) ILIKE '%transaction_kind%';
    IF c_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE billing_transactions DROP CONSTRAINT %I', c_name);
    END IF;
END $$;

ALTER TABLE billing_transactions
ADD CONSTRAINT billing_transactions_transaction_kind_check
CHECK (transaction_kind IN ('monthly_initial', 'monthly_renewal', 'annual_prepaid', 'annual_renewal', 'refund', 'dispute'));

COMMIT;
