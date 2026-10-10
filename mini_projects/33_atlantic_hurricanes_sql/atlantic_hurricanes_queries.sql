-- 175 years of Atlantic hurricanes, in plain SQL (SQLite dialect)
-- Run against hurricanes.db: https://www.nhc.noaa.gov/data/hurdat/

-- 1. How active was each season?
WITH y AS (
    SELECT year,
           SUM(peak_wind_kt >= 34) AS named_storms,
           SUM(peak_wind_kt >= 64) AS hurricanes,
           SUM(peak_wind_kt >= 96) AS major_hurricanes
    FROM storms
    GROUP BY year
)
SELECT year, named_storms, hurricanes, major_hurricanes,
       ROUND(AVG(named_storms) OVER (ORDER BY year ROWS BETWEEN 9 PRECEDING AND CURRENT ROW), 1) AS named_storms_10yr_avg
FROM y
ORDER BY year;


-- 2. Are there more storms, or just better detection?
SELECT CASE WHEN year < 1900 THEN '1) 1851-1899'
            WHEN year < 1966 THEN '2) 1900-1965 (ships and aircraft)'
            WHEN year < 1995 THEN '3) 1966-1994 (satellites)'
            ELSE                  '4) 1995-2025 (satellites, active era)' END      AS period,
       COUNT(DISTINCT year)                                                          AS seasons,
       ROUND(1.0 * SUM(peak_wind_kt >= 34) / COUNT(DISTINCT year), 1)                AS named_storms_per_season,
       ROUND(1.0 * SUM(peak_wind_kt >= 64) / COUNT(DISTINCT year), 1)                AS hurricanes_per_season,
       ROUND(1.0 * SUM(peak_wind_kt >= 96) / COUNT(DISTINCT year), 1)                AS major_per_season,
       ROUND(100.0 * AVG(julianday(last_obs) - julianday(first_obs) < 2), 1)         AS short_lived_pct
FROM storms
GROUP BY period
ORDER BY period;


-- 3. How strong do the strongest get?
SELECT (year / 10) * 10                                                     AS decade,
       COUNT(*)                                                             AS storms,
       SUM(peak_wind_kt < 34)                                               AS depression,
       SUM(peak_wind_kt >= 34 AND peak_wind_kt < 64)                        AS tropical_storm,
       SUM(peak_wind_kt >= 64 AND peak_wind_kt < 96)                        AS cat_1_2,
       SUM(peak_wind_kt >= 96 AND peak_wind_kt < 137)                       AS cat_3_4,
       SUM(peak_wind_kt >= 137)                                             AS cat_5,
       ROUND(100.0 * SUM(peak_wind_kt >= 96) / NULLIF(SUM(peak_wind_kt >= 64), 0), 1) AS major_share_of_hurricanes_pct
FROM storms
GROUP BY decade
ORDER BY decade;


-- 4. When is the season?
SELECT CAST(substr(first_obs, 6, 2) AS INT)                          AS month,
       COUNT(*)                                                      AS storms,
       SUM(peak_wind_kt >= 64)                                       AS hurricanes,
       SUM(peak_wind_kt >= 96)                                       AS major_hurricanes,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)            AS share_pct,
       ROUND(AVG(peak_wind_kt))                                      AS avg_peak_wind_kt,
       RANK() OVER (ORDER BY COUNT(*) DESC)                          AS busiest_rank
FROM storms
GROUP BY month
ORDER BY month;


-- 5. Which seasons carried the most energy?
WITH a AS (
    SELECT s.year, t.storm_id, t.max_wind_kt
    FROM tracks t
    JOIN storms s USING (storm_id)
    WHERE substr(t.obs_time, 12, 5) IN ('00:00', '06:00', '12:00', '18:00') AND t.max_wind_kt >= 34 AND t.status IN ('TS', 'HU')
)
SELECT year,
       COUNT(DISTINCT storm_id)                          AS storms,
       ROUND(SUM(max_wind_kt * max_wind_kt) / 10000.0)   AS ace,
       RANK() OVER (ORDER BY SUM(max_wind_kt * max_wind_kt) DESC) AS rank
FROM a
GROUP BY year
ORDER BY year;


-- 6. Which storms were the most intense?
SELECT RANK() OVER (ORDER BY peak_wind_kt DESC, lowest_pressure_mb)  AS rank_by_wind,
       RANK() OVER (ORDER BY lowest_pressure_mb)                     AS rank_by_pressure,
       COALESCE(name, 'unnamed') || ' ' || year                      AS storm,
       peak_wind_kt,
       ROUND(peak_wind_kt * 1.15078)                                 AS peak_wind_mph,
       lowest_pressure_mb,
       ROUND(julianday(last_obs) - julianday(first_obs), 1)          AS lifetime_days
FROM storms
WHERE lowest_pressure_mb IS NOT NULL
ORDER BY rank_by_wind
LIMIT 12;


-- 7. Do storms intensify faster than they used to?
WITH gains AS (
    SELECT a.storm_id, MAX(a.max_wind_kt - b.max_wind_kt) AS max_gain_24h
    FROM tracks a
    JOIN tracks b ON b.storm_id = a.storm_id AND b.obs_time = strftime('%Y-%m-%d %H:%M', a.obs_time, '-24 hours')
    WHERE a.max_wind_kt IS NOT NULL AND b.max_wind_kt IS NOT NULL
    GROUP BY a.storm_id
)
SELECT (s.year / 10) * 10                                         AS decade,
       COUNT(*)                                                    AS hurricanes,
       SUM(g.max_gain_24h >= 30)                                   AS rapidly_intensifying,
       ROUND(100.0 * AVG(g.max_gain_24h >= 30), 1)                 AS rapid_pct,
       ROUND(AVG(g.max_gain_24h), 1)                               AS avg_max_gain_kt,
       MAX(g.max_gain_24h)                                         AS fastest_gain_kt
FROM storms s
JOIN gains g USING (storm_id)
WHERE s.peak_wind_kt >= 64
GROUP BY decade
ORDER BY decade;


-- 8. How often do hurricanes make landfall?
SELECT (CAST(substr(obs_time, 1, 4) AS INT) / 10) * 10         AS decade,
       COUNT(*)                                                AS landfalls,
       SUM(max_wind_kt >= 64)                                  AS hurricane_landfalls,
       SUM(max_wind_kt >= 96)                                  AS major_landfalls,
       MAX(max_wind_kt)                                        AS strongest_kt
FROM tracks
WHERE record_id = 'L'
GROUP BY decade
ORDER BY decade;


-- 9. Longest runs of seasons with and without a major hurricane
WITH y AS (
    SELECT year, MAX(peak_wind_kt >= 96) AS had_major
    FROM storms
    GROUP BY year
),
n AS (
    SELECT year, had_major,
           ROW_NUMBER() OVER (ORDER BY year)                         AS rn,
           ROW_NUMBER() OVER (PARTITION BY had_major ORDER BY year)  AS rn_kind
    FROM y
),
runs AS (
    SELECT had_major, rn - rn_kind AS grp, MIN(year) AS from_year, MAX(year) AS to_year, COUNT(*) AS seasons
    FROM n
    GROUP BY had_major, grp
)
SELECT CASE WHEN had_major = 1 THEN 'with a major hurricane' ELSE 'without one' END AS kind,
       from_year, to_year, seasons
FROM runs
ORDER BY seasons DESC
LIMIT 10;


-- 10. Where do storms start, and has that moved?
WITH first_ts AS (
    SELECT storm_id, lat, lon,
           ROW_NUMBER() OVER (PARTITION BY storm_id ORDER BY obs_time) AS rn
    FROM tracks
    WHERE max_wind_kt >= 34
),
peak AS (
    SELECT storm_id, lat, lon,
           ROW_NUMBER() OVER (PARTITION BY storm_id ORDER BY max_wind_kt DESC, obs_time) AS rn
    FROM tracks
    WHERE max_wind_kt IS NOT NULL
)
SELECT (s.year / 10) * 10                  AS decade,
       COUNT(*)                            AS storms,
       ROUND(AVG(f.lat), 1)                AS avg_lat_named,
       ROUND(AVG(f.lon), 1)                AS avg_lon_named,
       ROUND(AVG(p.lat), 1)                AS avg_lat_at_peak,
       ROUND(AVG(p.lon), 1)                AS avg_lon_at_peak
FROM storms s
JOIN first_ts f ON f.storm_id = s.storm_id AND f.rn = 1
JOIN peak     p ON p.storm_id = s.storm_id AND p.rn = 1
WHERE s.year >= 1900
GROUP BY decade
ORDER BY decade;
