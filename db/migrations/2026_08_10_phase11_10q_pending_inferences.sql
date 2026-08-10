-- Phase 11.10Q: provider-neutral split-phase inference state.
-- Stores short-lived server-owned preparation state before external/device
-- inference completes. Completed Oracle dialogue remains in oracle_interactions.

BEGIN;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS oracle_pending_inferences (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    session_id UUID NOT NULL,
    user_id UUID NULL,

    deity TEXT NOT NULL
        CHECK (deity IN ('Hathor', 'Moses')),

    input_mode TEXT NOT NULL
        CHECK (input_mode IN ('text', 'voice')),

    status TEXT NOT NULL DEFAULT 'prepared'
        CHECK (status IN ('prepared', 'completing', 'completed', 'expired')),

    prepared_state JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '15 minutes'),
    completed_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_oracle_pending_inferences_status_expires
ON oracle_pending_inferences(status, expires_at);

CREATE INDEX IF NOT EXISTS idx_oracle_pending_inferences_session
ON oracle_pending_inferences(session_id);

CREATE INDEX IF NOT EXISTS idx_oracle_pending_inferences_user
ON oracle_pending_inferences(user_id);

COMMIT;
