-- Phase 10.5: Cost, TTS, and Mobile App Readiness
-- Adds persistent usage/cost/timing event tables.
-- Safe to run more than once.

CREATE TABLE IF NOT EXISTS oracle_usage_events (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at timestamptz NOT NULL DEFAULT now(),

    user_id uuid NULL REFERENCES users(id) ON DELETE SET NULL,
    anonymous_user_id varchar NULL,
    session_id uuid NULL,

    plan_code text NULL,
    usage_class text NULL,
    input_mode text NULL,
    deity text NULL,

    provider text NULL,
    model text NULL,

    retrieval_backend text NULL,
    pgvector_limit integer NULL,

    prompt_tokens integer NULL,
    completion_tokens integer NULL,
    total_tokens integer NULL,

    estimated_input_tokens integer NULL,
    estimated_output_tokens integer NULL,
    estimated_total_tokens integer NULL,

    question_chars integer NULL,
    enhanced_question_chars integer NULL,
    answer_chars integer NULL,

    final_model_ms numeric NULL,
    total_ms numeric NULL,

    estimated_cost_usd numeric(12, 8) NULL,

    metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_oracle_usage_created_at
    ON oracle_usage_events(created_at);

CREATE INDEX IF NOT EXISTS idx_oracle_usage_user_id
    ON oracle_usage_events(user_id);

CREATE INDEX IF NOT EXISTS idx_oracle_usage_session_id
    ON oracle_usage_events(session_id);

CREATE INDEX IF NOT EXISTS idx_oracle_usage_plan_code
    ON oracle_usage_events(plan_code);

CREATE INDEX IF NOT EXISTS idx_oracle_usage_deity
    ON oracle_usage_events(deity);

CREATE INDEX IF NOT EXISTS idx_oracle_usage_input_mode
    ON oracle_usage_events(input_mode);

CREATE INDEX IF NOT EXISTS idx_oracle_usage_provider_model
    ON oracle_usage_events(provider, model);


CREATE TABLE IF NOT EXISTS voice_usage_events (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at timestamptz NOT NULL DEFAULT now(),

    user_id uuid NULL REFERENCES users(id) ON DELETE SET NULL,
    anonymous_user_id varchar NULL,
    session_id uuid NULL,

    plan_code text NULL,
    input_mode text NULL,
    deity text NULL,

    stage text NOT NULL,
    status text NOT NULL,

    transcribe_ms numeric NULL,
    oracle_ms numeric NULL,
    tts_ms numeric NULL,
    total_ms numeric NULL,

    transcript_chars integer NULL,
    answer_chars integer NULL,
    audio_url_present boolean NULL,

    tts_provider text NULL,
    tts_model text NULL,
    tts_voice text NULL,

    estimated_tts_cost_usd numeric(12, 8) NULL,

    metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_voice_usage_created_at
    ON voice_usage_events(created_at);

CREATE INDEX IF NOT EXISTS idx_voice_usage_user_id
    ON voice_usage_events(user_id);

CREATE INDEX IF NOT EXISTS idx_voice_usage_session_id
    ON voice_usage_events(session_id);

CREATE INDEX IF NOT EXISTS idx_voice_usage_stage_status
    ON voice_usage_events(stage, status);

CREATE INDEX IF NOT EXISTS idx_voice_usage_deity
    ON voice_usage_events(deity);
