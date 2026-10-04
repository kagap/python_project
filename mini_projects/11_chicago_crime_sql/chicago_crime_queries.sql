-- A year of crime in Chicago, in plain SQL (SQLite dialect)
-- Run against chicago.db: https://data.cityofchicago.org/Public-Safety/Crimes-2001-to-Present/ijzp-q8t2

-- 1. What does the year look like day by day?
WITH daily AS (
    SELECT substr(date, 1, 10) AS day, COUNT(*) AS incidents
    FROM crimes
    GROUP BY day
)
SELECT day, incidents,
       ROUND(AVG(incidents) OVER (ORDER BY day ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 1) AS moving_avg_7d
FROM daily
ORDER BY day;


-- 2. What kinds of crime are most common, and how often do they end in an arrest?
SELECT primary_type                                          AS crime_type,
       COUNT(*)                                              AS incidents,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)    AS share_pct,
       ROUND(100.0 * AVG(arrest), 1)                         AS arrest_pct,
       ROUND(100.0 * AVG(domestic), 1)                       AS domestic_pct
FROM crimes
GROUP BY primary_type
ORDER BY incidents DESC
LIMIT 12;


-- 3. When do crimes happen?
SELECT CAST(strftime('%w', date) AS INT) AS weekday,    -- 0 = Sunday
       CAST(strftime('%H', date) AS INT) AS hour,
       COUNT(*)                          AS incidents
FROM crimes
GROUP BY weekday, hour
ORDER BY weekday, hour;


-- 4. Which neighbourhoods report the most, and what is their typical crime?
WITH area_type AS (
    SELECT community_area, primary_type, COUNT(*) AS n
    FROM crimes
    WHERE community_area IS NOT NULL
    GROUP BY community_area, primary_type
),
ranked AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY community_area ORDER BY n DESC) AS rn,
           SUM(n)       OVER (PARTITION BY community_area)                 AS total
    FROM area_type
)
SELECT RANK() OVER (ORDER BY r.total DESC)  AS rank,
       a.community,
       r.total                              AS incidents,
       r.primary_type                       AS most_common_type,
       ROUND(100.0 * r.n / r.total, 1)      AS its_share_pct
FROM ranked r
JOIN community_areas a ON a.area_number = r.community_area
WHERE r.rn = 1
ORDER BY r.total DESC
LIMIT 10;


-- 5. Where do thefts happen?
SELECT location_description                                      AS location,
       COUNT(*)                                                  AS thefts,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)        AS share_pct,
       ROUND(100.0 * SUM(COUNT(*)) OVER (ORDER BY COUNT(*) DESC)
                   / SUM(COUNT(*)) OVER (), 1)                   AS cumulative_pct
FROM crimes
WHERE primary_type = 'THEFT' AND location_description IS NOT NULL
GROUP BY location_description
ORDER BY thefts DESC
LIMIT 10;


-- 6. Which days were unusual?
WITH daily AS (
    SELECT substr(date, 1, 10) AS day, COUNT(*) AS incidents
    FROM crimes
    GROUP BY day
),
stats AS (
    SELECT day, incidents,
           AVG(incidents)              OVER () AS mean,
           AVG(incidents * incidents)  OVER () AS mean_sq
    FROM daily
),
scored AS (
    SELECT day, incidents, mean,
           (incidents - mean) / sqrt(mean_sq - mean * mean) AS z
    FROM stats
)
SELECT day,
       CASE strftime('%w', day) WHEN '0' THEN 'Sun' WHEN '1' THEN 'Mon' WHEN '2' THEN 'Tue'
                                WHEN '3' THEN 'Wed' WHEN '4' THEN 'Thu' WHEN '5' THEN 'Fri' ELSE 'Sat' END AS weekday,
       incidents,
       ROUND(mean)       AS typical_day,
       ROUND(z, 2)       AS z_score
FROM scored
WHERE ABS(z) >= 2.5
ORDER BY ABS(z) DESC
LIMIT 10;


-- 7. Why are the 1st of the month so busy?
WITH daily AS (
    SELECT substr(date, 1, 10) AS day, primary_type, COUNT(*) AS n
    FROM crimes
    WHERE substr(date, 1, 10) <> '2023-01-01'
    GROUP BY day, primary_type
),
split AS (
    SELECT primary_type,
           AVG(CASE WHEN substr(day, 9, 2) =  '01' THEN n END) AS on_the_1st,
           AVG(CASE WHEN substr(day, 9, 2) <> '01' THEN n END) AS other_days
    FROM daily
    GROUP BY primary_type
)
SELECT primary_type                         AS crime_type,
       ROUND(on_the_1st)                    AS avg_on_the_1st,
       ROUND(other_days)                    AS avg_other_days,
       ROUND(on_the_1st - other_days)       AS extra_per_day,
       ROUND(on_the_1st / other_days, 1)    AS ratio
FROM split
WHERE other_days >= 20
ORDER BY extra_per_day DESC
LIMIT 6;


-- 8. How long did the hot streaks last?
WITH daily AS (
    SELECT substr(date, 1, 10) AS day, COUNT(*) AS incidents
    FROM crimes
    GROUP BY day
),
ordered AS (
    SELECT incidents,
           ROW_NUMBER() OVER (ORDER BY incidents) AS rn,
           COUNT(*)     OVER ()                   AS n
    FROM daily
),
median AS (SELECT incidents AS m FROM ordered WHERE rn = (n + 1) / 2),
flagged AS (
    SELECT day, incidents,
           incidents > (SELECT m FROM median)      AS above,
           ROW_NUMBER() OVER (ORDER BY day)        AS day_no
    FROM daily
),
numbered AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY above ORDER BY day) AS flag_no
    FROM flagged
)
SELECT MIN(day)              AS first_day,
       MAX(day)              AS last_day,
       COUNT(*)              AS days_in_a_row,
       ROUND(AVG(incidents)) AS avg_incidents
FROM numbered
WHERE above = 1
GROUP BY day_no - flag_no
ORDER BY days_in_a_row DESC, first_day
LIMIT 6;


-- 9. Which crimes follow the seasons?
WITH top_types AS (
    SELECT primary_type FROM crimes GROUP BY primary_type ORDER BY COUNT(*) DESC LIMIT 5
),
monthly AS (
    SELECT primary_type, CAST(strftime('%m', date) AS INT) AS month, COUNT(*) AS n
    FROM crimes
    WHERE primary_type IN (SELECT primary_type FROM top_types)
    GROUP BY primary_type, month
)
SELECT primary_type AS crime_type, month, n,
       ROUND(100.0 * n / SUM(n) OVER (PARTITION BY primary_type), 2) AS share_of_year_pct
FROM monthly
ORDER BY primary_type, month;
