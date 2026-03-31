BEGIN;

UPDATE users
SET plan_code = 'anon'
WHERE plan_code = 'dormant';

ALTER TABLE users
ALTER COLUMN plan_code SET DEFAULT 'anon';

COMMIT;
