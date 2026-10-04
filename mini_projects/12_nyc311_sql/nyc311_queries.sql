-- What New Yorkers complain about, in plain SQL (SQLite dialect)
-- Run against nyc311.db: https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9

-- 1. What do people complain about?
SELECT complaint_type,
       COUNT(*)                                           AS requests,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS share_pct
FROM requests
GROUP BY complaint_type
ORDER BY requests DESC
LIMIT 12;


-- 2. How do the boroughs compare?
WITH borough_type AS (
    SELECT borough, complaint_type, COUNT(*) AS n
    FROM requests
    WHERE borough <> 'Unspecified'
    GROUP BY borough, complaint_type
),
ranked AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY borough ORDER BY n DESC) AS rn,
           SUM(n)       OVER (PARTITION BY borough)                 AS total,
           SUM(n)       OVER ()                                     AS grand_total
    FROM borough_type
)
SELECT borough,
       total                                   AS requests,
       ROUND(100.0 * total / grand_total, 1)   AS share_of_city_pct,
       complaint_type                          AS most_common_complaint,
       ROUND(100.0 * n / total, 1)             AS its_share_pct
FROM ranked
WHERE rn = 1
ORDER BY requests DESC;


-- 3. Which agencies respond fastest?
WITH durations AS (
    SELECT agency,
           (julianday(closed_date) - julianday(created_date)) * 24 AS hours
    FROM requests
    WHERE closed_date IS NOT NULL AND closed_date >= created_date
),
ranked AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY agency ORDER BY hours) AS rn,
           COUNT(*)     OVER (PARTITION BY agency)                AS n
    FROM durations
)
SELECT agency,
       MAX(n)                                                              AS closed_requests,
       ROUND(MAX(CASE WHEN rn = CAST(n * 0.5 AS INT) + 1 THEN hours END), 1) AS median_hours,
       ROUND(MAX(CASE WHEN rn = CAST(n * 0.9 AS INT) + 1 THEN hours END), 1) AS p90_hours
FROM ranked
GROUP BY agency
HAVING closed_requests >= 1000
ORDER BY median_hours;


-- 4. Which complaints take longest to resolve?
WITH durations AS (
    SELECT complaint_type,
           julianday(closed_date) - julianday(created_date) AS days
    FROM requests
    WHERE closed_date IS NOT NULL AND closed_date >= created_date
),
ranked AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY complaint_type ORDER BY days) AS rn,
           COUNT(*)     OVER (PARTITION BY complaint_type)               AS n
    FROM durations
)
SELECT complaint_type,
       MAX(n)                                                            AS closed_requests,
       ROUND(MAX(CASE WHEN rn = CAST(n * 0.5 AS INT) + 1 THEN days END), 1) AS median_days,
       ROUND(MAX(CASE WHEN rn = CAST(n * 0.9 AS INT) + 1 THEN days END), 1) AS p90_days
FROM ranked
GROUP BY complaint_type
HAVING closed_requests >= 400
ORDER BY median_days DESC
LIMIT 10;


-- 5. When do different complaints come in?
WITH top_types AS (
    SELECT complaint_type FROM requests GROUP BY complaint_type ORDER BY COUNT(*) DESC LIMIT 4
),
hourly AS (
    SELECT complaint_type,
           CAST(strftime('%H', created_date) AS INT) AS hour,
           COUNT(*)                                  AS n
    FROM requests
    WHERE complaint_type IN (SELECT complaint_type FROM top_types)
      AND strftime('%H:%M:%S', created_date) <> '00:00:00'
    GROUP BY complaint_type, hour
)
SELECT complaint_type, hour, n,
       ROUND(100.0 * n / SUM(n) OVER (PARTITION BY complaint_type), 2) AS share_of_day_pct
FROM hourly
ORDER BY complaint_type, hour;


-- 6. Which complaints follow the seasons?
SELECT strftime('%m', created_date)                                          AS month,
       ROUND(SUM(complaint_type = 'HEAT/HOT WATER')        / 2.0)           AS heat_hot_water,
       ROUND(SUM(complaint_type = 'Noise - Residential')   / 2.0)           AS noise_residential,
       ROUND(SUM(complaint_type = 'Illegal Parking')       / 2.0)           AS illegal_parking,
       ROUND(SUM(complaint_type LIKE 'Water%'
              OR complaint_type = 'Plumbing')              / 2.0)           AS water_plumbing,
       ROUND(COUNT(*) / 2.0)                                                AS all_requests
FROM requests
GROUP BY month
ORDER BY month;


-- 7. What is each borough unusually known for?
WITH borough_type AS (
    SELECT borough, complaint_type, COUNT(*) AS n
    FROM requests
    WHERE borough <> 'Unspecified'
    GROUP BY borough, complaint_type
),
shares AS (
    SELECT *,
           1.0 * n / SUM(n) OVER (PARTITION BY borough)         AS borough_share,
           1.0 * SUM(n) OVER (PARTITION BY complaint_type)
               / SUM(n) OVER ()                                 AS city_share
    FROM borough_type
),
ranked AS (
    SELECT *, borough_share / city_share AS quotient,
           ROW_NUMBER() OVER (PARTITION BY borough ORDER BY borough_share / city_share DESC) AS rn
    FROM shares
    WHERE n >= 150
)
SELECT borough, complaint_type, n AS requests,
       ROUND(100.0 * borough_share, 1) AS borough_share_pct,
       ROUND(100.0 * city_share, 1)    AS city_share_pct,
       ROUND(quotient, 1)              AS location_quotient
FROM ranked
WHERE rn <= 2
ORDER BY borough, location_quotient DESC;
