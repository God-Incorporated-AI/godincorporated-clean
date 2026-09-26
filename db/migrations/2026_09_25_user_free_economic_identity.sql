-- Phase 11.10R/App Store admission authority
-- Give each registered account one durable free-economic authority.
-- Existing users are intentionally NOT backfilled here. Each environment
-- must be reconciled from its own historical identity data before cutover.

BEGIN;

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS free_economic_identity_id varchar;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'users_free_economic_identity_id_fkey'
          AND conrelid = 'public.users'::regclass
    ) THEN
        ALTER TABLE public.users
            ADD CONSTRAINT
                users_free_economic_identity_id_fkey
            FOREIGN KEY (
                free_economic_identity_id
            )
            REFERENCES public.anonymous_users(id)
            ON DELETE RESTRICT;
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS
    idx_users_free_economic_identity_id
ON public.users (
    free_economic_identity_id
)
WHERE free_economic_identity_id IS NOT NULL;

COMMENT ON COLUMN
    public.users.free_economic_identity_id
IS
    'Durable free-economic authority for this account. '
    'Separate from personal-content/browser continuity.';

COMMIT;
