-- Every rocket launch in history, in plain SQL (SQLite dialect)
-- Run against space_launches.db: https://planet4589.org/space/gcat/

-- 1. What counts as a launch?
SELECT CASE WHEN orbital = 1 THEN 'Orbital launch' ELSE category END AS kind,
       COUNT(*)                                               AS launches,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)     AS share_pct,
       MIN(year)                                              AS first_year,
       MAX(year)                                              AS last_year
FROM launches
GROUP BY kind
ORDER BY launches DESC
LIMIT 10;


-- 2. How many orbital launches per year?
WITH yearly AS (
    SELECT year,
           COUNT(*)                   AS launches,
           SUM(outcome = 'success')   AS successes,
           SUM(outcome = 'failure')   AS failures
    FROM launches
    WHERE orbital = 1
    GROUP BY year
)
SELECT year, launches, failures,
       ROUND(100.0 * successes / launches, 1)                   AS success_pct,
       SUM(launches) OVER (ORDER BY year)                       AS total_so_far,
       launches - LAG(launches) OVER (ORDER BY year)            AS change
FROM yearly
ORDER BY year;


-- 3. Who launched, decade by decade?
SELECT (year / 10) * 10                                                                        AS decade,
       COUNT(*)                                                                                AS launches,
       SUM(state IN ('Soviet Union', 'Russia'))                                                AS soviet_russia,
       SUM(state = 'United States')                                                            AS usa,
       SUM(state = 'China')                                                                    AS china,
       SUM(state IN ('France', 'ESA', 'ELDO', 'United Kingdom', 'Italy', 'Germany'))           AS europe,
       SUM(state IN ('Japan', 'India'))                                                        AS japan_india,
       SUM(NOT (state IN ('Soviet Union', 'Russia', 'United States', 'China', 'France', 'ESA', 'ELDO',
                          'United Kingdom', 'Italy', 'Germany', 'Japan', 'India')) OR state IS NULL) AS other
FROM launches
WHERE orbital = 1
GROUP BY decade
ORDER BY decade;


-- 4. Which rockets flew the most?
WITH f AS (
    SELECT COALESCE(v.family, l.vehicle)  AS family,
           MIN(v.manufacturer)             AS manufacturer,
           COUNT(*)                        AS launches,
           SUM(l.outcome = 'success')      AS successes,
           MIN(l.year)                     AS first_year,
           MAX(l.year)                     AS last_year
    FROM launches l
    LEFT JOIN vehicles v USING (vehicle)
    WHERE l.orbital = 1
    GROUP BY COALESCE(v.family, l.vehicle)
)
SELECT RANK() OVER (ORDER BY launches DESC)  AS rank,
       family, manufacturer, launches, first_year, last_year,
       ROUND(100.0 * successes / launches, 1) AS success_pct
FROM f
ORDER BY launches DESC
LIMIT 15;


-- 5. Do rockets get safer with age?
WITH n AS (
    SELECT vehicle, outcome,
           ROW_NUMBER() OVER (PARTITION BY vehicle ORDER BY launch_date, launch_tag) AS flight_no,
           COUNT(*)     OVER (PARTITION BY vehicle)                                   AS total
    FROM launches
    WHERE orbital = 1
)
SELECT CASE WHEN flight_no <= 5  THEN '1) flights 1-5'
            WHEN flight_no <= 20 THEN '2) flights 6-20'
            WHEN flight_no <= 50 THEN '3) flights 21-50'
            ELSE                      '4) flights 51+' END         AS flight_numbers,
       COUNT(*)                                                    AS launches,
       COUNT(DISTINCT vehicle)                                     AS vehicles,
       SUM(outcome = 'failure')                                    AS failures,
       ROUND(100.0 * SUM(outcome = 'failure') / COUNT(*), 1)       AS failure_pct
FROM n
WHERE total >= 20
GROUP BY flight_numbers
ORDER BY flight_numbers;


-- 6. Which launch sites matter?
SELECT s.short_name                                                       AS site,
       GROUP_CONCAT(DISTINCT s.state_code)                                AS states,
       COUNT(*)                                                           AS launches,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)                 AS share_pct,
       MIN(l.year)                                                        AS first_year,
       MAX(l.year)                                                        AS last_year,
       ROUND(100.0 * SUM(l.outcome = 'success') / COUNT(*), 1)            AS success_pct
FROM launches l
JOIN sites s USING (site_code)
WHERE l.orbital = 1
GROUP BY s.short_name
ORDER BY launches DESC
LIMIT 12;


-- 7. How often does something fly? Gaps between launches
WITH g AS (
    SELECT year, launch_date,
           julianday(launch_date) - julianday(LAG(launch_date) OVER (ORDER BY launch_date, launch_tag)) AS gap_days
    FROM launches
    WHERE orbital = 1
)
SELECT year,
       COUNT(*)                   AS launches,
       ROUND(AVG(gap_days), 2)    AS avg_gap_days,
       MAX(gap_days)              AS longest_gap_days,
       SUM(gap_days = 0)          AS same_day_as_previous
FROM g
GROUP BY year
ORDER BY year;


-- 8. How much of the world's flying is SpaceX?
WITH l AS (
    SELECT year, agency_code,
           CASE WHEN vehicle LIKE 'Starship%' THEN NULL ELSE payload_tonnes END AS tonnes
    FROM launches
    WHERE orbital = 1 AND year >= 2006
)
SELECT year,
       COUNT(*)                                                                         AS launches,
       SUM(agency_code = 'SPX')                                                         AS spacex_launches,
       ROUND(100.0 * SUM(agency_code = 'SPX') / COUNT(*), 1)                            AS spacex_launch_share_pct,
       ROUND(SUM(tonnes))                                                               AS world_tonnes,
       ROUND(100.0 * SUM(CASE WHEN agency_code = 'SPX' THEN tonnes END) / SUM(tonnes), 1) AS spacex_tonnes_share_pct
FROM l
GROUP BY year
ORDER BY year;


-- 9. Where do satellites go?
SELECT (year / 5) * 5                                                       AS from_year,
       COUNT(*)                                                             AS launches,
       SUM(substr(category, 5, 3) = 'LEO')                                  AS low_earth,
       SUM(substr(category, 5, 3) = 'SSO')                                  AS sun_synchronous,
       SUM(substr(category, 5, 3) IN ('GTO', 'GEO', 'STO', 'MTO'))          AS geostationary_transfer,
       SUM(substr(category, 5, 3) = 'ISS')                                  AS space_station,
       SUM(substr(category, 5, 3) IN ('MOL', 'HEO', 'EEO'))                 AS elliptical,
       SUM(substr(category, 5, 3) = 'MEO')                                  AS medium_earth
FROM launches
WHERE orbital = 1
GROUP BY from_year
ORDER BY from_year;


-- 10. Longest unbroken runs of success
WITH o AS (
    SELECT vehicle, launch_date, outcome,
           ROW_NUMBER() OVER (PARTITION BY vehicle ORDER BY launch_date, launch_tag)          AS rn,
           ROW_NUMBER() OVER (PARTITION BY vehicle, outcome ORDER BY launch_date, launch_tag) AS rn_outcome
    FROM launches
    WHERE orbital = 1
),
runs AS (
    SELECT vehicle, rn - rn_outcome AS grp, COUNT(*) AS run_length,
           MIN(launch_date) AS from_date, MAX(launch_date) AS to_date
    FROM o
    WHERE outcome = 'success'
    GROUP BY vehicle, grp
)
SELECT vehicle, run_length, from_date, to_date,
       CASE WHEN to_date = (SELECT MAX(launch_date) FROM launches x WHERE x.vehicle = runs.vehicle AND x.orbital = 1)
            THEN 'run reaches the last flight' ELSE 'ended by a non-success' END AS status
FROM runs
ORDER BY run_length DESC
LIMIT 10;
