-- Phase 11.8C.1: Scroll ingestion job queue foundation
-- Safe to run more than once.
--
-- This creates the durable queue table for future background scroll ingestion.
-- It does not change /upload_scroll behavior yet.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),

    scroll_id uuid NULL REFERENCES scrolls(id) ON DELETE CASCADE,
    session_id uuid NULL,
    user_id uuid NULL,

    job_type text NOT NULL DEFAULT 'scroll_upload',
    status text NOT NULL DEFAULT 'queued',

    original_filename text NULL,
    storage_ref text NULL,
    mime_type text NULL,
    corpus_layer text NULL,

    error_message text NULL,
    attempts integer NOT NULL DEFAULT 0,

    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz NULL,
    finished_at timestamptz NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status_created_at
    ON ingestion_jobs(status, created_at);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_job_type_status
    ON ingestion_jobs(job_type, status);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_scroll_id
    ON ingestion_jobs(scroll_id);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_user_id
    ON ingestion_jobs(user_id);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_session_id
    ON ingestion_jobs(session_id);
