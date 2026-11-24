-- =====================================================================
--  RescueLink :: 04_queries.sql
-- ---------------------------------------------------------------------
--  The analytical suite.  Every query is preceded by machine-readable
--  metadata markers; the web application parses this very file to build
--  the /analytics page, so the SQL shown in the report and the SQL that
--  actually runs can never drift apart.
--
--  Required technique coverage:
--    Q1  window functions  (RANK, SUM OVER, PARTITION BY)
--    Q2  CTE + ROW_NUMBER  (three-stage pipeline)
--    Q3  CTE + LAG         (month-over-month growth)
--    Q4  correlated subquery, NOT EXISTS
--    Q5  subquery in FROM
--    Q6  GROUP BY ... HAVING (quality)
--    Q7  GROUP BY ... HAVING (volunteer effort)
--    Q8  window function ROW_NUMBER (top-N per group)
--
--  Run with:  psql -f sql/04_queries.sql
-- =====================================================================

-- @query: donor_leaderboard
-- @title: Donor leaderboard — monthly rank and running total
-- @techniques: Window functions — RANK(), SUM() OVER (PARTITION BY ... ORDER BY ...)
-- @description: Who rescued the most food each month, and how is each donor trending over time? The inner CTE collapses 12,000 batches into one row per donor-month; the outer query ranks donors inside each month and keeps a per-donor running total of kg rescued.
WITH monthly AS (
    SELECT b.donor_org_id,
           o.name                                   AS donor_name,
           date_trunc('month', b.created_at)::date  AS month,
           sum(b.quantity_kg)                       AS rescued_kg,
           count(*)                                 AS batches
    FROM donation_batches b
    JOIN organizations o ON o.org_id = b.donor_org_id
    WHERE b.status IN ('delivered', 'claimed')
    GROUP BY b.donor_org_id, o.name, date_trunc('month', b.created_at)
)
SELECT month,
       donor_name,
       batches,
       rescued_kg,
       RANK() OVER (PARTITION BY month ORDER BY rescued_kg DESC)      AS rank_in_month,
       round(sum(rescued_kg) OVER (PARTITION BY donor_org_id
                                   ORDER BY month), 1)                AS running_total_kg
FROM monthly
WHERE month >= (date_trunc('month', current_date) - interval '3 months')
ORDER BY month DESC, rank_in_month
LIMIT 30;

-- @query: at_risk_pipeline
-- @title: At-risk batches matched to capable recipients
-- @techniques: Multi-stage CTE, ROW_NUMBER(), LEFT JOIN aggregation
-- @description: The operations query. Stage 1 finds available food expiring within 48 h (remaining kg is derived from allocations, never stored). Stage 2 measures each recipient's rolling 7-day intake per category. Stage 3 matches batches to recipients whose weekly capacity still has room, ranked so an operator sees the best three candidates per batch.
WITH at_risk AS (
    SELECT v.batch_id, v.category_id, v.donor_name, v.category_name,
           v.remaining_kg, v.expires_at, v.needs_refrigeration
    FROM v_batch_availability v
    WHERE v.status = 'available'
      AND v.remaining_kg > 0
      AND v.expires_at BETWEEN now() AND now() + interval '48 hours'
),
weekly_load AS (
    SELECT a.recipient_org_id, b.category_id, sum(a.allocated_kg) AS week_kg
    FROM batch_allocations a
    JOIN donation_batches b ON b.batch_id = a.batch_id
    WHERE a.status <> 'cancelled'
      AND a.allocated_at >= now() - interval '7 days'
    GROUP BY a.recipient_org_id, b.category_id
),
matches AS (
    SELECT ar.batch_id,
           ar.donor_name,
           ar.category_name,
           ar.remaining_kg,
           ar.expires_at,
           o.org_id                                        AS recipient_org_id,
           o.name                                          AS recipient_name,
           o.district,
           round(c.max_weekly_kg - COALESCE(w.week_kg, 0), 1) AS spare_weekly_kg,
           ROW_NUMBER() OVER (PARTITION BY ar.batch_id
                              ORDER BY (c.max_weekly_kg - COALESCE(w.week_kg, 0)) DESC) AS candidate_no
    FROM at_risk ar
    JOIN org_capabilities c ON c.category_id = ar.category_id
    JOIN organizations   o ON o.org_id = c.org_id AND o.is_active
    LEFT JOIN weekly_load w ON w.recipient_org_id = c.org_id
                           AND w.category_id = ar.category_id
    WHERE c.max_weekly_kg - COALESCE(w.week_kg, 0) >= LEAST(ar.remaining_kg, 20)
)
SELECT batch_id, donor_name, category_name, remaining_kg, expires_at,
       recipient_name, district, spare_weekly_kg, candidate_no
FROM matches
WHERE candidate_no <= 3
ORDER BY expires_at, batch_id, candidate_no;

-- @query: monthly_growth
-- @title: Month-over-month rescue volume growth
-- @techniques: CTE + LAG() window function
-- @description: Is the network growing? Compares each month with the previous one and computes the percentage change, guarding against division by zero with NULLIF.
WITH monthly AS (
    SELECT date_trunc('month', created_at)::date AS month,
           count(*)                              AS batches,
           sum(quantity_kg)                      AS rescued_kg
    FROM donation_batches
    WHERE status IN ('delivered', 'claimed')
    GROUP BY date_trunc('month', created_at)
)
SELECT month,
       batches,
       round(rescued_kg, 1)                            AS rescued_kg,
       round(rescued_kg - lag(rescued_kg) OVER w, 1)    AS delta_kg,
       round(100 * (rescued_kg - lag(rescued_kg) OVER w)
             / NULLIF(lag(rescued_kg) OVER w, 0), 1)    AS growth_pct
FROM monthly
WINDOW w AS (ORDER BY month)
ORDER BY month;

-- @query: underserved_recipients
-- @title: Recipients below their category average (correlated subquery)
-- @techniques: Correlated subquery in HAVING, aggregate comparison
-- @description: For every recipient/category pair, compares the kg actually received against the average for that same category across all recipients. The subquery is correlated: it re-evaluates per category_id from the outer GROUP BY.
SELECT o.name                     AS recipient,
       c.name                     AS category,
       round(sum(a.allocated_kg), 1) AS received_kg,
       (SELECT round(avg(t.total), 1)
          FROM (SELECT sum(a2.allocated_kg) AS total
                  FROM batch_allocations a2
                  JOIN donation_batches b2 ON b2.batch_id = a2.batch_id
                 WHERE b2.category_id = c.category_id
                   AND a2.status = 'handed_over'
                 GROUP BY a2.recipient_org_id) t) AS category_avg_kg
FROM batch_allocations a
JOIN donation_batches b ON b.batch_id = a.batch_id
JOIN food_categories  c ON c.category_id = b.category_id
JOIN organizations    o ON o.org_id = a.recipient_org_id
WHERE a.status = 'handed_over'
GROUP BY o.org_id, o.name, c.category_id, c.name
HAVING sum(a.allocated_kg) < (SELECT avg(t.total)
                                FROM (SELECT sum(a3.allocated_kg) AS total
                                        FROM batch_allocations a3
                                        JOIN donation_batches b3 ON b3.batch_id = a3.batch_id
                                       WHERE b3.category_id = c.category_id
                                         AND a3.status = 'handed_over'
                                       GROUP BY a3.recipient_org_id) t)
ORDER BY c.name, received_kg;

-- @query: dormant_donors
-- @title: Dormant donors (NOT EXISTS anti-join)
-- @techniques: Correlated NOT EXISTS subquery
-- @description: Active donors with no donation in the last 30 days — the retention list. Written as NOT EXISTS rather than NOT IN so that NULLs can never silently empty the result.
SELECT o.org_id,
       o.name,
       o.district,
       count(b.batch_id)        AS lifetime_batches,
       max(b.created_at)::date  AS last_donation
FROM organizations o
LEFT JOIN donation_batches b ON b.donor_org_id = o.org_id
WHERE o.org_type = 'donor'
  AND o.is_active
  AND NOT EXISTS (SELECT 1
                    FROM donation_batches recent
                   WHERE recent.donor_org_id = o.org_id
                     AND recent.created_at > now() - interval '30 days')
GROUP BY o.org_id, o.name, o.district
ORDER BY last_donation NULLS FIRST;

-- @query: fill_rate
-- @title: Fill rate per food category (subquery in FROM)
-- @techniques: Non-correlated subquery in FROM, filtered aggregate
-- @description: What share of each batch actually reached a recipient? The derived table computes per-batch fill percentage; the outer query aggregates it per category to expose categories that regularly go unclaimed.
SELECT x.category,
       count(*)                        AS batches,
       round(avg(x.fill_pct), 1)       AS avg_fill_pct,
       round(min(x.fill_pct), 1)       AS worst_fill_pct,
       round(sum(x.kg), 1)             AS offered_kg
FROM (
    SELECT b.batch_id,
           c.name AS category,
           b.quantity_kg AS kg,
           100.0 * COALESCE(sum(a.allocated_kg)
                            FILTER (WHERE a.status <> 'cancelled'), 0) / b.quantity_kg AS fill_pct
    FROM donation_batches b
    JOIN food_categories c ON c.category_id = b.category_id
    LEFT JOIN batch_allocations a ON a.batch_id = b.batch_id
    GROUP BY b.batch_id, c.name, b.quantity_kg
) AS x
GROUP BY x.category
ORDER BY avg_fill_pct, x.category;

-- @query: spoilage_by_category
-- @title: Spoilage rates above threshold (GROUP BY ... HAVING)
-- @techniques: Filtered aggregation (FILTER), GROUP BY ... HAVING
-- @description: Categories where more than 6% of batches spoiled and at least 20 batches exist. Uses COUNT(*) FILTER to compute two aggregates in one pass.
SELECT c.name                                              AS category,
       count(*)                                            AS batches,
       count(*) FILTER (WHERE b.status = 'spoiled')        AS spoiled,
       round(100.0 * count(*) FILTER (WHERE b.status = 'spoiled')
             / count(*), 1)                                AS spoil_pct,
       round(sum(b.quantity_kg) FILTER (WHERE b.status = 'spoiled'), 1) AS lost_kg
FROM donation_batches b
JOIN food_categories c ON c.category_id = b.category_id
GROUP BY c.name
HAVING count(*) >= 20
   AND 100.0 * count(*) FILTER (WHERE b.status = 'spoiled') / count(*) > 6.0
ORDER BY spoil_pct DESC;

-- @query: volunteer_leaderboard
-- @title: Volunteer effort leaderboard (HAVING)
-- @techniques: GROUP BY ... HAVING, multi-table join
-- @description: Couriers with at least five completed runs, ranked by logged hours. Only completed runs count, so the numbers are auditable.
SELECT u.full_name,
       count(*)                       AS completed_runs,
       round(sum(s.hours_logged), 1)  AS hours_logged,
       round(avg(s.hours_logged), 1)  AS avg_hours_per_run,
       count(*) FILTER (WHERE s.role_on_run = 'driver') AS drives
FROM shift_assignments s
JOIN users         u ON u.user_id = s.user_id
JOIN delivery_runs r ON r.run_id = s.run_id
WHERE r.status = 'completed'
GROUP BY u.user_id, u.full_name
HAVING count(*) >= 5
ORDER BY hours_logged DESC;

-- @query: top_donors_per_district
-- @title: Top two donors per district (ROW_NUMBER top-N-per-group)
-- @techniques: Window function ROW_NUMBER() PARTITION BY
-- @description: District-level outreach list. ROW_NUMBER restarts for every district so the outer filter can keep exactly the top two donors without a correlated subquery.
WITH rescued AS (
    SELECT o.org_id, o.name AS donor_name, o.district,
           sum(b.quantity_kg) AS rescued_kg,
           count(*)           AS batches
    FROM organizations o
    JOIN donation_batches b ON b.donor_org_id = o.org_id
    WHERE o.org_type = 'donor'
      AND b.status IN ('delivered', 'claimed')
    GROUP BY o.org_id, o.name, o.district
),
ranked AS (
    SELECT rescued.*,
           ROW_NUMBER() OVER (PARTITION BY district ORDER BY rescued_kg DESC, donor_name) AS rn,
           round(100.0 * rescued_kg / sum(rescued_kg) OVER (PARTITION BY district), 1) AS pct_of_district
    FROM rescued
)
SELECT district, rn, donor_name, batches, round(rescued_kg, 1) AS rescued_kg, pct_of_district
FROM ranked
WHERE rn <= 2
ORDER BY district, rn;
