-- Phase 11.8D.2: Reporting and alerting foundation
-- Safe to run more than once.
--
-- Creates private operational tables for scheduled reports, report artifacts,
-- red-flag alerts, and notification delivery records.
--
-- This migration does not send email, expose reports, or change public behavior.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS report_artifacts (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),

    report_key text NOT NULL,
    environment text NOT NULL,
    format text NOT NULL,

    storage_ref text NULL,
    sha256 text NULL,
    size_bytes integer NULL,

    summary_json jsonb NOT NULL DEFAULT '{}'::jsonb,

    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS report_runs (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),

    report_key text NOT NULL,
    environment text NOT NULL,
    status text NOT NULL DEFAULT 'queued',

    period_start timestamptz NULL,
    period_end timestamptz NULL,

    started_at timestamptz NULL,
    finished_at timestamptz NULL,

    error_message text NULL,
    artifact_id uuid NULL REFERENCES report_artifacts(id) ON DELETE SET NULL,

    git_sha text NULL,
    metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,

    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alert_events (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),

    alert_key text NOT NULL,
    fingerprint text NOT NULL,
    environment text NOT NULL,

    severity text NOT NULL,
    status text NOT NULL DEFAULT 'open',

    title text NOT NULL,
    message text NULL,
    metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,

    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    count integer NOT NULL DEFAULT 1,

    resolved_at timestamptz NULL,
    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT alert_events_unique_open_fingerprint
        UNIQUE (alert_key, fingerprint, environment)
);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),

    alert_event_id uuid NULL REFERENCES alert_events(id) ON DELETE CASCADE,

    channel text NOT NULL,
    recipient text NULL,
    status text NOT NULL,

    error_message text NULL,
    sent_at timestamptz NULL,

    metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,

    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_report_artifacts_key_env_created
    ON report_artifacts(report_key, environment, created_at);

CREATE INDEX IF NOT EXISTS idx_report_runs_key_env_created
    ON report_runs(report_key, environment, created_at);

CREATE INDEX IF NOT EXISTS idx_report_runs_status_created
    ON report_runs(status, created_at);

CREATE INDEX IF NOT EXISTS idx_alert_events_status_severity
    ON alert_events(status, severity);

CREATE INDEX IF NOT EXISTS idx_alert_events_key_env
    ON alert_events(alert_key, environment);

CREATE INDEX IF NOT EXISTS idx_alert_events_last_seen
    ON alert_events(last_seen_at);

CREATE INDEX IF NOT EXISTS idx_notification_deliveries_alert_event
    ON notification_deliveries(alert_event_id);

CREATE INDEX IF NOT EXISTS idx_notification_deliveries_status_created
    ON notification_deliveries(status, created_at);
