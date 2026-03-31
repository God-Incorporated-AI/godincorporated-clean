BEGIN;

INSERT INTO plan_catalog (
    plan_code,
    display_name,
    rank_order,
    is_free_plan,
    monthly_price_cents,
    annual_prepaid_price_cents,
    annual_term_days,
    monthly_enabled,
    annual_enabled,
    question_limit,
    memory_depth,
    is_unlimited_questions,
    active
)
VALUES
    ('anon',        'Anon',        0, TRUE,  NULL, NULL, 365, FALSE, FALSE, 9,      1,    FALSE, TRUE),
    ('pilgrim',     'Pilgrim',     1, TRUE,  NULL, NULL, 365, FALSE, FALSE, 1,      1,    FALSE, TRUE),
    ('seeker',      'Seeker',      3, FALSE, 99,   999,  365, TRUE,  TRUE,  33,     3,    FALSE, TRUE),
    ('magister',    'Magister',    5, FALSE, 499,  4999, 365, TRUE,  TRUE,  144,    7,    FALSE, TRUE),
    ('sovereign',   'Sovereign',   7, FALSE, 999,  8999, 365, TRUE,  TRUE,  333,    9,    FALSE, TRUE),
    ('philosophus', 'Philosophus', 9, FALSE, 1999, 11900,365, TRUE,  TRUE,  999999, 33,   TRUE,  TRUE),
    ('theoricus',   'Theoricus',   10,FALSE, 3300, 19900,365, TRUE,  TRUE,  999999, NULL, TRUE,  TRUE)
ON CONFLICT (plan_code) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    rank_order = EXCLUDED.rank_order,
    is_free_plan = EXCLUDED.is_free_plan,
    monthly_price_cents = EXCLUDED.monthly_price_cents,
    annual_prepaid_price_cents = EXCLUDED.annual_prepaid_price_cents,
    annual_term_days = EXCLUDED.annual_term_days,
    monthly_enabled = EXCLUDED.monthly_enabled,
    annual_enabled = EXCLUDED.annual_enabled,
    question_limit = EXCLUDED.question_limit,
    memory_depth = EXCLUDED.memory_depth,
    is_unlimited_questions = EXCLUDED.is_unlimited_questions,
    active = EXCLUDED.active,
    updated_at = NOW();

COMMIT;
