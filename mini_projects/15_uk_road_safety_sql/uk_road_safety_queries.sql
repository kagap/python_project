-- A year of road collisions in Britain, in plain SQL (SQLite dialect)
-- Run against roads.db: https://www.data.gov.uk/dataset/cb7ae6f0-4be6-4935-9277-47e5ce24a11f/road-safety-data

-- 1. How serious are the collisions?
SELECT s.severity_name                                   AS severity,
       COUNT(*)                                          AS collisions,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS share_pct,
       SUM(c.number_of_casualties)                       AS casualties,
       ROUND(AVG(c.number_of_vehicles), 2)               AS avg_vehicles,
       ROUND(AVG(c.number_of_casualties), 2)             AS avg_casualties
FROM collisions c
JOIN severities s ON s.severity = c.collision_severity
GROUP BY c.collision_severity
ORDER BY c.collision_severity;


-- 2. When do collisions happen?
SELECT c.day_of_week,
       w.weekday_name                                 AS weekday,
       CAST(substr(c.time, 1, 2) AS INT)              AS hour,
       COUNT(*)                                       AS collisions
FROM collisions c
JOIN weekdays w USING (day_of_week)
GROUP BY c.day_of_week, hour
ORDER BY c.day_of_week, hour;


-- 3. Are the quiet hours safer?
SELECT CAST(substr(time, 1, 2) AS INT)                       AS hour,
       COUNT(*)                                              AS collisions,
       SUM(collision_severity <= 2)                          AS fatal_or_serious,
       ROUND(100.0 * AVG(collision_severity <= 2), 1)        AS fatal_or_serious_pct,
       ROUND(100.0 * AVG(collision_severity = 1), 2)         AS fatal_pct
FROM collisions
GROUP BY hour
ORDER BY hour;


-- 4. How much does the speed limit matter?
SELECT speed_limit,
       COUNT(*)                                       AS collisions,
       SUM(collision_severity = 1)                    AS fatal,
       ROUND(100.0 * AVG(collision_severity <= 2), 1) AS fatal_or_serious_pct,
       ROUND(100.0 * AVG(collision_severity = 1), 2)  AS fatal_pct,
       ROUND(AVG(number_of_casualties), 2)            AS avg_casualties
FROM collisions
GROUP BY speed_limit
ORDER BY speed_limit;


-- 5. Does darkness change the odds?
SELECT l.light_name                                     AS light,
       a.area_name                                      AS area,
       COUNT(*)                                         AS collisions,
       ROUND(100.0 * AVG(c.collision_severity <= 2), 1) AS fatal_or_serious_pct
FROM collisions c
JOIN light l USING (light_conditions)
JOIN areas a USING (urban_or_rural_area)
WHERE a.area_name IN ('Urban', 'Rural')
GROUP BY l.light_name, a.area_name
HAVING collisions >= 300
ORDER BY a.area_name, fatal_or_serious_pct DESC;


-- 6. Which vehicles are in the most dangerous collisions?
SELECT COALESCE(vt.vehicle_group, 'Unclassified')           AS vehicle_group,
       COUNT(*)                                             AS vehicles_involved,
       ROUND(100.0 * AVG(c.collision_severity <= 2), 1)     AS fatal_or_serious_pct,
       ROUND(100.0 * AVG(c.collision_severity = 1), 2)      AS fatal_pct
FROM vehicles v
JOIN collisions c USING (collision_index)
LEFT JOIN vehicle_types vt ON vt.vehicle_type = v.vehicle_type
GROUP BY vehicle_group
HAVING vehicles_involved >= 300
ORDER BY fatal_or_serious_pct DESC;


-- 7. Who gets hurt, by age?
WITH banded AS (
    SELECT CASE WHEN k.age_of_casualty < 16 THEN '0-15'
                WHEN k.age_of_casualty < 25 THEN '16-24'
                WHEN k.age_of_casualty < 35 THEN '25-34'
                WHEN k.age_of_casualty < 45 THEN '35-44'
                WHEN k.age_of_casualty < 55 THEN '45-54'
                WHEN k.age_of_casualty < 65 THEN '55-64'
                WHEN k.age_of_casualty < 75 THEN '65-74'
                ELSE '75+' END                      AS age_band,
           k.casualty_severity, k.casualty_class
    FROM casualties k
    WHERE k.age_of_casualty >= 0
)
SELECT age_band,
       COUNT(*)                                             AS casualties,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)   AS share_pct,
       ROUND(100.0 * AVG(casualty_severity <= 2), 1)        AS fatal_or_serious_pct,
       ROUND(100.0 * AVG(casualty_class = 3), 1)            AS pedestrian_pct
FROM banded
GROUP BY age_band
ORDER BY age_band;


-- 8. Are collisions with more vehicles worse?
SELECT CASE WHEN number_of_vehicles >= 3 THEN '3 or more' ELSE CAST(number_of_vehicles AS TEXT) END AS vehicles,
       COUNT(*)                                          AS collisions,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS share_pct,
       ROUND(100.0 * AVG(collision_severity <= 2), 1)    AS fatal_or_serious_pct,
       ROUND(100.0 * AVG(light_conditions <> 1), 1)      AS in_darkness_pct,
       ROUND(AVG(number_of_casualties), 2)               AS avg_casualties
FROM collisions
GROUP BY vehicles
ORDER BY vehicles;
