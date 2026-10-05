-- A month of New York yellow cabs, in plain SQL (SQLite dialect)
-- Run against taxi.db: https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page

-- 1. What does the month look like day by day?
WITH daily AS (
    SELECT substr(pickup_time, 1, 10) AS day,
           COUNT(*)                   AS trips,
           ROUND(SUM(total_amount))   AS revenue
    FROM trips
    WHERE pickup_time >= '2023-01-01' AND pickup_time < '2023-02-01'
    GROUP BY day
)
SELECT day, trips, revenue,
       ROUND(AVG(trips) OVER (ORDER BY day ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)) AS moving_avg_7d,
       ROUND(100.0 * (trips - LAG(trips, 7) OVER (ORDER BY day))
                   / LAG(trips, 7) OVER (ORDER BY day), 1)                            AS vs_same_day_last_week_pct
FROM daily
ORDER BY day;


-- 2. When do people take cabs?
SELECT CAST(strftime('%w', pickup_time) AS INT) AS weekday,    -- 0 = Sunday
       CAST(strftime('%H', pickup_time) AS INT) AS hour,
       COUNT(*)                                 AS trips
FROM trips
WHERE pickup_time >= '2023-01-01' AND pickup_time < '2023-02-01'
GROUP BY weekday, hour
ORDER BY weekday, hour;


-- 3. Where do trips start?
SELECT RANK() OVER (ORDER BY COUNT(*) DESC)               AS rank,
       z.zone,
       z.borough,
       COUNT(*)                                           AS trips,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS share_pct,
       ROUND(AVG(t.fare_amount), 2)                       AS avg_fare
FROM trips t
JOIN zones z ON z.zone_id = t.pickup_zone_id
WHERE pickup_time >= '2023-01-01' AND pickup_time < '2023-02-01'
GROUP BY z.zone_id
ORDER BY trips DESC
LIMIT 10;


-- 4. What are the most common routes?
WITH valid AS (
    SELECT pickup_zone_id, dropoff_zone_id, fare_amount, trip_distance,
           (julianday(dropoff_time) - julianday(pickup_time)) * 1440 AS minutes
    FROM trips
    WHERE pickup_time >= '2023-01-01' AND pickup_time < '2023-02-01' AND trip_distance > 0 AND fare_amount > 0
      AND dropoff_time > pickup_time
)
SELECT zp.zone                                AS pickup,
       zd.zone                                AS dropoff,
       COUNT(*)                               AS trips,
       ROUND(AVG(v.trip_distance), 1)         AS avg_miles,
       ROUND(AVG(v.minutes), 1)               AS avg_minutes,
       ROUND(AVG(v.fare_amount), 2)           AS avg_fare
FROM valid v
JOIN zones zp ON zp.zone_id = v.pickup_zone_id
JOIN zones zd ON zd.zone_id = v.dropoff_zone_id
WHERE zp.borough <> 'Unknown' AND zd.borough <> 'Unknown'      -- zones 264 and 265 are not real places
GROUP BY v.pickup_zone_id, v.dropoff_zone_id
ORDER BY trips DESC
LIMIT 10;


-- 5. What do airport trips look like?
WITH valid AS (
    SELECT zp.zone AS pickup, zd.zone AS dropoff, t.fare_amount, t.trip_distance, t.tip_amount, t.payment_type,
           (julianday(t.dropoff_time) - julianday(t.pickup_time)) * 1440 AS minutes
    FROM trips t
    JOIN zones zp ON zp.zone_id = t.pickup_zone_id
    JOIN zones zd ON zd.zone_id = t.dropoff_zone_id
    WHERE t.pickup_time >= '2023-01-01' AND t.pickup_time < '2023-02-01' AND t.trip_distance > 0 AND t.fare_amount > 0
      AND t.dropoff_time > t.pickup_time
)
SELECT CASE WHEN dropoff LIKE '%Airport' THEN 'To ' || dropoff ELSE 'From ' || pickup END AS direction,
       COUNT(*)                                       AS trips,
       ROUND(AVG(fare_amount), 1)                     AS avg_fare,
       ROUND(AVG(trip_distance), 1)                   AS avg_miles,
       ROUND(AVG(minutes))                            AS avg_minutes,
       ROUND(100.0 * AVG(CASE WHEN payment_type = 1 AND fare_amount >= 3 AND tip_amount / fare_amount < 1.5
                           THEN tip_amount / fare_amount END), 1) AS avg_tip_pct
FROM valid
WHERE dropoff LIKE '%Airport' OR pickup LIKE '%Airport'
GROUP BY direction
HAVING trips >= 500
ORDER BY trips DESC;


-- 6. When do passengers tip, and how much?
SELECT CAST(strftime('%H', pickup_time) AS INT)          AS hour,
       COUNT(*)                                          AS trips,
       ROUND(100.0 * AVG(tip_amount / fare_amount), 1)   AS avg_tip_pct,
       ROUND(100.0 * AVG(tip_amount = 0), 1)             AS no_tip_pct
FROM trips
WHERE pickup_time >= '2023-01-01' AND pickup_time < '2023-02-01' AND payment_type = 1 AND fare_amount >= 3
  AND tip_amount / fare_amount < 1.5                    -- drop obvious data-entry errors
GROUP BY hour
ORDER BY hour;


-- 7. How slow is Manhattan traffic?
WITH valid AS (
    SELECT CAST(strftime('%H', pickup_time) AS INT)                          AS hour,
           CASE WHEN strftime('%w', pickup_time) IN ('0', '6') THEN 'Weekend' ELSE 'Weekday' END AS day_type,
           trip_distance,
           (julianday(dropoff_time) - julianday(pickup_time)) * 24           AS hours
    FROM trips
    WHERE pickup_time >= '2023-01-01' AND pickup_time < '2023-02-01' AND trip_distance > 0.2
)
SELECT hour, day_type,
       COUNT(*)                           AS trips,
       ROUND(SUM(trip_distance) / SUM(hours), 1) AS avg_mph
FROM valid
WHERE hours BETWEEN 1.0 / 60 AND 3 AND trip_distance / hours < 80
GROUP BY hour, day_type
ORDER BY day_type, hour;


-- 8. Are most trips short hops?
WITH valid AS (
    SELECT trip_distance, fare_amount,
           CASE WHEN trip_distance < 1  THEN '1  under 1 mile'
                WHEN trip_distance < 2  THEN '2  1-2 miles'
                WHEN trip_distance < 5  THEN '3  2-5 miles'
                WHEN trip_distance < 10 THEN '4  5-10 miles'
                ELSE                         '5  over 10 miles' END AS bucket
    FROM trips
    WHERE pickup_time >= '2023-01-01' AND pickup_time < '2023-02-01' AND trip_distance > 0 AND fare_amount > 0
)
SELECT substr(bucket, 4)                                   AS distance,
       COUNT(*)                                            AS trips,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)  AS share_pct,
       ROUND(AVG(fare_amount), 2)                          AS avg_fare,
       ROUND(SUM(fare_amount) / SUM(trip_distance), 2)     AS fare_per_mile
FROM valid
GROUP BY bucket
ORDER BY bucket;
