-- R6D-SR1 SOURCE CANDIDATE. NOT AUTHORIZED FOR EXECUTION.
-- Environment-neutral migration. A future environment-specific runner
-- must verify database identity and set godinc.r6d_expected_database.
-- This ledger alone does not authorize snapshot publication or reads.

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $r6d_preflight$
DECLARE found_columns integer;
BEGIN
    IF pg_catalog.current_setting(
        'godinc.r6d_expected_database', true
    ) IS DISTINCT FROM pg_catalog.current_database() THEN
        RAISE EXCEPTION 'STOP: R6D database not authorized';
    END IF;

    -- Fail closed until a dedicated non-superuser migration/definer role
    -- is authorized and granted the narrowly required schema privileges.
    -- Local AIServer is a superuser and must not own the production function.
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles
        WHERE rolname = CURRENT_USER AND rolsuper
    ) THEN
        RAISE EXCEPTION 'STOP: R6D revision function owner must not be superuser';
    END IF;

    IF pg_catalog.to_regclass('public.oracle_interactions') IS NULL
       OR pg_catalog.to_regclass(
           'public.temple_context_question_review_cache'
       ) IS NULL
       OR pg_catalog.to_regclass(
           'public.temple_context_themes'
       ) IS NULL
       OR pg_catalog.to_regclass(
           'public.temple_context_theme_daily'
       ) IS NULL THEN
        RAISE EXCEPTION 'STOP: required R6D tables missing';
    END IF;

    IF pg_catalog.to_regclass(
        'public.temple_context_revision_state'
    ) IS NOT NULL
       OR pg_catalog.to_regprocedure(
           'public._r6d_revision_track_change()'
       ) IS NOT NULL THEN
        RAISE EXCEPTION 'STOP: revision objects already present';
    END IF;

    SELECT COUNT(*) INTO found_columns
    FROM pg_catalog.pg_attribute
    WHERE attrelid =
        'public.oracle_interactions'::pg_catalog.regclass
      AND attname IN (
          'id', 'user_id', 'question_text', 'created_at'
      )
      AND NOT attisdropped;

    IF found_columns <> 4 THEN
        RAISE EXCEPTION 'STOP: source column contract changed';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute
        WHERE attrelid =
          'public.temple_context_question_review_cache'::pg_catalog.regclass
          AND attname = 'interaction_id'
          AND NOT attisdropped
    ) THEN
        RAISE EXCEPTION 'STOP: cache column contract changed';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute
        WHERE attrelid =
          'public.temple_context_question_review_cache'::pg_catalog.regclass
          AND attname = 'review_contract_fingerprint'
          AND NOT attisdropped
    ) THEN
        RAISE EXCEPTION 'STOP: R6D review provenance migration missing';
    END IF;
END;
$r6d_preflight$;

CREATE TABLE public.temple_context_revision_state (
    singleton_id smallint PRIMARY KEY
        DEFAULT 1 CHECK (singleton_id = 1),
    source_revision bigint NOT NULL DEFAULT 0
        CHECK (source_revision >= 0),
    published_revision bigint,
    published_window_ended_at timestamptz,
    published_policy_version text,
    published_contract_fingerprint text,
    published_at timestamptz,

    CONSTRAINT r6d_revision_publication_pair CHECK (
        (
            published_revision IS NULL
            AND published_window_ended_at IS NULL
            AND published_policy_version IS NULL
            AND published_contract_fingerprint IS NULL
            AND published_at IS NULL
        )
        OR
        (
            published_revision IS NOT NULL
            AND published_revision >= 0
            AND published_revision <= source_revision
            AND published_window_ended_at IS NOT NULL
            AND published_policy_version IS NOT NULL
            AND length(btrim(published_policy_version)) > 0
            AND published_contract_fingerprint IS NOT NULL
            AND published_contract_fingerprint ~ '^[0-9a-f]{64}$'
            AND published_at IS NOT NULL
        )
    )
);

INSERT INTO public.temple_context_revision_state (
    singleton_id, source_revision
) VALUES (1, 0);

COMMENT ON TABLE public.temple_context_revision_state IS
    'R6D source/cache revision gate. Null published_revision '
    'means no publication. Consumers must verify revision equality.';

-- A transactionally updated singleton serializes source revisions.
-- Function ownership and privileges require separate acceptance.
CREATE FUNCTION public._r6d_revision_track_change()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $r6d_trigger$
DECLARE affected boolean := false;
BEGIN
    IF TG_TABLE_SCHEMA <> 'public'
       OR TG_LEVEL <> 'STATEMENT'
       OR TG_WHEN <> 'AFTER' THEN
        RAISE EXCEPTION 'STOP: invalid revision trigger context';
    END IF;

    IF TG_TABLE_NAME = 'oracle_interactions' THEN
        IF TG_OP = 'INSERT' THEN
            SELECT EXISTS (
                SELECT 1 FROM r6d_new n
                WHERE n.user_id IS NOT NULL
                  AND n.question_text IS NOT NULL
                  AND btrim(n.question_text) <> ''
                  AND n.created_at IS NOT NULL
            ) INTO affected;

        ELSIF TG_OP = 'DELETE' THEN
            SELECT EXISTS (
                SELECT 1 FROM r6d_old o
                WHERE o.user_id IS NOT NULL
                  AND o.question_text IS NOT NULL
                  AND btrim(o.question_text) <> ''
                  AND o.created_at IS NOT NULL
            ) INTO affected;

        ELSIF TG_OP = 'UPDATE' THEN
            SELECT EXISTS (
                SELECT 1
                FROM r6d_old o
                FULL JOIN r6d_new n ON o.id = n.id
                WHERE (
                    (
                        o.user_id IS NOT NULL
                        AND o.question_text IS NOT NULL
                        AND btrim(o.question_text) <> ''
                        AND o.created_at IS NOT NULL
                    )
                    OR
                    (
                        n.user_id IS NOT NULL
                        AND n.question_text IS NOT NULL
                        AND btrim(n.question_text) <> ''
                        AND n.created_at IS NOT NULL
                    )
                )
                AND (
                    o.id IS DISTINCT FROM n.id
                    OR o.user_id IS DISTINCT FROM n.user_id
                    OR o.question_text IS DISTINCT FROM n.question_text
                    OR o.created_at IS DISTINCT FROM n.created_at
                )
            ) INTO affected;

        ELSIF TG_OP = 'TRUNCATE' THEN
            affected := true;
        ELSE
            RAISE EXCEPTION 'STOP: unsupported source operation';
        END IF;

    ELSIF TG_TABLE_NAME =
        'temple_context_question_review_cache' THEN

        IF TG_OP = 'INSERT' THEN
            SELECT EXISTS (
                SELECT 1 FROM r6d_new
            ) INTO affected;

        ELSIF TG_OP = 'UPDATE' THEN
            -- Ignore review metadata-only refreshes; compare all fields
            -- controlling source identity, approval, and embedding readiness.
            SELECT EXISTS (
                SELECT 1
                FROM r6d_old o
                FULL JOIN r6d_new n
                  ON o.interaction_id = n.interaction_id
                WHERE o.interaction_id IS NULL
                   OR n.interaction_id IS NULL
                   OR ROW(
                       o.source_fingerprint_salt,
                       o.source_fingerprint,
                       o.deidentification_version,
                       o.aggregation_version,
                       o.sanitizer_model,
                       o.review_contract_fingerprint,
                       o.review_status,
                       o.theme_label,
                       o.theme_summary,
                       o.theme_embedding::text,
                       o.theme_embedding_model,
                       (o.embedded_at IS NOT NULL)
                   ) IS DISTINCT FROM ROW(
                       n.source_fingerprint_salt,
                       n.source_fingerprint,
                       n.deidentification_version,
                       n.aggregation_version,
                       n.sanitizer_model,
                       n.review_contract_fingerprint,
                       n.review_status,
                       n.theme_label,
                       n.theme_summary,
                       n.theme_embedding::text,
                       n.theme_embedding_model,
                       (n.embedded_at IS NOT NULL)
                   )
            ) INTO affected;

        ELSIF TG_OP = 'DELETE' THEN
            SELECT EXISTS (
                SELECT 1 FROM r6d_old
            ) INTO affected;

        ELSIF TG_OP = 'TRUNCATE' THEN
            affected := true;
        ELSE
            RAISE EXCEPTION 'STOP: unsupported cache operation';
        END IF;

    ELSE
        RAISE EXCEPTION 'STOP: unexpected revision trigger table';
    END IF;

    IF affected THEN
        UPDATE public.temple_context_revision_state
        SET source_revision = source_revision + 1
        WHERE singleton_id = 1
          AND source_revision < 9223372036854775807;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'STOP: revision ledger missing or exhausted';
        END IF;
    END IF;

    RETURN NULL;
END;
$r6d_trigger$;

REVOKE ALL ON FUNCTION
    public._r6d_revision_track_change() FROM PUBLIC;

REVOKE ALL ON TABLE
    public.temple_context_revision_state FROM PUBLIC;

CREATE TRIGGER r6d_oracle_revision_insert
AFTER INSERT ON public.oracle_interactions
REFERENCING NEW TABLE AS r6d_new
FOR EACH STATEMENT
EXECUTE FUNCTION public._r6d_revision_track_change();

CREATE TRIGGER r6d_oracle_revision_update
AFTER UPDATE ON public.oracle_interactions
REFERENCING OLD TABLE AS r6d_old NEW TABLE AS r6d_new
FOR EACH STATEMENT
EXECUTE FUNCTION public._r6d_revision_track_change();

CREATE TRIGGER r6d_oracle_revision_delete
AFTER DELETE ON public.oracle_interactions
REFERENCING OLD TABLE AS r6d_old
FOR EACH STATEMENT
EXECUTE FUNCTION public._r6d_revision_track_change();

CREATE TRIGGER r6d_oracle_revision_truncate
AFTER TRUNCATE ON public.oracle_interactions
FOR EACH STATEMENT
EXECUTE FUNCTION public._r6d_revision_track_change();

CREATE TRIGGER r6d_cache_revision_insert
AFTER INSERT ON public.temple_context_question_review_cache
REFERENCING NEW TABLE AS r6d_new
FOR EACH STATEMENT
EXECUTE FUNCTION public._r6d_revision_track_change();

CREATE TRIGGER r6d_cache_revision_update
AFTER UPDATE ON public.temple_context_question_review_cache
REFERENCING OLD TABLE AS r6d_old NEW TABLE AS r6d_new
FOR EACH STATEMENT
EXECUTE FUNCTION public._r6d_revision_track_change();

CREATE TRIGGER r6d_cache_revision_delete
AFTER DELETE ON public.temple_context_question_review_cache
REFERENCING OLD TABLE AS r6d_old
FOR EACH STATEMENT
EXECUTE FUNCTION public._r6d_revision_track_change();

CREATE TRIGGER r6d_cache_revision_truncate
AFTER TRUNCATE ON public.temple_context_question_review_cache
FOR EACH STATEMENT
EXECUTE FUNCTION public._r6d_revision_track_change();

COMMIT;
