BEGIN;

CREATE TABLE public.account_delete_auth_handoffs (
    deleted_user_hmac char(64) PRIMARY KEY,

    economic_identity_id varchar NOT NULL
        REFERENCES public.anonymous_users(id)
        ON DELETE RESTRICT,

    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,

    CONSTRAINT account_delete_auth_handoffs_hmac
        CHECK (
            deleted_user_hmac
            ~ '^[0-9a-f]{64}$'
        ),

    CONSTRAINT account_delete_auth_handoffs_expiry
        CHECK (
            expires_at > created_at
        )
);

CREATE INDEX
    idx_account_delete_auth_handoffs_expires
ON public.account_delete_auth_handoffs (
    expires_at
);

CREATE INDEX
    idx_account_delete_auth_handoffs_economic
ON public.account_delete_auth_handoffs (
    economic_identity_id
);

COMMIT;
