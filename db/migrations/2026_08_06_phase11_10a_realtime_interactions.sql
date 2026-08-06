-- Phase 11.10A: provider-neutral realtime interaction logging
-- Adds idempotent completed-turn memory fields to oracle_interactions.

BEGIN;

ALTER TABLE oracle_interactions
ADD COLUMN IF NOT EXISTS client_interaction_id TEXT;

ALTER TABLE oracle_interactions
ADD COLUMN IF NOT EXISTS metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE UNIQUE INDEX IF NOT EXISTS idx_oracle_interactions_client_interaction_id
ON oracle_interactions (client_interaction_id)
WHERE client_interaction_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_oracle_interactions_metadata_input_mode
ON oracle_interactions ((metadata_json->>'input_mode'));

COMMIT;
