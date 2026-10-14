-- 131 years of American baby names, in plain SQL (SQLite dialect)
-- Run against baby_names.db: https://www.ssa.gov/oact/babynames/limits.html

-- 1. Do parents still pick from the same few names?
WITH ranked AS (
    SELECT year, sex, births,
           RANK() OVER (PARTITION BY year, sex ORDER BY births DESC) AS rnk,
           SUM(births) OVER (PARTITION BY year, sex)                 AS total_births
    FROM names
)
SELECT year, sex,
       COUNT(*)                                                                         AS distinct_names,
       ROUND(100.0 * SUM(CASE WHEN rnk <= 10 THEN births END) / MAX(total_births), 1)   AS top10_pct,
       ROUND(1000.0 * COUNT(*) / MAX(total_births), 1)                                  AS names_per_1000_births
FROM ranked
GROUP BY year, sex
ORDER BY year, sex;


-- 2. How many names does it take to name half of all children?
WITH r AS (
    SELECT year, sex,
           ROW_NUMBER() OVER (PARTITION BY year, sex ORDER BY births DESC, name) AS n,
           1.0 * SUM(births) OVER (PARTITION BY year, sex ORDER BY births DESC, name ROWS UNBOUNDED PRECEDING)
               / SUM(births) OVER (PARTITION BY year, sex)                       AS cum_share
    FROM names
)
SELECT year, sex, MIN(n) AS names_for_half
FROM r
WHERE cum_share >= 0.5
GROUP BY year, sex
ORDER BY year, sex;


-- 3. The most common names ever given
SELECT RANK() OVER (ORDER BY SUM(births) DESC)                                        AS rank,
       name, sex,
       SUM(births)                                                                    AS births,
       ROUND(100.0 * SUM(births) / SUM(SUM(births)) OVER (PARTITION BY sex), 2)       AS pct_of_sex,
       COUNT(*)                                                                       AS years_listed
FROM names
GROUP BY name, sex
ORDER BY rank
LIMIT 10;


-- 4. Who held number one, and for how long?
WITH top AS (
    SELECT year, sex, name
    FROM (SELECT year, sex, name, RANK() OVER (PARTITION BY year, sex ORDER BY births DESC) AS rnk FROM names)
    WHERE rnk = 1
),
n AS (
    SELECT sex, name, year, year - ROW_NUMBER() OVER (PARTITION BY sex, name ORDER BY year) AS grp
    FROM top
)
SELECT sex, name, MIN(year) AS from_year, MAX(year) AS to_year, COUNT(*) AS years_at_no1
FROM n
GROUP BY sex, name, grp
ORDER BY years_at_no1 DESC
LIMIT 12;


-- 5. Which names jumped overnight?
WITH s AS (
    SELECT name, sex, year, births,
           LAG(births) OVER (PARTITION BY name, sex ORDER BY year) AS prev,
           LAG(year)   OVER (PARTITION BY name, sex ORDER BY year) AS prev_year
    FROM names
)
SELECT name, sex, year, prev AS births_year_before, births, ROUND(1.0 * births / prev, 1) AS times_more, births - prev AS extra_births
FROM s
WHERE prev_year = year - 1 AND prev >= 200 AND births - prev >= 1500
ORDER BY times_more DESC
LIMIT 12;


-- 6. What does a spike look like?
SELECT name, year, births
FROM names
WHERE (name = 'Tammy' AND sex = 'F' AND year BETWEEN 1945 AND 1975)
   OR (name = 'Jaime' AND sex = 'F' AND year BETWEEN 1965 AND 1995)
   OR (name = 'Dawson' AND sex = 'M' AND year BETWEEN 1985 AND 2010)
   OR (name = 'Ashanti' AND sex = 'F' AND year BETWEEN 1990 AND 2010)
ORDER BY name, year;


-- 7. Which names changed sex?
WITH big AS (
    SELECT name FROM names GROUP BY name HAVING SUM(births) >= 50000
),
d AS (
    SELECT name, (year / 10) * 10 AS decade,
           SUM(CASE WHEN sex = 'F' THEN births ELSE 0 END) AS girls,
           SUM(births)                                      AS births
    FROM names
    WHERE name IN (SELECT name FROM big)
    GROUP BY name, decade
    HAVING SUM(births) >= 500
),
s AS (
    SELECT name, decade, births, ROUND(100.0 * girls / births, 1) AS girls_pct,
           MIN(100.0 * girls / births) OVER (PARTITION BY name) AS lowest,
           MAX(100.0 * girls / births) OVER (PARTITION BY name) AS highest
    FROM d
)
SELECT name, decade, births, girls_pct
FROM s
WHERE lowest < 10 AND highest > 90
ORDER BY name, decade;


-- 8. Letters and sounds: the rise of boys' names ending in n
SELECT year,
       ROUND(100.0 * SUM(CASE WHEN sex = 'M' AND substr(name, -1) = 'n' THEN births END)
             / SUM(CASE WHEN sex = 'M' THEN births END), 1) AS boys_ending_n_pct,
       ROUND(100.0 * SUM(CASE WHEN sex = 'F' AND substr(name, -1) = 'a' THEN births END)
             / SUM(CASE WHEN sex = 'F' THEN births END), 1) AS girls_ending_a_pct,
       ROUND(100.0 * SUM(CASE WHEN sex = 'F' AND substr(name, 1, 1) = 'A' THEN births END)
             / SUM(CASE WHEN sex = 'F' THEN births END), 1) AS girls_starting_a_pct
FROM names
GROUP BY year
ORDER BY year;


-- 9. How fast does a fad fall?
WITH RECURSIVE years(y) AS (
    SELECT 1880 UNION ALL SELECT y + 1 FROM years WHERE y < 2010
),
big AS (
    SELECT name, sex, SUM(births) AS total FROM names GROUP BY name, sex HAVING SUM(births) >= 100000
),
filled AS (
    SELECT b.name, b.sex, y.y AS year, COALESCE(n.births, 0) AS births
    FROM big b
    CROSS JOIN years y
    LEFT JOIN names n ON n.name = b.name AND n.sex = b.sex AND n.year = y.y
),
smooth AS (
    SELECT name, sex, year, births,
           AVG(births) OVER (PARTITION BY name, sex ORDER BY year ROWS BETWEEN 2 PRECEDING AND 2 FOLLOWING) AS avg5
    FROM filled
),
peak AS (
    SELECT name, sex, year AS peak_year, avg5 AS peak_avg5,
           ROW_NUMBER() OVER (PARTITION BY name, sex ORDER BY avg5 DESC, year) AS rn
    FROM smooth
),
fall AS (
    SELECT p.name, p.sex, MIN(s.year) AS half_year
    FROM peak p
    JOIN smooth s ON s.name = p.name AND s.sex = p.sex AND s.year > p.peak_year AND s.avg5 * 2 < p.peak_avg5
    WHERE p.rn = 1
    GROUP BY p.name, p.sex
)
SELECT p.name, p.sex, p.peak_year, ROUND(p.peak_avg5) AS peak_births_per_year,
       f.half_year - p.peak_year AS years_to_halve
FROM peak p
LEFT JOIN fall f USING (name, sex)
WHERE p.rn = 1 AND p.peak_year <= 1990
ORDER BY years_to_halve IS NULL, years_to_halve, p.peak_avg5 DESC;
