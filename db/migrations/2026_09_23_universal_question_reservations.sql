-- God Incorporated: universal ordinary-question reservation kinds
--
-- Extends the accepted Phase 11.10R oracle_pending_inferences operational
-- rail so ordinary server inference may reserve question allowance alongside
-- existing device/PCC and browser-realtime inference.
--
-- This migration changes no runtime behavior, performs no backfill, and
-- deliberately preserves the existing device_inference column default.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $preflight$
DECLARE
    reservation_kind_definition text;
    reservation_kind_default text;
BEGIN
    IF to_regclass('public.oracle_pending_inferences') IS NULL THEN
        RAISE EXCEPTION
            'STOP: missing public.oracle_pending_inferences';
    END IF;

    SELECT column_default
    INTO reservation_kind_default
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'oracle_pending_inferences'
      AND column_name = 'reservation_kind';

    IF reservation_kind_default IS NULL THEN
        RAISE EXCEPTION
            'STOP: oracle_pending_inferences.reservation_kind is missing or has no default';
    END IF;

    IF position(
        'device_inference'
        IN reservation_kind_default
    ) = 0 THEN
        RAISE EXCEPTION
            'STOP: unexpected reservation_kind default: %',
            reservation_kind_default;
    END IF;

    SELECT pg_get_constraintdef(oid)
    INTO reservation_kind_definition
    FROM pg_constraint
    WHERE conrelid =
          'public.oracle_pending_inferences'::regclass
      AND conname =
          'oracle_pending_inferences_reservation_kind_check'
      AND contype = 'c';

    IF reservation_kind_definition IS NULL THEN
        RAISE EXCEPTION
            'STOP: expected reservation-kind CHECK constraint is missing';
    END IF;

    IF position(
        'server_inference'
        IN reservation_kind_definition
    ) > 0 THEN
        RAISE EXCEPTION
            'STOP: server_inference is already permitted; inspect before migration';
    END IF;

    IF position(
        'device_inference'
        IN reservation_kind_definition
    ) = 0
       OR position(
           'browser_realtime'
           IN reservation_kind_definition
       ) = 0 THEN
        RAISE EXCEPTION
            'STOP: unexpected reservation-kind CHECK definition: %',
            reservation_kind_definition;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.oracle_pending_inferences
        WHERE reservation_kind NOT IN (
            'device_inference',
            'browser_realtime'
        )
    ) THEN
        RAISE EXCEPTION
            'STOP: unexpected existing reservation_kind values found';
    END IF;
END;
$preflight$;


ALTER TABLE public.oracle_pending_inferences
    DROP CONSTRAINT
        oracle_pending_inferences_reservation_kind_check;


ALTER TABLE public.oracle_pending_inferences
    ADD CONSTRAINT
        oracle_pending_inferences_reservation_kind_check
    CHECK (
        reservation_kind IN (
            'device_inference',
            'browser_realtime',
            'server_inference'
        )
    );


COMMENT ON COLUMN
    public.oracle_pending_inferences.reservation_kind
IS
    'Short-lived Oracle reservation class: device_inference, browser_realtime, or server_inference. Runtime quota policy determines which active reservations consume question capacity.';


COMMIT;
