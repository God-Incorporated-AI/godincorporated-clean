-- R6D-SR1 restricted NOLOGIN role bootstrap.
-- SOURCE ONLY: requires separately authorized, environment-verified execution.
-- Ordered after private-cache foundation and before revision/source-lock.
-- Roles are PostgreSQL-cluster-wide; this migration is deliberately one-shot.
-- Login credentials, memberships and object grants are separate controlled gates.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $r6d_role_preflight$
DECLARE
    intended text[] := ARRAY[
        'godinc_r6d_reader',
        'godinc_r6d_cache_writer',
        'godinc_r6d_publisher',
        'godinc_r6d_lock_owner',
        'godinc_r6d_revision_owner'
    ];
BEGIN
    IF pg_catalog.current_setting('godinc.r6d_expected_database', true)
       IS DISTINCT FROM pg_catalog.current_database() THEN
        RAISE EXCEPTION 'STOP: R6D database identity mismatch';
    END IF;

    IF pg_catalog.to_regclass(
        'public.temple_context_question_review_cache'
    ) IS NULL THEN
        RAISE EXCEPTION 'STOP: R6D cache foundation missing';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles
        WHERE rolname = CURRENT_USER
          AND (rolcreaterole OR rolsuper)
    ) THEN
        RAISE EXCEPTION 'STOP: insufficient role-creation authority';
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles
        WHERE rolname = ANY(intended)
    ) THEN
        RAISE EXCEPTION 'STOP: R6D role already exists';
    END IF;
END;
$r6d_role_preflight$;

CREATE ROLE godinc_r6d_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE godinc_r6d_cache_writer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE godinc_r6d_publisher NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE godinc_r6d_lock_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE godinc_r6d_revision_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
COMMIT;
