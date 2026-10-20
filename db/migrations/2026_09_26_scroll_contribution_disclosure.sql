-- God Incorporated: scroll contribution disclosure authority
--
-- Records which contribution disclosure governed a scroll at upload
-- acceptance time.
--
-- NULL means legacy/unversioned. It MUST NOT be interpreted as consent.
-- No historical scroll or ingestion job is backfilled by this migration.
--
-- ingestion_jobs carries the acceptance-time disclosure across queued
-- processing. scrolls stores the durable per-scroll authority.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $preflight$
BEGIN
    IF to_regclass('public.scrolls') IS NULL THEN
        RAISE EXCEPTION
            'STOP: public.scrolls does not exist';
    END IF;

    IF to_regclass('public.ingestion_jobs') IS NULL THEN
        RAISE EXCEPTION
            'STOP: public.ingestion_jobs does not exist';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'scrolls'
          AND column_name =
              'contribution_disclosure_version'
    ) THEN
        RAISE EXCEPTION
            'STOP: scrolls contribution disclosure column already exists';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'ingestion_jobs'
          AND column_name =
              'contribution_disclosure_version'
    ) THEN
        RAISE EXCEPTION
            'STOP: ingestion_jobs contribution disclosure column already exists';
    END IF;
END
$preflight$;

ALTER TABLE public.scrolls
    ADD COLUMN contribution_disclosure_version text,
    ADD CONSTRAINT
        scrolls_contribution_disclosure_version_nonblank
        CHECK (
            contribution_disclosure_version IS NULL
            OR length(
                btrim(contribution_disclosure_version)
            ) > 0
        );

ALTER TABLE public.ingestion_jobs
    ADD COLUMN contribution_disclosure_version text,
    ADD CONSTRAINT
        ingestion_jobs_contribution_disclosure_version_nonblank
        CHECK (
            contribution_disclosure_version IS NULL
            OR length(
                btrim(contribution_disclosure_version)
            ) > 0
        );

COMMENT ON COLUMN
    public.scrolls.contribution_disclosure_version IS
    'Contribution disclosure recorded at upload acceptance. NULL means legacy/unversioned and is not evidence of consent.';

COMMENT ON COLUMN
    public.ingestion_jobs.contribution_disclosure_version IS
    'Acceptance-time contribution disclosure carried through queued ingestion. NULL means legacy/unversioned.';

COMMIT;
