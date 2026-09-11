-- Phase 11.10R: browser realtime ordinary-question reservations.
--
-- Generalizes provider-neutral pending inference state without changing
-- the existing PCC lifecycle contract.

BEGIN;

ALTER TABLE oracle_pending_inferences
ADD COLUMN IF NOT EXISTS anonymous_user_id VARCHAR NULL;

ALTER TABLE oracle_pending_inferences
ADD COLUMN IF NOT EXISTS client_interaction_id TEXT NULL;

ALTER TABLE oracle_pending_inferences
ADD COLUMN IF NOT EXISTS reservation_kind TEXT NOT NULL
DEFAULT 'device_inference';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'oracle_pending_inferences_reservation_kind_check'
    ) THEN
        ALTER TABLE oracle_pending_inferences
        ADD CONSTRAINT oracle_pending_inferences_reservation_kind_check
        CHECK (
            reservation_kind IN (
                'device_inference',
                'browser_realtime'
            )
        );
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_oracle_pending_inferences_anonymous
ON oracle_pending_inferences(anonymous_user_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_oracle_pending_inferences_client_interaction
ON oracle_pending_inferences(client_interaction_id)
WHERE client_interaction_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_oracle_pending_inferences_kind_status_expires
ON oracle_pending_inferences(reservation_kind, status, expires_at);

COMMIT;
