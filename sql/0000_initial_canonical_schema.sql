-- Enable UUID support
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =====================
-- USERS
-- =====================
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    display_name TEXT UNIQUE,
    status TEXT CHECK (status IN ('named', 'registered')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =====================
-- SESSIONS
-- =====================
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ip_hash TEXT,
    user_agent TEXT,
    metadata JSONB
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);

-- =====================
-- SCROLLS (CANONICAL CORPUS)
-- =====================
CREATE TABLE IF NOT EXISTS scrolls (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL REFERENCES sessions(id),
    user_id UUID REFERENCES users(id),

    source_type TEXT CHECK (source_type IN ('text', 'file')),
    original_filename TEXT,
    mime_type TEXT,
    storage_ref TEXT,

    content_text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    word_count INTEGER,

    tags JSONB,
    status TEXT CHECK (status IN ('active', 'hidden', 'rejected')) DEFAULT 'active',

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scrolls_created_at ON scrolls(created_at);
CREATE INDEX IF NOT EXISTS idx_scrolls_user_id ON scrolls(user_id);
CREATE INDEX IF NOT EXISTS idx_scrolls_session_id ON scrolls(session_id);
CREATE INDEX IF NOT EXISTS idx_scrolls_content_hash ON scrolls(content_hash);

-- =====================
-- ORACLE INTERACTIONS
-- =====================
CREATE TABLE IF NOT EXISTS oracle_interactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL REFERENCES sessions(id),
    user_id UUID REFERENCES users(id),

    input_type TEXT CHECK (input_type IN ('text', 'voice')),
    question_text TEXT NOT NULL,
    response_text TEXT NOT NULL,

    model_provider TEXT,
    model_name TEXT,
    mode TEXT,
    confidence NUMERIC,
    reason TEXT,
    client_interaction_id TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_oracle_created_at ON oracle_interactions(created_at);
CREATE INDEX IF NOT EXISTS idx_oracle_session_id ON oracle_interactions(session_id);
CREATE INDEX IF NOT EXISTS idx_oracle_user_id ON oracle_interactions(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_oracle_interactions_client_interaction_id
    ON oracle_interactions (client_interaction_id)
    WHERE client_interaction_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_oracle_interactions_metadata_input_mode
    ON oracle_interactions ((metadata_json->>'input_mode'));

-- =====================
-- PENDING ORACLE INFERENCES
-- =====================
-- Short-lived server-owned state for provider-neutral split-phase inference.
-- Completed dialogue belongs in oracle_interactions, not this table.

CREATE TABLE IF NOT EXISTS oracle_pending_inferences (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    session_id UUID NOT NULL,
    user_id UUID NULL,

    deity TEXT NOT NULL
        CHECK (deity IN ('Hathor', 'Moses')),

    input_mode TEXT NOT NULL
        CHECK (input_mode IN ('text', 'voice')),

    status TEXT NOT NULL DEFAULT 'prepared'
        CHECK (status IN ('prepared', 'completed', 'expired')),

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

-- =====================
-- DONATIONS
-- =====================
CREATE TABLE IF NOT EXISTS donations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID REFERENCES sessions(id),
    user_id UUID REFERENCES users(id),

    provider TEXT,
    provider_event_id TEXT UNIQUE,

    amount NUMERIC NOT NULL,
    currency TEXT NOT NULL,

    billing_period TEXT CHECK (billing_period IN ('one_time', 'monthly')),
    status TEXT CHECK (status IN ('pending', 'succeeded', 'failed', 'refunded')),
    attribution TEXT CHECK (attribution IN ('anonymous', 'named', 'registered')),

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =====================
-- AUDIT EVENTS
-- =====================
CREATE TABLE IF NOT EXISTS audit_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_type TEXT,
    actor_type TEXT,
    actor_id UUID,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
