-- God Incorporated H4G17M3CQ: first annual recurring payment classification.
-- Forward migration. Preserve all six historic kinds; add annual_initial only.
-- Execute inside a controlled transaction; historical migrations remain unchanged.
DO $h4g17_m3cq$
DECLARE
    old_definition text;
BEGIN
    SELECT pg_get_constraintdef(c.oid, true)
      INTO old_definition
      FROM pg_constraint c
     WHERE c.conrelid = 'public.billing_transactions'::regclass
       AND c.conname = 'billing_transactions_transaction_kind_check'
       AND c.contype = 'c'
       AND c.convalidated = true;
    IF old_definition IS DISTINCT FROM
       'CHECK (transaction_kind = ANY (ARRAY[''monthly_initial''::text, ''monthly_renewal''::text, ''annual_prepaid''::text, ''annual_renewal''::text, ''refund''::text, ''dispute''::text]))'
    THEN
        RAISE EXCEPTION 'STOP H4G17M3CQ: existing transaction-kind contract differs';
    END IF;
    ALTER TABLE public.billing_transactions
        DROP CONSTRAINT billing_transactions_transaction_kind_check;
    ALTER TABLE public.billing_transactions
        ADD CONSTRAINT billing_transactions_transaction_kind_check
        CHECK (transaction_kind IN (
            'monthly_initial', 'monthly_renewal', 'annual_prepaid',
            'annual_renewal', 'refund', 'dispute', 'annual_initial'
        ));
END;
$h4g17_m3cq$;
