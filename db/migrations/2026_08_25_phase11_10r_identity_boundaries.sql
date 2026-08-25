-- Phase 11.10R: separate anonymous browser identity from conversation identity.
--
-- anonymous_user_id = persistent anonymous/browser identity
-- session_id        = Oracle conversation identity
-- user_id           = authenticated seeker identity
--
-- This migration is additive. Runtime authority moves in a separate commit
-- after the schema is present and verified.

BEGIN;

ALTER TABLE sessions
ADD COLUMN IF NOT EXISTS anonymous_user_id VARCHAR NULL;

ALTER TABLE oracle_interactions
ADD COLUMN IF NOT EXISTS anonymous_user_id VARCHAR NULL;

ALTER TABLE scrolls
ADD COLUMN IF NOT EXISTS anonymous_user_id VARCHAR NULL;

ALTER TABLE scroll_associations
ADD COLUMN IF NOT EXISTS anonymous_user_id VARCHAR NULL;

ALTER TABLE ingestion_jobs
ADD COLUMN IF NOT EXISTS anonymous_user_id VARCHAR NULL;


-- Historical rows were created while session_id also represented the
-- persistent anonymous/browser UUID. Preserve that identity only when the
-- value corresponds to a real anonymous_users row.

UPDATE sessions s
SET anonymous_user_id = a.id
FROM anonymous_users a
WHERE s.anonymous_user_id IS NULL
  AND s.id::text = a.id::text;

UPDATE oracle_interactions oi
SET anonymous_user_id = a.id
FROM anonymous_users a
WHERE oi.anonymous_user_id IS NULL
  AND oi.session_id::text = a.id::text;

UPDATE scrolls s
SET anonymous_user_id = a.id
FROM anonymous_users a
WHERE s.anonymous_user_id IS NULL
  AND s.session_id::text = a.id::text;

UPDATE scroll_associations sa
SET anonymous_user_id = a.id
FROM anonymous_users a
WHERE sa.anonymous_user_id IS NULL
  AND sa.session_id::text = a.id::text;

UPDATE ingestion_jobs ij
SET anonymous_user_id = a.id
FROM anonymous_users a
WHERE ij.anonymous_user_id IS NULL
  AND ij.session_id::text = a.id::text;


-- Bind persistent anonymous identity to the existing anonymous_users
-- authority without requiring every historical row to have one.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'sessions_anonymous_user_id_fkey'
    ) THEN
        ALTER TABLE sessions
        ADD CONSTRAINT sessions_anonymous_user_id_fkey
        FOREIGN KEY (anonymous_user_id)
        REFERENCES anonymous_users(id)
        ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'oracle_interactions_anonymous_user_id_fkey'
    ) THEN
        ALTER TABLE oracle_interactions
        ADD CONSTRAINT oracle_interactions_anonymous_user_id_fkey
        FOREIGN KEY (anonymous_user_id)
        REFERENCES anonymous_users(id)
        ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'scrolls_anonymous_user_id_fkey'
    ) THEN
        ALTER TABLE scrolls
        ADD CONSTRAINT scrolls_anonymous_user_id_fkey
        FOREIGN KEY (anonymous_user_id)
        REFERENCES anonymous_users(id)
        ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'scroll_associations_anonymous_user_id_fkey'
    ) THEN
        ALTER TABLE scroll_associations
        ADD CONSTRAINT scroll_associations_anonymous_user_id_fkey
        FOREIGN KEY (anonymous_user_id)
        REFERENCES anonymous_users(id)
        ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ingestion_jobs_anonymous_user_id_fkey'
    ) THEN
        ALTER TABLE ingestion_jobs
        ADD CONSTRAINT ingestion_jobs_anonymous_user_id_fkey
        FOREIGN KEY (anonymous_user_id)
        REFERENCES anonymous_users(id)
        ON DELETE SET NULL;
    END IF;
END
$$;


CREATE INDEX IF NOT EXISTS idx_sessions_anonymous_user_id
ON sessions (anonymous_user_id)
WHERE anonymous_user_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_oracle_interactions_anonymous_user_id
ON oracle_interactions (anonymous_user_id)
WHERE anonymous_user_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_scrolls_anonymous_user_id
ON scrolls (anonymous_user_id)
WHERE anonymous_user_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_scroll_assoc_anonymous_user_id
ON scroll_associations (anonymous_user_id)
WHERE anonymous_user_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_scroll_assoc_scroll_anon_unique
ON scroll_associations (scroll_id, anonymous_user_id)
WHERE anonymous_user_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_anonymous_user_id
ON ingestion_jobs (anonymous_user_id)
WHERE anonymous_user_id IS NOT NULL;

COMMIT;
