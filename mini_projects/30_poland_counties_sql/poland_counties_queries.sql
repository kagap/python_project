-- Twenty years of Polish counties, in plain SQL (SQLite dialect)
-- Run against poland_counties.db: https://bdl.stat.gov.pl/

-- 1. How did Poland look as a whole, 2005 to 2025?
SELECT year,
       COUNT(*)                                                           AS counties,
       ROUND(SUM(unemployment * population) / SUM(population), 1)         AS unemployment_pct,
       ROUND(SUM(wage * population) / SUM(population))                    AS avg_wage_pln,
       ROUND(MIN(unemployment), 1)                                        AS lowest_county,
       ROUND(MAX(unemployment), 1)                                        AS highest_county,
       ROUND(SUM(unemployment * population) / SUM(population)
             - LAG(SUM(unemployment * population) / SUM(population)) OVER (ORDER BY year), 1) AS change_pp
FROM panel
WHERE unemployment IS NOT NULL AND population IS NOT NULL AND wage IS NOT NULL
GROUP BY year
ORDER BY year;


-- 2. Where do the county series break?
WITH yearly AS (
    SELECT p.name, p.voivodship, f.year, f.population,
           LAG(f.population) OVER (PARTITION BY f.powiat_id ORDER BY f.year) AS previous
    FROM panel f
    JOIN powiats p USING (powiat_id)
)
SELECT name, voivodship, year,
       ROUND(previous, 1)                           AS thousand_before,
       ROUND(population, 1)                         AS thousand_after,
       ROUND(100.0 * (population / previous - 1), 1) AS change_pct
FROM yearly
WHERE ABS(population / previous - 1) > 0.08
ORDER BY year, name;


-- 3. Is the gap between counties closing?
WITH ranked AS (
    SELECT year, unemployment,
           ROW_NUMBER() OVER (PARTITION BY year ORDER BY unemployment) AS rn,
           COUNT(*)     OVER (PARTITION BY year)                       AS n
    FROM panel
    WHERE unemployment IS NOT NULL
),
pct AS (
    SELECT year,
           MIN(CASE WHEN rn >= 0.10 * n THEN unemployment END) AS p10,
           MIN(CASE WHEN rn >= 0.50 * n THEN unemployment END) AS median,
           MIN(CASE WHEN rn >= 0.90 * n THEN unemployment END) AS p90
    FROM ranked
    GROUP BY year
)
SELECT year, p10, median, p90,
       ROUND(p90 - p10, 1)  AS gap_pp,
       ROUND(p90 / p10, 1)  AS ratio
FROM pct
ORDER BY year;


-- 4. Where are the best and the worst paid counties?
WITH w AS (
    SELECT p.name, p.voivodship, p.is_city, ROUND(f.wage) AS wage_pln,
           RANK() OVER (ORDER BY f.wage DESC)          AS rank_high,
           RANK() OVER (ORDER BY f.wage)               AS rank_low,
           ROUND(100.0 * f.wage / AVG(f.wage) OVER ()) AS index_vs_average
    FROM panel f
    JOIN powiats p USING (powiat_id)
    WHERE f.year = 2024
)
SELECT CASE WHEN rank_high <= 10 THEN rank_high ELSE -rank_low END AS position,
       name, voivodship, is_city, wage_pln, index_vs_average
FROM w
WHERE rank_high <= 10 OR rank_low <= 10
ORDER BY wage_pln DESC;


-- 5. How do the 16 voivodships compare?
SELECT p.voivodship,
       COUNT(DISTINCT p.powiat_id)                                             AS counties,
       ROUND(SUM(CASE WHEN f.year = 2024 THEN f.population END) / 1000, 2)    AS population_millions,
       ROUND(SUM(CASE WHEN f.year = 2024 THEN f.wage * f.population END)
             / SUM(CASE WHEN f.year = 2024 THEN f.population END))            AS wage_2024,
       ROUND(100.0 * ((SUM(CASE WHEN f.year = 2024 THEN f.wage * f.population END)
                       / SUM(CASE WHEN f.year = 2024 THEN f.population END))
                    / (SUM(CASE WHEN f.year = 2010 THEN f.wage * f.population END)
                       / SUM(CASE WHEN f.year = 2010 THEN f.population END)) - 1)) AS wage_growth_pct,
       ROUND(SUM(CASE WHEN f.year = 2010 THEN f.unemployment * f.population END)
             / SUM(CASE WHEN f.year = 2010 THEN f.population END), 1)         AS unemployment_2010,
       ROUND(SUM(CASE WHEN f.year = 2024 THEN f.unemployment * f.population END)
             / SUM(CASE WHEN f.year = 2024 THEN f.population END), 1)         AS unemployment_2024,
       ROUND(100.0 * (SUM(CASE WHEN f.year = 2024 THEN f.population END)
                      / SUM(CASE WHEN f.year = 2010 THEN f.population END) - 1), 1) AS population_change_pct
FROM panel f
JOIN powiats p USING (powiat_id)
WHERE f.year IN (2010, 2024)
GROUP BY p.voivodship
ORDER BY wage_2024 DESC;


-- 6. Which counties moved up the pay table, and which fell back?
WITH r AS (
    SELECT powiat_id, year, wage,
           RANK() OVER (PARTITION BY year ORDER BY wage DESC) AS pay_rank
    FROM panel
    WHERE year IN (2010, 2024) AND wage IS NOT NULL
      AND powiat_id NOT IN (SELECT powiat_id FROM series_breaks)
),
moves AS (
    SELECT p.name, p.voivodship, p.is_city,
           a.pay_rank AS rank_2010, b.pay_rank AS rank_2024,
           a.pay_rank - b.pay_rank AS places_gained,
           ROUND(a.wage) AS wage_2010, ROUND(b.wage) AS wage_2024,
           ROUND(100.0 * (b.wage / a.wage - 1)) AS growth_pct
    FROM r a
    JOIN r b ON a.powiat_id = b.powiat_id AND a.year = 2010 AND b.year = 2024
    JOIN powiats p ON p.powiat_id = a.powiat_id
),
numbered AS (
    SELECT *, ROW_NUMBER() OVER (ORDER BY places_gained DESC) AS best,
              ROW_NUMBER() OVER (ORDER BY places_gained)      AS worst
    FROM moves
)
SELECT name, voivodship, rank_2010, rank_2024, places_gained, wage_2010, wage_2024, growth_pct
FROM numbered
WHERE best <= 8 OR worst <= 8
ORDER BY places_gained DESC;


-- 7. Are poorer counties catching up?
WITH starts(y0) AS (VALUES (2005), (2010), (2015), (2019)),
pairs AS (
    SELECT s.y0, LN(a.wage) AS x, LN(b.wage / a.wage) AS y
    FROM starts s
    JOIN panel a ON a.year = s.y0
    JOIN panel b ON b.powiat_id = a.powiat_id AND b.year = 2024
    WHERE a.wage > 0 AND b.wage > 0 AND a.powiat_id NOT IN (SELECT powiat_id FROM series_breaks)
),
sums AS (
    SELECT y0, COUNT(*) AS n, SUM(x) AS sx, SUM(y) AS sy, SUM(x * y) AS sxy, SUM(x * x) AS sxx, SUM(y * y) AS syy
    FROM pairs GROUP BY y0
)
SELECT y0                                                                              AS start_year,
       n                                                                               AS counties,
       ROUND((n * sxy - sx * sy) / (n * sxx - sx * sx), 3)                             AS slope,
       ROUND((n * sxy - sx * sy) / SQRT((n * sxx - sx * sx) * (n * syy - sy * sy)), 2) AS correlation
FROM sums
ORDER BY y0;


-- 8. Do low unemployment and high wages go together?
WITH s AS (
    SELECT year, COUNT(*) AS n,
           SUM(unemployment) AS sx, SUM(LN(wage)) AS sy, SUM(unemployment * LN(wage)) AS sxy,
           SUM(unemployment * unemployment) AS sxx, SUM(LN(wage) * LN(wage)) AS syy
    FROM panel
    WHERE unemployment IS NOT NULL AND wage > 0
    GROUP BY year
),
u AS (
    SELECT year, COUNT(*) AS n,
           SUM(urbanization) AS sx, SUM(unemployment) AS sy, SUM(urbanization * unemployment) AS sxy,
           SUM(urbanization * urbanization) AS sxx, SUM(unemployment * unemployment) AS syy
    FROM panel
    WHERE unemployment IS NOT NULL AND urbanization IS NOT NULL
    GROUP BY year
)
SELECT s.year,
       ROUND((s.n * s.sxy - s.sx * s.sy) / SQRT((s.n * s.sxx - s.sx * s.sx) * (s.n * s.syy - s.sy * s.sy)), 2) AS corr_unemployment_wage,
       ROUND((u.n * u.sxy - u.sx * u.sy) / SQRT((u.n * u.sxx - u.sx * u.sx) * (u.n * u.syy - u.sy * u.sy)), 2) AS corr_urbanization_unemployment
FROM s
LEFT JOIN u USING (year)
ORDER BY s.year;


-- 9. Which counties grow and which shrink?
WITH change AS (
    SELECT p.name, p.voivodship, p.is_city,
           ROUND(a.population, 1)                               AS thousand_2010,
           ROUND(b.population, 1)                               AS thousand_2024,
           ROUND(100.0 * (b.population / a.population - 1), 1) AS change_pct,
           ROUND(b.median_age, 1)                               AS median_age_2024
    FROM panel a
    JOIN panel b ON a.powiat_id = b.powiat_id AND a.year = 2010 AND b.year = 2024
    JOIN powiats p ON p.powiat_id = a.powiat_id
    WHERE a.powiat_id NOT IN (SELECT powiat_id FROM series_breaks)
),
numbered AS (
    SELECT *, ROW_NUMBER() OVER (ORDER BY change_pct DESC) AS up,
              ROW_NUMBER() OVER (ORDER BY change_pct)      AS down,
              SUM(change_pct > 0) OVER ()                  AS growing,
              COUNT(*) OVER ()                             AS total
    FROM change
)
SELECT name, voivodship, is_city, thousand_2010, thousand_2024, change_pct, median_age_2024, growing, total
FROM numbered
WHERE up <= 8 OR down <= 8
ORDER BY change_pct DESC;


-- 10. Cities and countryside: who is ageing fastest?
SELECT f.year,
       CASE WHEN p.is_city = 1 THEN 'City with powiat status' ELSE 'Land county' END AS type,
       COUNT(*)                                  AS counties,
       ROUND(AVG(f.median_age), 1)               AS avg_median_age,
       ROUND(MIN(f.median_age), 1)               AS youngest,
       ROUND(MAX(f.median_age), 1)               AS oldest
FROM panel f
JOIN powiats p USING (powiat_id)
WHERE f.median_age IS NOT NULL
GROUP BY f.year, p.is_city
ORDER BY type, f.year;


-- 11. What kind of economy does each county have?
WITH c AS (
    SELECT f.*, MAX(emp_agriculture, emp_industry, emp_construction, emp_services, emp_public) AS top_share
    FROM panel f
    WHERE f.year = 2024 AND f.emp_services IS NOT NULL
),
labelled AS (
    SELECT *, CASE WHEN emp_services = top_share THEN 'Market services'
                   WHEN emp_industry = top_share THEN 'Industry'
                   WHEN emp_agriculture = top_share THEN 'Agriculture'
                   WHEN emp_public = top_share THEN 'Public services'
                   ELSE 'Construction' END AS dominant
    FROM c
)
SELECT dominant                              AS dominant_sector,
       COUNT(*)                              AS counties,
       ROUND(AVG(top_share), 1)              AS avg_top_share_pct,
       ROUND(AVG(wage))                      AS avg_wage_pln,
       ROUND(AVG(unemployment), 1)           AS avg_unemployment_pct,
       ROUND(AVG(median_age), 1)             AS avg_median_age
FROM labelled
GROUP BY dominant
ORDER BY counties DESC;


-- 12. Which counties are at the bottom year after year?
WITH q AS (
    SELECT powiat_id, year, unemployment,
           NTILE(4) OVER (PARTITION BY year ORDER BY unemployment DESC) AS quartile
    FROM panel
    WHERE unemployment IS NOT NULL
)
SELECT p.name, p.voivodship,
       COUNT(*)                         AS years,
       SUM(q.quartile = 1)              AS years_in_worst_quartile,
       ROUND(AVG(q.unemployment), 1)    AS avg_unemployment,
       COUNT(*) OVER ()                 AS persistent_total
FROM q
JOIN powiats p USING (powiat_id)
GROUP BY q.powiat_id
HAVING years = 21 AND years_in_worst_quartile = 21
ORDER BY avg_unemployment DESC
LIMIT 12;
