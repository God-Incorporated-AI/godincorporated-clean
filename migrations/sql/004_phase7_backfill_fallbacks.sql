BEGIN;

WITH scroll_totals AS (
    SELECT
        sa.user_id,
        COUNT(DISTINCT sa.scroll_id) AS total_scrolls
    FROM scroll_associations sa
    WHERE sa.user_id IS NOT NULL
    GROUP BY sa.user_id
)
UPDATE users u
SET
    scroll_floor_plan_code = CASE
        WHEN COALESCE(st.total_scrolls, 0) >= 99 THEN 'magister'
        WHEN COALESCE(st.total_scrolls, 0) >= 9 THEN 'seeker'
        WHEN COALESCE(st.total_scrolls, 0) >= 1 THEN 'pilgrim'
        ELSE NULL
    END,
    fallback_floor_plan_code = CASE
        WHEN COALESCE(st.total_scrolls, 0) >= 99 THEN 'magister'
        WHEN COALESCE(st.total_scrolls, 0) >= 9 THEN 'seeker'
        WHEN COALESCE(st.total_scrolls, 0) >= 1 THEN 'pilgrim'
        ELSE 'pilgrim'
    END
FROM scroll_totals st
WHERE u.id = st.user_id;

UPDATE users
SET
    fallback_floor_plan_code = 'pilgrim'
WHERE fallback_floor_plan_code IS NULL
  AND email_verified = TRUE;

UPDATE users
SET
    donor_floor_plan_code = CASE
        WHEN highest_paid_plan_ever IN ('magister', 'sovereign', 'philosophus', 'theoricus') THEN 'seeker'
        WHEN highest_paid_plan_ever = 'seeker' THEN 'pilgrim'
        ELSE donor_floor_plan_code
    END;

UPDATE users
SET
    renewal_offer_plan_code = COALESCE(last_paid_plan_code, renewal_offer_plan_code);

COMMIT;
