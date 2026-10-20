-- God Incorporated: durable account-delete operation authority
-- Canonical forward migration; execute separately per environment.
--
-- A row in this table is durable proof that account deletion has been
-- authorized for the named user/economic identity before any irreversible
-- provider or storage side effect is attempted.
--
-- The row is intentionally account-scoped, contains no email/display name,
-- and is removed automatically when the users row is durably deleted.
-- A surviving row therefore marks an interrupted deletion that may be retried.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $preflight$
BEGIN
    IF to_regclass(
        'public.account_delete_operations'
    ) IS NOT NULL THEN
        RAISE EXCEPTION
            'STOP: account_delete_operations already exists; inspect first';
    END IF;

    IF to_regclass('public.users') IS NULL THEN
        RAISE EXCEPTION
            'STOP: public.users is missing';
    END IF;

    IF to_regclass('public.anonymous_users') IS NULL THEN
        RAISE EXCEPTION
            'STOP: public.anonymous_users is missing';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_extension
        WHERE extname = 'uuid-ossp'
    ) THEN
        RAISE EXCEPTION
            'STOP: uuid-ossp extension is missing';
    END IF;
END;
$preflight$;

CREATE TABLE public.account_delete_operations (
    user_id uuid PRIMARY KEY
        REFERENCES public.users(id)
        ON DELETE CASCADE,

    operation_id uuid NOT NULL
        DEFAULT uuid_generate_v4(),

    economic_identity_id varchar NOT NULL
        REFERENCES public.anonymous_users(id)
        ON DELETE RESTRICT,

    claimed_at timestamptz NOT NULL
        DEFAULT now(),

    last_attempt_at timestamptz NOT NULL
        DEFAULT now(),

    CONSTRAINT account_delete_operations_operation_id_key
        UNIQUE (operation_id),

    CONSTRAINT account_delete_operations_attempt_time
        CHECK (last_attempt_at >= claimed_at)
);

CREATE INDEX idx_account_delete_operations_economic
ON public.account_delete_operations (
    economic_identity_id
);

COMMENT ON TABLE
    public.account_delete_operations
IS
    'Durable account-deletion authority established before irreversible provider or storage side effects. A surviving row marks an interrupted deletion eligible for retry.';

COMMENT ON COLUMN
    public.account_delete_operations.user_id
IS
    'Account being deleted. Cascades only when the authoritative users row is durably deleted.';

COMMENT ON COLUMN
    public.account_delete_operations.operation_id
IS
    'Stable opaque deletion-operation identifier; contains no account PII.';

COMMENT ON COLUMN
    public.account_delete_operations.economic_identity_id
IS
    'Durable free-economic identity that must remain unchanged throughout deletion.';

COMMIT;
