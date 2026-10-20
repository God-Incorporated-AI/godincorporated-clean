-- R6D-SR1 environment-bound source-lock function. SOURCE ONLY; do not execute by file presence.
-- Prerequisites: five restricted NOLOGIN roles, scoped source privileges,
-- temporary public schema CREATE for godinc_r6d_lock_owner and a separately
-- verified environment-specific database identity.
-- Runner must set godinc.r6d_expected_database for its session, then
-- execute this migration as godinc_r6d_lock_owner. No passwords in SQL.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $r6d_lock_install$
BEGIN
    IF pg_catalog.current_setting('godinc.r6d_expected_database', true)
       IS DISTINCT FROM pg_catalog.current_database() THEN
        RAISE EXCEPTION 'STOP: R6D target database identity mismatch';
    END IF;
    IF current_user <> 'godinc_r6d_lock_owner'
       OR EXISTS (
           SELECT 1 FROM pg_catalog.pg_roles
           WHERE rolname = CURRENT_USER AND (rolsuper OR rolcanlogin)
       ) THEN
        RAISE EXCEPTION 'STOP: R6D lock-owner authority mismatch';
    END IF;
    IF pg_catalog.to_regclass('public.oracle_interactions') IS NULL
       OR pg_catalog.to_regprocedure(
           'public._r6d_lock_review_source(uuid,uuid,text,timestamptz)'
       ) IS NOT NULL
       OR NOT EXISTS (
           SELECT 1 FROM pg_catalog.pg_roles
           WHERE rolname = 'godinc_r6d_cache_writer'
             AND NOT rolcanlogin AND NOT rolsuper
       ) THEN
        RAISE EXCEPTION 'STOP: R6D source-lock prerequisite mismatch';
    END IF;
    IF NOT pg_catalog.has_schema_privilege(
        CURRENT_USER, 'public', 'CREATE'
    ) OR NOT pg_catalog.has_column_privilege(
        CURRENT_USER, 'public.oracle_interactions', 'id', 'SELECT'
    ) OR NOT pg_catalog.has_column_privilege(
        CURRENT_USER, 'public.oracle_interactions', 'user_id', 'SELECT'
    ) OR NOT pg_catalog.has_column_privilege(
        CURRENT_USER, 'public.oracle_interactions', 'question_text', 'SELECT'
    ) OR NOT pg_catalog.has_column_privilege(
        CURRENT_USER, 'public.oracle_interactions', 'created_at', 'SELECT'
    ) OR NOT pg_catalog.has_any_column_privilege(
        CURRENT_USER, 'public.oracle_interactions', 'UPDATE'
    ) THEN
        RAISE EXCEPTION 'STOP: R6D source-lock minimum privileges missing';
    END IF;

    -- Retain accepted source-lock body and bind the approved DB name
    -- as a SQL literal at installation, not via runtime trust in a GUC.
    EXECUTE pg_catalog.format($r6d_lock_function$
CREATE FUNCTION public._r6d_lock_review_source(p_interaction uuid, p_user uuid, p_question text, p_created timestamp with time zone)
 RETURNS boolean
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'pg_temp'
AS $function$
BEGIN
    IF current_database() <> %L
       OR current_user <> 'godinc_r6d_lock_owner'
    THEN
        RAISE EXCEPTION 'STOP: R6D source-lock authority mismatch'
            USING ERRCODE = '42501';
    END IF;
    IF p_interaction IS NULL OR p_user IS NULL
       OR p_question IS NULL OR p_created IS NULL
    THEN
        RETURN false;
    END IF;
    PERFORM 1
      FROM public.oracle_interactions AS i
     WHERE i.id = p_interaction
       AND i.user_id = p_user
       AND i.question_text = p_question
       AND i.created_at = p_created
     FOR UPDATE OF i;
    RETURN FOUND;
END
$function$
;
$r6d_lock_function$, pg_catalog.current_database());
END;
$r6d_lock_install$;

REVOKE ALL ON FUNCTION
    public._r6d_lock_review_source(uuid,uuid,text,timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
    public._r6d_lock_review_source(uuid,uuid,text,timestamptz)
    TO godinc_r6d_cache_writer;
COMMIT;
