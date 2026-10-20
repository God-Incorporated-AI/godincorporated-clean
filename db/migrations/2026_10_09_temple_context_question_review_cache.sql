-- R6D private, deletable per-interaction theme review cache.
-- Canonical forward migration: apply separately per environment.
-- No raw question, response, account or session identifiers are stored.
-- interaction_id is private cache provenance, never aggregate output.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $preflight$
BEGIN
    IF to_regclass('public.temple_context_question_review_cache') IS NOT NULL THEN
        RAISE EXCEPTION 'STOP: R6D review cache exists; inspect first';
    END IF;
    IF to_regclass('public.oracle_interactions') IS NULL THEN
        RAISE EXCEPTION 'STOP: oracle_interactions source missing';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        RAISE EXCEPTION 'STOP: required pgvector extension missing';
    END IF;
END;
$preflight$;

CREATE TABLE public.temple_context_question_review_cache (
    interaction_id uuid PRIMARY KEY
        REFERENCES public.oracle_interactions(id) ON DELETE CASCADE,
    source_fingerprint_salt uuid NOT NULL,
    source_fingerprint text NOT NULL,
    deidentification_version text NOT NULL,
    aggregation_version text NOT NULL,
    sanitizer_model text NOT NULL,
    review_status text NOT NULL,
    theme_label text,
    theme_summary text,
    theme_embedding vector(1536),
    theme_embedding_model text,
    reviewed_at timestamptz NOT NULL DEFAULT now(),
    embedded_at timestamptz,

    CONSTRAINT r6d_review_cache_fingerprint_check
        CHECK (source_fingerprint ~ '^[0-9a-f]{64}$'),

    CONSTRAINT r6d_review_cache_contract_check
        CHECK (length(btrim(deidentification_version)) > 0
           AND length(btrim(aggregation_version)) > 0
           AND length(btrim(sanitizer_model)) > 0),

    CONSTRAINT r6d_review_cache_status_check
        CHECK (review_status IN ('approved', 'rejected')),

    CONSTRAINT r6d_review_cache_payload_check CHECK (
        (review_status = 'rejected'
            AND theme_label IS NULL
            AND theme_summary IS NULL
            AND theme_embedding IS NULL
            AND theme_embedding_model IS NULL
            AND embedded_at IS NULL)
        OR
        (review_status = 'approved'
            AND theme_label IS NOT NULL
            AND length(btrim(theme_label)) > 0
            AND theme_summary IS NOT NULL
            AND length(btrim(theme_summary)) > 0
            AND (
                (theme_embedding IS NULL
                    AND theme_embedding_model IS NULL
                    AND embedded_at IS NULL)
                OR
                (theme_embedding IS NOT NULL
                    AND theme_embedding_model IS NOT NULL
                    AND length(btrim(theme_embedding_model)) > 0
                    AND embedded_at IS NOT NULL)
            ))
    )
);

COMMENT ON TABLE public.temple_context_question_review_cache IS
    'Private R6D review/embedding cache. Interaction FK cascades on account deletion; never expose in community aggregates.';

COMMENT ON COLUMN public.temple_context_question_review_cache.source_fingerprint IS
    'SHA256 over per-record random salt plus original question, calculated only in private processing; not a cross-seeker identifier.';

COMMIT;
