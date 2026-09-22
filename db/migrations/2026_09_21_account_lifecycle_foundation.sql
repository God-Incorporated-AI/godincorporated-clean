-- God Incorporated: account lifecycle foundation
-- Canonical migration source; execution remains a separate per-environment operation.
-- Baseline: source 8908b88; schema audit completed 2026-09-21.
-- Apply only after the target database environment has been explicitly verified.
-- No usage backfill, account deletion, content transfer, provider call, or
-- runtime change is included. Separate approval is required before execution.
--
-- This schema does not establish that retained content is anonymous, that
-- consent exists, or that retention after deletion is legally permitted.

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $preflight$
DECLARE
    table_name text;
    table_oid regclass;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'anonymous_users', 'users', 'sessions',
        'billing_transactions', 'donations', 'admin_action_logs'
    ]
    LOOP
        table_oid := to_regclass(format('public.%I', table_name));
        IF table_oid IS NULL THEN
            RAISE EXCEPTION 'STOP: missing public.%', table_name;
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_class
            WHERE oid = table_oid AND relkind = 'r'
        ) THEN
            RAISE EXCEPTION 'STOP: public.% is not an ordinary table', table_name;
        END IF;
    END LOOP;

    IF to_regclass('public.community_corpus_items') IS NOT NULL THEN
        RAISE EXCEPTION 'STOP: community_corpus_items already exists; inspect first';
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute
        WHERE attrelid = 'public.anonymous_users'::regclass
          AND attnum > 0 AND NOT attisdropped
          AND attname = ANY(ARRAY[
              'intro_queries_used', 'intro_grant_completed_at',
              'free_window_started_at', 'free_window_queries_used',
              'verified_email_hmac', 'free_hold_until',
              'last_account_deleted_at', 'eligibility_policy_version'
          ])
    ) THEN
        RAISE EXCEPTION 'STOP: lifecycle columns already exist; inspect first';
    END IF;
END;
$preflight$;

-- 1. Extend the existing identity authority; do not create a second identity.
-- NULL counters/policy mean UNINITIALIZED, not unused introductory allowance.
-- Existing runtime remains on its existing authority. Any later switch must
-- require validated initialization and must never COALESCE unknown usage to 0.
ALTER TABLE public.anonymous_users
    ADD COLUMN intro_queries_used integer,
    ADD COLUMN intro_grant_completed_at timestamptz,
    ADD COLUMN free_window_started_at timestamptz,
    ADD COLUMN free_window_queries_used integer,
    ADD COLUMN verified_email_hmac text,
    ADD COLUMN free_hold_until timestamptz,
    ADD COLUMN last_account_deleted_at timestamptz,
    ADD COLUMN eligibility_policy_version text,
    ADD CONSTRAINT anonymous_users_intro_usage_nonnegative
        CHECK (intro_queries_used IS NULL OR intro_queries_used >= 0),
    ADD CONSTRAINT anonymous_users_free_usage_nonnegative
        CHECK (free_window_queries_used IS NULL OR free_window_queries_used >= 0),
    ADD CONSTRAINT anonymous_users_free_window_consistent
        CHECK (
            free_window_queries_used IS NULL
            OR free_window_queries_used = 0
            OR free_window_started_at IS NOT NULL
        ),
    ADD CONSTRAINT anonymous_users_intro_completion_consistent
        CHECK (
            intro_grant_completed_at IS NULL
            OR intro_queries_used IS NOT NULL
        ),
    ADD CONSTRAINT anonymous_users_eligibility_initialized
        CHECK (
            eligibility_policy_version IS NULL
            OR (
                length(btrim(eligibility_policy_version)) > 0
                AND intro_queries_used IS NOT NULL
                AND free_window_queries_used IS NOT NULL
            )
        ),
    ADD CONSTRAINT anonymous_users_email_hmac_format
        CHECK (
            verified_email_hmac IS NULL
            OR verified_email_hmac ~ '^[0-9a-f]{64}$'
        );

-- Lookup only: multiple browser identities can carry the same verified email
-- HMAC. This index neither merges their accounts nor implements quota grants.
-- A later design must also handle multiple emails on one browser without
-- overwriting away prior eligibility evidence. No HMAC is populated here.
CREATE INDEX idx_anonymous_users_verified_email_hmac
    ON public.anonymous_users (verified_email_hmac)
    WHERE verified_email_hmac IS NOT NULL;

COMMENT ON COLUMN public.anonymous_users.eligibility_policy_version IS
    'NULL means economic state is uninitialized; not evidence of free eligibility.';
COMMENT ON COLUMN public.anonymous_users.verified_email_hmac IS
    'Pseudonymous verified-email HMAC-SHA256; not anonymous data or an authentication credential. Retention policy and key lifecycle require separate approval.';

-- 2. Empty destination for independently sanitized corpus derivatives.
-- Never place raw personal source text or unresolved personal material here.
-- Quarantined/rejected refer to already sanitized candidates, NOT a permanent
-- holding area for identifiable source content. Only approved ready items
-- may ever be eligible for a later authorized training/retrieval process.
-- Version labels and a ready status are not themselves proof of anonymization,
-- contribution rights, valid consent, or permission to retain after deletion.
-- No source IDs, owner IDs, original filenames, or original storage references.
-- Hash the canonical SANITIZED payload, never the original personal payload.
CREATE TABLE public.community_corpus_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    content_kind text NOT NULL,
    scroll_text text,
    question_text text,
    response_text text,
    oracle_persona text,
    input_mode text,
    content_hash text NOT NULL,
    deidentification_version text NOT NULL,
    disclosure_version text NOT NULL,
    status text NOT NULL DEFAULT 'quarantined',
    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT community_corpus_items_kind_check
        CHECK (content_kind IN ('scroll', 'dialogue')),
    CONSTRAINT community_corpus_items_payload_check
        CHECK (
            (
                content_kind = 'scroll'
                AND scroll_text IS NOT NULL
                AND length(btrim(scroll_text)) > 0
                AND question_text IS NULL AND response_text IS NULL
                AND oracle_persona IS NULL AND input_mode IS NULL
            )
            OR (
                content_kind = 'dialogue'
                AND scroll_text IS NULL
                AND question_text IS NOT NULL
                AND length(btrim(question_text)) > 0
                AND response_text IS NOT NULL
                AND length(btrim(response_text)) > 0
            )
        ),
    CONSTRAINT community_corpus_items_persona_check
        CHECK (oracle_persona IS NULL OR length(btrim(oracle_persona)) > 0),
    CONSTRAINT community_corpus_items_input_mode_check
        CHECK (input_mode IS NULL OR input_mode IN ('text', 'voice')),
    CONSTRAINT community_corpus_items_hash_check
        CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT community_corpus_items_deidentification_version_check
        CHECK (length(btrim(deidentification_version)) > 0),
    CONSTRAINT community_corpus_items_disclosure_version_check
        CHECK (length(btrim(disclosure_version)) > 0),
    CONSTRAINT community_corpus_items_status_check
        CHECK (status IN ('ready', 'quarantined', 'rejected')),
    CONSTRAINT community_corpus_items_kind_hash_unique
        UNIQUE (content_kind, content_hash)
);

COMMENT ON TABLE public.community_corpus_items IS
    'Sanitized corpus derivatives only. Empty at migration. Not automatically connected to production retrieval or training; no ownership back-pointer.';

-- 3. Change exactly four audited foreign keys, retaining their actual names.
-- Only billing_transactions.user_id changes nullability.
-- No user/account/transaction row is deleted or detached by this migration.
-- No broad CASCADE conversion; personal-content restrictions stay untouched.
-- Provider identifiers/payloads remain potentially personal after SET NULL;
-- later deletion work must apply a justified, limited retention/scrubbing policy.
DO $foreign_keys$
DECLARE
    spec record;
    fk record;
    child_oid regclass;
    parent_oid regclass;
    child_attnum smallint;
    parent_attnum smallint;
    child_type oid;
    parent_type oid;
    child_not_null boolean;
    fk_count integer;
    fk_comment text;
BEGIN
    FOR spec IN
        SELECT * FROM (VALUES
            ('billing_transactions', 'user_id', 'users', 'c', true),
            ('donations', 'user_id', 'users', 'a', false),
            ('donations', 'session_id', 'sessions', 'a', false),
            ('admin_action_logs', 'target_user_id', 'users', 'a', false)
        ) AS expected(child_table, child_column, parent_table,
                      expected_delete_action, expected_not_null)
    LOOP
        child_oid := to_regclass(format('public.%I', spec.child_table));
        parent_oid := to_regclass(format('public.%I', spec.parent_table));

        SELECT a.attnum, a.atttypid, a.attnotnull
        INTO STRICT child_attnum, child_type, child_not_null
        FROM pg_catalog.pg_attribute a
        WHERE a.attrelid = child_oid AND a.attname = spec.child_column
          AND a.attnum > 0 AND NOT a.attisdropped;

        SELECT a.attnum, a.atttypid
        INTO STRICT parent_attnum, parent_type
        FROM pg_catalog.pg_attribute a
        WHERE a.attrelid = parent_oid AND a.attname = 'id'
          AND a.attnum > 0 AND NOT a.attisdropped;

        IF child_type <> 'pg_catalog.uuid'::regtype
           OR parent_type <> 'pg_catalog.uuid'::regtype
           OR child_not_null IS DISTINCT FROM spec.expected_not_null THEN
            RAISE EXCEPTION 'STOP: unexpected column definition for %.%',
                spec.child_table, spec.child_column;
        END IF;

        SELECT count(*) INTO fk_count
        FROM pg_catalog.pg_constraint c
        WHERE c.contype = 'f' AND c.conrelid = child_oid
          AND child_attnum = ANY(c.conkey);

        IF fk_count <> 1 THEN
            RAISE EXCEPTION 'STOP: expected one FK for %.%, found %',
                spec.child_table, spec.child_column, fk_count;
        END IF;

        SELECT c.* INTO STRICT fk
        FROM pg_catalog.pg_constraint c
        WHERE c.contype = 'f' AND c.conrelid = child_oid
          AND child_attnum = ANY(c.conkey);

        IF fk.conkey IS DISTINCT FROM ARRAY[child_attnum]::smallint[]
           OR fk.confrelid <> parent_oid
           OR fk.confkey IS DISTINCT FROM ARRAY[parent_attnum]::smallint[]
           OR fk.confdeltype::text IS DISTINCT FROM spec.expected_delete_action
           OR fk.confupdtype::text <> 'a'
           OR fk.confmatchtype::text <> 's'
           OR fk.condeferrable OR fk.condeferred
           OR NOT fk.convalidated
           OR NOT fk.conislocal OR fk.coninhcount <> 0 THEN
            RAISE EXCEPTION 'STOP: FK differs from audited shape for %.%',
                spec.child_table, spec.child_column;
        END IF;

        fk_comment := obj_description(fk.oid, 'pg_constraint');

        IF child_not_null THEN
            EXECUTE format(
                'ALTER TABLE public.%I ALTER COLUMN %I DROP NOT NULL',
                spec.child_table, spec.child_column
            );
        END IF;

        EXECUTE format('ALTER TABLE public.%I DROP CONSTRAINT %I',
                       spec.child_table, fk.conname);
        EXECUTE format(
            'ALTER TABLE public.%I ADD CONSTRAINT %I FOREIGN KEY (%I) '
            'REFERENCES public.%I (id) ON DELETE SET NULL',
            spec.child_table, fk.conname, spec.child_column, spec.parent_table
        );

        IF fk_comment IS NOT NULL THEN
            EXECUTE format('COMMENT ON CONSTRAINT %I ON public.%I IS %L',
                           fk.conname, spec.child_table, fk_comment);
        END IF;

        RAISE NOTICE 'Prepared %.%: FK % now ON DELETE SET NULL',
            spec.child_table, spec.child_column, fk.conname;
    END LOOP;
END;
$foreign_keys$;

-- No existing personal data, economic usage, ownership, or claim is rewritten.
-- Separate acceptance must cover backfill/cutover, atomic reservation and
-- completion accounting, shared-device and multi-email identity reconciliation,
-- billing/provider retries, and the full account deletion lifecycle.
COMMIT;
