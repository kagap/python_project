-- The world's airline network, in plain SQL (SQLite dialect)
-- Run against openflights.db: https://openflights.org/data.html

-- 1. Which airports have the most destinations?
WITH pairs AS (
    SELECT DISTINCT src, dst FROM routes
)
SELECT RANK() OVER (ORDER BY COUNT(*) DESC) AS airport_rank,
       p.src AS iata, a.city, a.country, COUNT(*) AS destinations
FROM pairs p
JOIN airports a ON a.iata = p.src
GROUP BY p.src, a.city, a.country
ORDER BY destinations DESC, iata
LIMIT 15;


-- 2. How unequal is the network?
WITH d AS (
    SELECT src AS iata, COUNT(DISTINCT dst) AS destinations FROM routes GROUP BY src
),
r AS (
    SELECT iata, destinations,
           ROW_NUMBER() OVER (ORDER BY destinations DESC, iata)                                          AS airport_rank,
           COUNT(*) OVER ()                                                                              AS airports,
           SUM(destinations) OVER (ORDER BY destinations DESC, iata ROWS UNBOUNDED PRECEDING)           AS running,
           SUM(destinations) OVER ()                                                                     AS total_pairs
    FROM d
)
SELECT airport_rank, iata, destinations,
       ROUND(100.0 * airport_rank / airports, 2) AS airport_pct,
       ROUND(100.0 * running / total_pairs, 1)   AS route_share_pct
FROM r
ORDER BY airport_rank;


-- 3. How many airports are tiny?
WITH d AS (
    SELECT src AS iata, COUNT(DISTINCT dst) AS destinations FROM routes GROUP BY src
)
SELECT COUNT(*)                         AS airports_with_departures,
       SUM(destinations = 1)            AS one_destination,
       SUM(destinations <= 3)           AS three_or_fewer,
       SUM(destinations >= 100)         AS hundred_or_more,
       ROUND(AVG(destinations), 1)      AS average,
       MAX(destinations)                AS most
FROM d;


-- 4. Which countries fly mostly at home?
WITH l AS (
    SELECT DISTINCT r.src, r.dst, a.country AS from_country, b.country AS to_country
    FROM routes r
    JOIN airports a ON a.iata = r.src
    JOIN airports b ON b.iata = r.dst
)
SELECT from_country AS country,
       COUNT(*)                                                       AS airport_pairs,
       ROUND(100.0 * SUM(from_country = to_country) / COUNT(*), 1)    AS domestic_pct
FROM l
GROUP BY from_country
HAVING COUNT(*) >= 500
ORDER BY domestic_pct DESC;


-- 5. How far do the routes go?
WITH l AS (
    SELECT DISTINCT r.src, r.dst, 6371 * acos(MIN(1.0, MAX(-1.0, sin(radians(a.lat)) * sin(radians(b.lat)) + cos(radians(a.lat)) * cos(radians(b.lat)) * cos(radians(b.lon - a.lon))))) AS km
    FROM routes r
    JOIN airports a ON a.iata = r.src
    JOIN airports b ON b.iata = r.dst
)
SELECT CAST(km / 500 AS INT) * 500                                                AS from_km,
       COUNT(*)                                                                    AS pairs,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)                          AS pct,
       ROUND(100.0 * SUM(COUNT(*)) OVER (ORDER BY CAST(km / 500 AS INT)) / SUM(COUNT(*)) OVER (), 1) AS cumulative_pct
FROM l
GROUP BY from_km
ORDER BY from_km;


-- 6. What are the longest routes?
WITH pairs AS (
    SELECT DISTINCT MIN(src, dst) AS src, MAX(src, dst) AS dst FROM routes
)
SELECT r.src, r.dst, a.city AS from_city, b.city AS to_city, ROUND(6371 * acos(MIN(1.0, MAX(-1.0, sin(radians(a.lat)) * sin(radians(b.lat)) + cos(radians(a.lat)) * cos(radians(b.lat)) * cos(radians(b.lon - a.lon)))))) AS km
FROM pairs r
JOIN airports a ON a.iata = r.src
JOIN airports b ON b.iata = r.dst
ORDER BY km DESC
LIMIT 8;


-- 7. Hub airlines and point-to-point airlines
WITH names AS (
    SELECT iata, MIN(name) AS name
    FROM airlines
    WHERE active = 'Y' AND iata IS NOT NULL AND iata <> ''
    GROUP BY iata
    HAVING COUNT(*) = 1
),
leg AS (
    SELECT r.airline_code, r.src, 6371 * acos(MIN(1.0, MAX(-1.0, sin(radians(a.lat)) * sin(radians(b.lat)) + cos(radians(a.lat)) * cos(radians(b.lat)) * cos(radians(b.lon - a.lon))))) AS km
    FROM routes r
    JOIN airports a ON a.iata = r.src
    JOIN airports b ON b.iata = r.dst
    WHERE r.codeshare IS NULL AND r.airline_code IN (SELECT iata FROM names)
),
per_airport AS (
    SELECT airline_code, src, COUNT(*) AS n FROM leg GROUP BY airline_code, src
),
hub AS (
    SELECT airline_code, SUM(n) AS routes, COUNT(*) AS airports_served, MAX(n) AS top_n
    FROM per_airport GROUP BY airline_code
),
top AS (
    SELECT airline_code, src AS top_airport
    FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY airline_code ORDER BY n DESC, src) AS rk FROM per_airport)
    WHERE rk = 1
),
dist AS (
    SELECT airline_code, ROUND(AVG(km)) AS avg_km FROM leg GROUP BY airline_code
)
SELECT n.name AS airline, h.airline_code AS code, h.routes, h.airports_served, t.top_airport,
       ROUND(100.0 * h.top_n / h.routes, 1) AS top_airport_share_pct, d.avg_km
FROM hub h
JOIN names n ON n.iata = h.airline_code
JOIN top   t USING (airline_code)
JOIN dist  d USING (airline_code)
WHERE h.routes >= 300
ORDER BY h.routes DESC;


-- 8. How many flights to anywhere? A recursive walk through the network
WITH RECURSIVE starts(a) AS (
    VALUES ('WAW'), ('ATL'), ('LHR'), ('SIN'), ('JFK')
),
hops(start, airport, n) AS (
    SELECT a, a, 0 FROM starts
    UNION
    SELECT h.start, r.dst, h.n + 1
    FROM hops h
    JOIN routes r ON r.src = h.airport
    WHERE h.n < 6
),
best AS (
    SELECT start, airport, MIN(n) AS n FROM hops GROUP BY start, airport
),
network AS (
    SELECT COUNT(*) AS airports FROM (SELECT src FROM routes UNION SELECT dst FROM routes)
)
SELECT start, n AS flights, COUNT(*) AS new_airports,
       ROUND(100.0 * SUM(COUNT(*)) OVER (PARTITION BY start ORDER BY n) / (SELECT airports FROM network), 1) AS reached_pct
FROM best
GROUP BY start, n
ORDER BY start, n;


-- 9. The shortest itinerary from Warsaw
WITH RECURSIVE reach(airport, n) AS (
    SELECT 'WAW', 0
    UNION
    SELECT r.dst, h.n + 1 FROM reach h JOIN routes r ON r.src = h.airport WHERE h.n < 6
),
best AS (
    SELECT airport, MIN(n) AS n FROM reach GROUP BY airport
),
pred AS (
    SELECT r.dst AS airport, b.n + 1 AS n, MIN(r.src) AS src
    FROM routes r JOIN best b ON b.airport = r.src
    GROUP BY r.dst, b.n
),
target(airport) AS (
    VALUES ('IPC'), ('USH'), ('LYR'), ('PPT'), ('LXA'), ('ULN'), ('MLE'), ('KTM'), ('GUM'), ('HNL')
),
back(airport, n, flights, path) AS (
    SELECT b.airport, b.n, b.n, b.airport FROM best b JOIN target t USING (airport)
    UNION ALL
    SELECT p.src, k.n - 1, k.flights, p.src || ' > ' || k.path
    FROM back k
    JOIN pred p ON p.airport = k.airport AND p.n = k.n
    WHERE k.n > 0
)
SELECT SUBSTR(path, -3) AS destination, a.city, a.country, flights, path AS itinerary
FROM back
JOIN airports a ON a.iata = SUBSTR(path, -3)
WHERE n = 0
ORDER BY flights DESC, destination;


-- 10. The highest airports with scheduled flights
WITH served AS (
    SELECT src AS iata FROM routes UNION SELECT dst FROM routes
)
SELECT RANK() OVER (ORDER BY altitude_ft DESC) AS altitude_rank, name, city, country, altitude_ft, ROUND(altitude_ft * 0.3048) AS altitude_m
FROM airports
WHERE iata IN (SELECT iata FROM served)
ORDER BY altitude_ft DESC
LIMIT 8;


-- 11. How complete is the data?
WITH p AS (
    SELECT DISTINCT src, dst FROM routes
),
used AS (
    SELECT src AS iata FROM routes UNION SELECT dst FROM routes
)
SELECT (SELECT COUNT(*) FROM p)                                                                              AS airport_pairs,
       (SELECT COUNT(*) FROM p a WHERE NOT EXISTS (SELECT 1 FROM p b WHERE b.src = a.dst AND b.dst = a.src)) AS pairs_without_return,
       (SELECT COUNT(*) FROM airports WHERE iata IS NOT NULL)                                               AS airports_with_code,
       (SELECT COUNT(*) FROM airports WHERE iata IS NOT NULL AND iata NOT IN (SELECT iata FROM used))       AS airports_without_routes,
       (SELECT COUNT(*) FROM routes WHERE airline_id IS NULL)                                               AS routes_without_airline_id,
       (SELECT COUNT(*) FROM routes r
         WHERE NOT EXISTS (SELECT 1 FROM airports a WHERE a.iata = r.src)
            OR NOT EXISTS (SELECT 1 FROM airports a WHERE a.iata = r.dst))                                  AS routes_with_unknown_airport;
