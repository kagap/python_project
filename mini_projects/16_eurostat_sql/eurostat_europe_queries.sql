-- Jobs and prices across Europe, in plain SQL (SQLite dialect)
-- Run against eurostat.db: https://ec.europa.eu/eurostat

-- 1. Which countries have the highest unemployment right now?
WITH series AS (
    SELECT country_code, month, rate_total, rate_youth,
           LAG(rate_total, 12) OVER (PARTITION BY country_code ORDER BY month)  AS year_ago,
           ROW_NUMBER()        OVER (PARTITION BY country_code ORDER BY month DESC) AS rn
    FROM unemployment
)
SELECT RANK() OVER (ORDER BY s.rate_total DESC)  AS rank,
       c.country,
       s.month,
       s.rate_total                              AS unemployment_pct,
       ROUND(s.rate_total - s.year_ago, 1)       AS change_vs_year_ago,
       s.rate_youth                              AS under25_pct
FROM series s
JOIN countries c USING (country_code)
WHERE s.rn = 1 AND c.is_aggregate = 0 AND s.month >= '2026-01'
ORDER BY s.rate_total DESC;


-- 2. Is youth unemployment a different problem?
WITH series AS (
    SELECT country_code, month, rate_total, rate_youth,
           ROW_NUMBER() OVER (PARTITION BY country_code ORDER BY month DESC) AS rn
    FROM unemployment
    WHERE rate_total IS NOT NULL AND rate_youth IS NOT NULL
)
SELECT c.country, s.month,
       s.rate_total                          AS overall_pct,
       s.rate_youth                          AS under25_pct,
       ROUND(s.rate_youth / s.rate_total, 1) AS youth_to_overall_ratio
FROM series s
JOIN countries c USING (country_code)
WHERE s.rn = 1 AND c.is_aggregate = 0 AND s.month >= '2026-01'
ORDER BY under25_pct DESC
LIMIT 12;


-- 3. How bad did the crisis years get, and who recovered?
WITH ranked AS (
    SELECT country_code, month, rate_total,
           ROW_NUMBER() OVER (PARTITION BY country_code ORDER BY rate_total DESC, month DESC) AS peak_rn,
           ROW_NUMBER() OVER (PARTITION BY country_code ORDER BY month DESC)                  AS last_rn
    FROM unemployment
    WHERE rate_total IS NOT NULL
)
SELECT c.country,
       MAX(CASE WHEN peak_rn = 1 THEN rate_total END)              AS peak_pct,
       MAX(CASE WHEN peak_rn = 1 THEN month END)                   AS peak_month,
       MAX(CASE WHEN last_rn = 1 THEN rate_total END)              AS latest_pct,
       MAX(CASE WHEN last_rn = 1 THEN month END)                   AS latest_month,
       ROUND(MAX(CASE WHEN peak_rn = 1 THEN rate_total END)
           - MAX(CASE WHEN last_rn = 1 THEN rate_total END), 1)    AS fall_since_peak
FROM ranked r
JOIN countries c USING (country_code)
WHERE c.is_aggregate = 0
GROUP BY r.country_code
HAVING latest_month >= '2026-01'
ORDER BY peak_pct DESC
LIMIT 12;


-- 4. How has Poland compared with the EU average?
SELECT month,
       MAX(CASE WHEN country_code = 'PL'        THEN rate_total END) AS poland,
       MAX(CASE WHEN country_code = 'EU27_2020' THEN rate_total END) AS eu27,
       ROUND(MAX(CASE WHEN country_code = 'PL'        THEN rate_total END)
           - MAX(CASE WHEN country_code = 'EU27_2020' THEN rate_total END), 1) AS gap
FROM unemployment
WHERE country_code IN ('PL', 'EU27_2020')
GROUP BY month
HAVING poland IS NOT NULL AND eu27 IS NOT NULL
ORDER BY month;


-- 5. Where did the 2022 price shock hit hardest?
WITH ranked AS (
    SELECT country_code, month, annual_rate,
           ROW_NUMBER() OVER (PARTITION BY country_code ORDER BY annual_rate DESC) AS rn
    FROM inflation
    WHERE coicop = 'CP00'
)
SELECT RANK() OVER (ORDER BY r.annual_rate DESC) AS rank,
       c.country,
       r.month        AS peak_month,
       r.annual_rate  AS peak_inflation_pct
FROM ranked r
JOIN countries c USING (country_code)
WHERE r.rn = 1 AND c.is_aggregate = 0
ORDER BY r.annual_rate DESC
LIMIT 12;


-- 6. What drove prices up: food, energy or transport?
SELECT month,
       MAX(CASE WHEN coicop = 'CP00' THEN annual_rate END) AS all_items,
       MAX(CASE WHEN coicop = 'CP01' THEN annual_rate END) AS food,
       MAX(CASE WHEN coicop = 'CP04' THEN annual_rate END) AS housing_and_energy,
       MAX(CASE WHEN coicop = 'CP07' THEN annual_rate END) AS transport
FROM inflation
WHERE country_code = 'EU27_2020' AND month >= '2019-01'
GROUP BY month
ORDER BY month;


-- 7. Do countries with higher unemployment have lower inflation?
WITH u AS (
    SELECT country_code, substr(month, 1, 4) AS year, AVG(rate_total) AS unemployment
    FROM unemployment WHERE rate_total IS NOT NULL
    GROUP BY country_code, year
),
i AS (
    SELECT country_code, substr(month, 1, 4) AS year, AVG(annual_rate) AS inflation
    FROM inflation WHERE coicop = 'CP00'
    GROUP BY country_code, year
),
joined AS (
    SELECT u.year, u.unemployment AS x, i.inflation AS y
    FROM u JOIN i USING (country_code, year)
    JOIN countries c USING (country_code)
    WHERE c.is_aggregate = 0 AND u.year BETWEEN '2015' AND '2025'
),
sums AS (
    SELECT year, COUNT(*) AS n, SUM(x) AS sx, SUM(y) AS sy, SUM(x * y) AS sxy, SUM(x * x) AS sxx, SUM(y * y) AS syy
    FROM joined GROUP BY year
)
SELECT year, n AS countries,
       ROUND((n * sxy - sx * sy) / (sqrt(n * sxx - sx * sx) * sqrt(n * syy - sy * sy)), 2) AS correlation
FROM sums
ORDER BY year;


-- 8. Which country had the longest run of falling unemployment?
WITH changes AS (
    SELECT country_code, month, rate_total,
           rate_total - LAG(rate_total) OVER (PARTITION BY country_code ORDER BY month) AS delta
    FROM unemployment
    WHERE rate_total IS NOT NULL
),
flagged AS (
    SELECT *, delta <= 0 AS not_rising,
           ROW_NUMBER() OVER (PARTITION BY country_code ORDER BY month) AS month_no
    FROM changes
    WHERE delta IS NOT NULL
),
numbered AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY country_code, not_rising ORDER BY month) AS flag_no
    FROM flagged
)
SELECT c.country,
       COUNT(*)              AS months_in_a_row,
       MIN(n.month)          AS from_month,
       MAX(n.month)          AS to_month,
       ROUND(SUM(n.delta), 1) AS change_pp
FROM numbered n
JOIN countries c USING (country_code)
WHERE n.not_rising = 1 AND c.is_aggregate = 0
GROUP BY n.country_code, n.month_no - n.flag_no
ORDER BY months_in_a_row DESC, from_month
LIMIT 8;
