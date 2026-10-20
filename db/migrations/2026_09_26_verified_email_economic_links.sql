-- God Incorporated: verified-email economic link authority
-- Canonical forward migration; execute separately per environment.
--
-- Replaces the unused scalar anonymous_users.verified_email_hmac placeholder
-- with an append-only many-to-many association between the durable free
-- economic identity and pseudonymous evidence of verified email addresses.
--
-- This migration:
--   * stores no raw email address;
--   * performs no HMAC generation or verification;
--   * performs no account deletion;
--   * performs no user/economic-identity reconciliation;
--   * performs no historical backfill;
--   * changes no free-usage counters;
--   * does not create a second economic identity authority.
--
-- anonymous_users remains the durable free-economic authority.
-- Multiple verified emails may be associated with one economic identity.
-- One verified-email HMAC may be associated with multiple economic identities.
--
-- HMAC key and normalization versions are retained explicitly so future key
-- rotation or normalization changes never require interpreting old hashes
-- under an unknown contract.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $preflight$
DECLARE
    populated_hmac_count bigint;
BEGIN
    IF to_regclass(
        'public.verified_email_economic_links'
    ) IS NOT NULL THEN
        RAISE EXCEPTION
            'STOP: verified_email_economic_links already exists; inspect first';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_attribute
        WHERE attrelid =
            'public.anonymous_users'::regclass
          AND attnum > 0
          AND NOT attisdropped
          AND attname = 'verified_email_hmac'
    ) THEN
        RAISE EXCEPTION
            'STOP: anonymous_users.verified_email_hmac is missing';
    END IF;

    SELECT COUNT(*)
    INTO populated_hmac_count
    FROM public.anonymous_users
    WHERE verified_email_hmac IS NOT NULL;

    IF populated_hmac_count <> 0 THEN
        RAISE EXCEPTION
            'STOP: anonymous_users.verified_email_hmac has % populated rows',
            populated_hmac_count;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid =
            'public.anonymous_users'::regclass
          AND conname =
            'anonymous_users_email_hmac_format'
    ) THEN
        RAISE EXCEPTION
            'STOP: expected verified-email HMAC constraint is missing';
    END IF;

    IF to_regclass(
        'public.idx_anonymous_users_verified_email_hmac'
    ) IS NULL THEN
        RAISE EXCEPTION
            'STOP: expected verified-email HMAC index is missing';
    END IF;
END;
$preflight$;

CREATE TABLE public.verified_email_economic_links (
    economic_identity_id varchar NOT NULL,
    verified_email_hmac text NOT NULL,
    hmac_key_version text NOT NULL,
    email_normalization_version text NOT NULL,
    first_verified_at timestamptz NOT NULL DEFAULT now(),
    last_verified_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT verified_email_economic_links_pkey
        PRIMARY KEY (
            economic_identity_id,
            hmac_key_version,
            email_normalization_version,
            verified_email_hmac
        ),

    CONSTRAINT verified_email_economic_links_identity_fkey
        FOREIGN KEY (economic_identity_id)
        REFERENCES public.anonymous_users(id)
        ON DELETE RESTRICT,

    CONSTRAINT verified_email_economic_links_hmac_format
        CHECK (
            verified_email_hmac ~ '^[0-9a-f]{64}$'
        ),

    CONSTRAINT verified_email_economic_links_key_version_check
        CHECK (
            length(btrim(hmac_key_version)) > 0
        ),

    CONSTRAINT verified_email_economic_links_normalization_version_check
        CHECK (
            length(
                btrim(email_normalization_version)
            ) > 0
        ),

    CONSTRAINT verified_email_economic_links_time_check
        CHECK (
            last_verified_at >= first_verified_at
        )
);

CREATE INDEX idx_verified_email_economic_links_hmac
    ON public.verified_email_economic_links (
        hmac_key_version,
        email_normalization_version,
        verified_email_hmac
    );

COMMENT ON TABLE
    public.verified_email_economic_links
IS
    'Pseudonymous verified-email evidence attached to the durable free-economic identity. Contains no raw email and is not an authentication authority.';

COMMENT ON COLUMN
    public.verified_email_economic_links.economic_identity_id
IS
    'Durable free-economic authority in anonymous_users; not an account identifier.';

COMMENT ON COLUMN
    public.verified_email_economic_links.verified_email_hmac
IS
    'HMAC-SHA256 of the normalized verified email under the named key version; pseudonymous, not anonymous.';

COMMENT ON COLUMN
    public.verified_email_economic_links.hmac_key_version
IS
    'Identifier for the approved eligibility-HMAC key used to produce verified_email_hmac.';

COMMENT ON COLUMN
    public.verified_email_economic_links.email_normalization_version
IS
    'Identifier for the canonical email-normalization contract applied before HMAC generation.';

DROP INDEX public.idx_anonymous_users_verified_email_hmac;

ALTER TABLE public.anonymous_users
    DROP CONSTRAINT anonymous_users_email_hmac_format;

ALTER TABLE public.anonymous_users
    DROP COLUMN verified_email_hmac;

COMMIT;
