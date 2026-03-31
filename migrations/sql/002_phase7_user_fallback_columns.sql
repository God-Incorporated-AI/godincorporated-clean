BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS highest_paid_plan_ever TEXT,
    ADD COLUMN IF NOT EXISTS last_paid_plan_code TEXT,
    ADD COLUMN IF NOT EXISTS donor_floor_plan_code TEXT,
    ADD COLUMN IF NOT EXISTS scroll_floor_plan_code TEXT,
    ADD COLUMN IF NOT EXISTS fallback_floor_plan_code TEXT,
    ADD COLUMN IF NOT EXISTS renewal_offer_plan_code TEXT,
    ADD COLUMN IF NOT EXISTS last_support_mode TEXT,
    ADD COLUMN IF NOT EXISTS last_support_ended_at TIMESTAMPTZ;

COMMIT;
