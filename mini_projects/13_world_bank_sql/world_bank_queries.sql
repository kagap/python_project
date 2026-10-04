-- 60 years of countries getting richer and healthier, in plain SQL (SQLite dialect)
-- Run against worldbank.db: https://data.worldbank.org/

-- 1. How did Poland's economy grow compared with its neighbours?
SELECT c.name AS country, i.year,
       ROUND(i.gdp_per_capita)                                       AS gdp_per_capita,
       ROUND(100.0 * i.gdp_per_capita
             / FIRST_VALUE(i.gdp_per_capita) OVER (PARTITION BY i.country_code ORDER BY i.year), 0) AS index_1995
FROM indicators i
JOIN countries c ON c.code = i.country_code
WHERE i.country_code IN ('POL', 'DEU', 'CZE', 'HUN', 'SVK', 'ROU')
  AND i.year >= 1995 AND i.gdp_per_capita IS NOT NULL
ORDER BY c.name, i.year;


-- 2. Which countries grew richest the fastest?
WITH start AS (SELECT country_code, gdp_per_capita AS gdp FROM indicators WHERE year = 1995),
finish AS (SELECT country_code, gdp_per_capita AS gdp, population FROM indicators WHERE year = 2022)
SELECT RANK() OVER (ORDER BY f.gdp / s.gdp DESC)      AS rank,
       c.name                                         AS country,
       c.region,
       ROUND(s.gdp)                                   AS gdp_1995,
       ROUND(f.gdp)                                   AS gdp_2022,
       ROUND(f.gdp / s.gdp, 1)                        AS times_richer,
       ROUND(100 * (power(f.gdp / s.gdp, 1.0 / 27) - 1), 1) AS avg_yearly_growth_pct
FROM start s
JOIN finish    f USING (country_code)
JOIN countries c ON c.code = s.country_code
WHERE f.population >= 1000000 AND s.gdp > 0
ORDER BY times_richer DESC
LIMIT 12;


-- 3. Which countries lost the most years of life expectancy?
WITH yearly AS (
    SELECT country_code, year, life_expectancy,
           LAG(life_expectancy, 2) OVER (PARTITION BY country_code ORDER BY year) AS two_years_before
    FROM indicators
    WHERE year BETWEEN 2018 AND 2021 AND life_expectancy IS NOT NULL
)
SELECT c.name AS country, c.region,
       ROUND(two_years_before, 1)                    AS le_2019,
       ROUND(life_expectancy, 1)                     AS le_2021,
       ROUND(life_expectancy - two_years_before, 1)  AS change_years
FROM yearly y
JOIN countries c ON c.code = y.country_code
WHERE y.year = 2021 AND two_years_before IS NOT NULL
ORDER BY change_years
LIMIT 12;


-- 4. How strongly are money and health linked?
WITH data AS (
    SELECT year, ln(gdp_per_capita) AS x, life_expectancy AS y
    FROM indicators
    WHERE year IN (1970, 1980, 1990, 2000, 2010, 2020)
      AND gdp_per_capita > 0 AND life_expectancy IS NOT NULL
),
sums AS (
    SELECT year, COUNT(*) AS n, SUM(x) AS sx, SUM(y) AS sy, SUM(x * y) AS sxy,
           SUM(x * x) AS sxx, SUM(y * y) AS syy
    FROM data
    GROUP BY year
)
SELECT year,
       n AS countries,
       ROUND((n * sxy - sx * sy) / (sqrt(n * sxx - sx * sx) * sqrt(n * syy - sy * sy)), 3) AS correlation
FROM sums
ORDER BY year;


-- 5. How much longer do people live now than in 1960, by region?
SELECT c.region, i.year,
       ROUND(SUM(i.life_expectancy * i.population) / SUM(i.population), 1) AS life_expectancy,
       ROUND(SUM(i.under5_mortality * i.population) / SUM(i.population), 0) AS under5_mortality
FROM indicators i
JOIN countries c ON c.code = i.country_code
WHERE i.year IN (1960, 1970, 1980, 1990, 2000, 2010, 2019)
  AND i.life_expectancy IS NOT NULL AND i.population IS NOT NULL AND i.under5_mortality IS NOT NULL
GROUP BY c.region, i.year
ORDER BY c.region, i.year;


-- 6. Which countries climbed the income ranking?
WITH r2000 AS (
    SELECT country_code, gdp_per_capita AS gdp,
           RANK() OVER (ORDER BY gdp_per_capita DESC) AS rnk
    FROM indicators
    WHERE year = 2000 AND gdp_per_capita IS NOT NULL AND population >= 1000000
),
r2022 AS (
    SELECT country_code, gdp_per_capita AS gdp,
           RANK() OVER (ORDER BY gdp_per_capita DESC) AS rnk
    FROM indicators
    WHERE year = 2022 AND gdp_per_capita IS NOT NULL AND population >= 1000000
)
SELECT c.name AS country,
       a.rnk  AS rank_2000,
       b.rnk  AS rank_2022,
       a.rnk - b.rnk AS places_gained,
       ROUND(a.gdp) AS gdp_2000,
       ROUND(b.gdp) AS gdp_2022
FROM r2000 a
JOIN r2022 b USING (country_code)
JOIN countries c ON c.code = a.country_code
ORDER BY places_gained DESC;


-- 7. Can a country grow richer and cut emissions at the same time?
WITH wide AS (
    SELECT country_code,
           MAX(CASE WHEN year = 2005 THEN gdp_per_capita END) AS gdp_2005,
           MAX(CASE WHEN year = 2019 THEN gdp_per_capita END) AS gdp_2019,
           MAX(CASE WHEN year = 2005 THEN co2_per_capita END) AS co2_2005,
           MAX(CASE WHEN year = 2019 THEN co2_per_capita END) AS co2_2019,
           MAX(CASE WHEN year = 2019 THEN population END)     AS pop_2019
    FROM indicators
    WHERE year IN (2005, 2019)
    GROUP BY country_code
)
SELECT c.name AS country,
       ROUND(100.0 * (gdp_2019 / gdp_2005 - 1)) AS gdp_change_pct,
       ROUND(100.0 * (co2_2019 / co2_2005 - 1)) AS co2_change_pct,
       ROUND(co2_2005, 1) AS co2_2005,
       ROUND(co2_2019, 1) AS co2_2019
FROM wide w
JOIN countries c ON c.code = w.country_code
WHERE gdp_2005 > 0 AND co2_2005 > 0 AND pop_2019 >= 5000000
  AND gdp_2019 / gdp_2005 > 1.2 AND co2_2019 < co2_2005
ORDER BY co2_change_pct
LIMIT 15;
