-- Phase 11.9D.1
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Library artifact foundation.
--
-- Purpose:
-- Preserve seeker-visible upload artifacts separately from deduped scroll
-- retrieval content.
--
-- library_uploads = the seeker-visible uploaded artifact
-- scrolls = deduped corpus/retrieval record
-- scroll_chunks = retrieval chunks and embeddings
-- ingestion_jobs = background processing job

CREATE TABLE IF NOT EXISTS library_uploads (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),

    session_id uuid NULL REFERENCES sessions(id) ON DELETE SET NULL,
    anonymous_user_id character varying NULL REFERENCES anonymous_users(id) ON DELETE SET NULL,
    user_id uuid NULL REFERENCES users(id) ON DELETE SET NULL,

    ingestion_job_id uuid NULL REFERENCES ingestion_jobs(id) ON DELETE SET NULL,
    scroll_id uuid NULL REFERENCES scrolls(id) ON DELETE SET NULL,

    original_filename text NOT NULL,
    mime_type text NULL,
    file_size_bytes bigint NULL,

    storage_ref text NULL,
    storage_backend text NULL,

    file_sha256 text NULL,
    content_hash text NULL,

    seeker_status text NOT NULL DEFAULT 'received',
    admin_status text NULL,
    dedupe_kind text NULL,

    metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,

    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),

    CONSTRAINT library_uploads_seeker_status_check
        CHECK (
            seeker_status IN (
                'received',
                'saved',
                'queued',
                'reading',
                'ready',
                'needs_ocr',
                'failed',
                'already_saved',
                'indexing_deferred'
            )
        ),

    CONSTRAINT library_uploads_dedupe_kind_check
        CHECK (
            dedupe_kind IS NULL
            OR dedupe_kind IN (
                'none',
                'exact_byte',
                'content_hash',
                'canonical_match',
                'legacy_duplicate_not_preserved'
            )
        )
);

CREATE INDEX IF NOT EXISTS idx_library_uploads_user_created
    ON library_uploads (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_library_uploads_anonymous_user_created
    ON library_uploads (anonymous_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_library_uploads_session_created
    ON library_uploads (session_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_library_uploads_ingestion_job
    ON library_uploads (ingestion_job_id);

CREATE INDEX IF NOT EXISTS idx_library_uploads_scroll
    ON library_uploads (scroll_id);

CREATE INDEX IF NOT EXISTS idx_library_uploads_seeker_status
    ON library_uploads (seeker_status);

CREATE INDEX IF NOT EXISTS idx_library_uploads_dedupe_kind
    ON library_uploads (dedupe_kind);

CREATE INDEX IF NOT EXISTS idx_library_uploads_file_sha256
    ON library_uploads (file_sha256);

CREATE INDEX IF NOT EXISTS idx_library_uploads_content_hash
    ON library_uploads (content_hash);

CREATE INDEX IF NOT EXISTS idx_library_uploads_storage_backend
    ON library_uploads (storage_backend);

CREATE INDEX IF NOT EXISTS idx_library_uploads_created_at
    ON library_uploads (created_at DESC);
