-- Phase 11.10R: unified external provider cost for voice stages.
-- Existing estimated_tts_cost_usd remains for compatibility.

ALTER TABLE voice_usage_events
ADD COLUMN IF NOT EXISTS estimated_external_cost_usd
    numeric(12, 8) NULL;
