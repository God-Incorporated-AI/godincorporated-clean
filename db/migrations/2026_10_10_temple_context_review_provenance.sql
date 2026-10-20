-- R6D-SR1 VERSIONED SOURCE. NOT AUTHORIZED FOR EXECUTION BY FILE PRESENCE.
-- Run only with separately approved environment-specific database and owner guards.
-- Database identity uses the accepted SR1 revision migration setting.
-- Apply under private cache table-owner authority, BEFORE the revised V3.
BEGIN;
DO $r6d_guard$
BEGIN
    IF pg_catalog.current_setting('godinc.r6d_expected_database', true)
       IS DISTINCT FROM pg_catalog.current_database() THEN
        RAISE EXCEPTION 'STOP: wrong Local Dev database';
    END IF;
    IF pg_catalog.to_regclass(
        'public.temple_context_question_review_cache') IS NULL THEN
        RAISE EXCEPTION 'STOP: private cache table missing';
    END IF;
    IF pg_catalog.to_regclass('public.temple_context_revision_state')
        IS NOT NULL THEN
        RAISE EXCEPTION 'STOP: revision ledger already installed';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute
        WHERE attrelid =
            'public.temple_context_question_review_cache'::pg_catalog.regclass
          AND attname = 'review_contract_fingerprint'
          AND NOT attisdropped
    ) THEN
        RAISE EXCEPTION 'STOP: review provenance column already exists';
    END IF;
END;
$r6d_guard$;
ALTER TABLE public.temple_context_question_review_cache
    ADD COLUMN review_contract_fingerprint text;
ALTER TABLE public.temple_context_question_review_cache
    ADD CONSTRAINT r6d_review_cache_provenance_check
    CHECK (review_contract_fingerprint IS NULL OR
           review_contract_fingerprint ~ '^[0-9a-f]{64}$');
COMMENT ON COLUMN
    public.temple_context_question_review_cache.review_contract_fingerprint
IS 'Private review provenance; legacy NULL values never qualify for provenance-aware publication.';
COMMIT;
