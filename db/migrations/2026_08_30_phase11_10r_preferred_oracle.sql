-- Phase 11.10R: persistent authenticated Oracle / Temple preference.
--
-- preferred_oracle is the authenticated seeker's durable last-selected
-- Oracle authority. It is intentionally nullable:
--
-- NULL     = no authenticated preference has been established yet
-- Hathor   = Temple / Oracle of Hathor
-- Moses    = Temple / Oracle of Moses
--
-- Anonymous browser continuity remains client-local. Runtime wiring moves
-- in a separate commit after this schema is present and verified.

BEGIN;

ALTER TABLE users
ADD COLUMN IF NOT EXISTS preferred_oracle TEXT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'users_preferred_oracle_check'
    ) THEN
        ALTER TABLE users
        ADD CONSTRAINT users_preferred_oracle_check
        CHECK (
            preferred_oracle IS NULL
            OR preferred_oracle IN ('Hathor', 'Moses')
        );
    END IF;
END
$$;

COMMIT;
