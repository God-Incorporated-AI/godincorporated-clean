-- Phase 11.9C: final queued upload result payloads.
-- Safe to run repeatedly.

ALTER TABLE ingestion_jobs
    ADD COLUMN IF NOT EXISTS result_json jsonb NOT NULL DEFAULT '{}'::jsonb;
