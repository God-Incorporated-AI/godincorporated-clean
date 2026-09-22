-- God Incorporated: registered introductory grant
-- Canonical migration source; execution remains a separate per-environment operation.
--
-- Adds the second one-time introductory allowance granted after verified
-- account activation. The existing intro_* fields remain the anonymous
-- introductory grant. Product limits remain runtime policy, not schema values.
--
-- This migration performs no usage backfill and initializes no economic state.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $preflight$
DECLARE
    required_column_count integer;
BEGIN
    IF to_regclass('public.anonymous_users') IS NULL THEN
        RAISE EXCEPTION 'STOP: missing public.anonymous_users';
    END IF;

    SELECT COUNT(*)
    INTO required_column_count
    FROM pg_catalog.pg_attribute
    WHERE attrelid = 'public.anonymous_users'::regclass
      AND attnum > 0
      AND NOT attisdropped
      AND attname = ANY(
          ARRAY[
              'intro_queries_used',
              'intro_grant_completed_at',
              'free_window_started_at',
              'free_window_queries_used',
              'verified_email_hmac',
              'free_hold_until',
              'last_account_deleted_at',
              'eligibility_policy_version'
          ]
      );

    IF required_column_count <> 8 THEN
        RAISE EXCEPTION
            'STOP: account lifecycle foundation is incomplete; expected 8 lifecycle columns, found %',
            required_column_count;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_attribute
        WHERE attrelid = 'public.anonymous_users'::regclass
          AND attnum > 0
          AND NOT attisdropped
          AND attname = ANY(
              ARRAY[
                  'registered_intro_queries_used',
                  'registered_intro_grant_completed_at'
              ]
          )
    ) THEN
        RAISE EXCEPTION
            'STOP: registered introductory grant columns already exist; inspect first';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_constraint
        WHERE conrelid = 'public.anonymous_users'::regclass
          AND conname = 'anonymous_users_eligibility_initialized'
          AND contype = 'c'
    ) THEN
        RAISE EXCEPTION
            'STOP: expected anonymous_users_eligibility_initialized constraint is missing';
    END IF;

    -- This migration is deliberately pre-runtime. If any lifecycle authority
    -- has already been initialized, stop rather than silently reinterpret it.
    IF EXISTS (
        SELECT 1
        FROM public.anonymous_users
        WHERE eligibility_policy_version IS NOT NULL
           OR intro_queries_used IS NOT NULL
           OR intro_grant_completed_at IS NOT NULL
           OR free_window_started_at IS NOT NULL
           OR free_window_queries_used IS NOT NULL
           OR verified_email_hmac IS NOT NULL
           OR free_hold_until IS NOT NULL
           OR last_account_deleted_at IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            'STOP: lifecycle/economic state is already populated; inspect before migration';
    END IF;
END;
$preflight$;


ALTER TABLE public.anonymous_users
    ADD COLUMN registered_intro_queries_used integer,
    ADD COLUMN registered_intro_grant_completed_at timestamptz,

    ADD CONSTRAINT anonymous_users_registered_intro_usage_nonnegative
        CHECK (
            registered_intro_queries_used IS NULL
            OR registered_intro_queries_used >= 0
        ),

    ADD CONSTRAINT anonymous_users_registered_intro_completion_consistent
        CHECK (
            registered_intro_grant_completed_at IS NULL
            OR registered_intro_queries_used IS NOT NULL
        );


-- An initialized eligibility policy must now carry all three economic counters:
-- anonymous introductory grant, registered introductory grant, and daily free use.
ALTER TABLE public.anonymous_users
    DROP CONSTRAINT anonymous_users_eligibility_initialized;

ALTER TABLE public.anonymous_users
    ADD CONSTRAINT anonymous_users_eligibility_initialized
        CHECK (
            eligibility_policy_version IS NULL
            OR (
                length(btrim(eligibility_policy_version)) > 0
                AND intro_queries_used IS NOT NULL
                AND registered_intro_queries_used IS NOT NULL
                AND free_window_queries_used IS NOT NULL
            )
        );


COMMENT ON COLUMN public.anonymous_users.intro_queries_used IS
    'Anonymous introductory grant usage. Product limit is runtime policy; NULL means uninitialized.';

COMMENT ON COLUMN public.anonymous_users.intro_grant_completed_at IS
    'Time the anonymous introductory grant was exhausted; NULL does not itself imply eligibility.';

COMMENT ON COLUMN public.anonymous_users.registered_intro_queries_used IS
    'One-time registered introductory grant usage after verified account activation. Product limit is runtime policy; NULL means uninitialized.';

COMMENT ON COLUMN public.anonymous_users.registered_intro_grant_completed_at IS
    'Time the one-time registered introductory grant was exhausted; NULL does not itself imply eligibility.';

COMMENT ON COLUMN public.anonymous_users.free_window_queries_used IS
    'Post-intro authenticated free usage within the current free window. Product limit is runtime policy.';

COMMENT ON COLUMN public.anonymous_users.eligibility_policy_version IS
    'NULL means economic state is uninitialized. An initialized policy requires anonymous-intro, registered-intro, and daily-free counters.';


COMMIT;
