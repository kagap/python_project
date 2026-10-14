-- Twenty thousand board games, in plain SQL (SQLite dialect)
-- Run against boardgames.db: https://github.com/rfordatascience/tidytuesday/tree/master/data/2022/2022-01-25
-- The views game_mechanics, game_categories and game_designers are created by build_db.py (query 5 shows the recursive CTE they use).

-- 1. The publishing boom
WITH y AS (
    SELECT year, COUNT(*) AS games FROM games WHERE year BETWEEN 1980 AND 2021 GROUP BY year
)
SELECT year, games,
       ROUND(AVG(games) OVER (ORDER BY year ROWS BETWEEN 4 PRECEDING AND CURRENT ROW), 1)       AS games_5yr_avg,
       ROUND(100.0 * (games - LAG(games) OVER (ORDER BY year)) / LAG(games) OVER (ORDER BY year), 1) AS growth_pct
FROM y
ORDER BY year;


-- 2. Do newer games get better ratings?
SELECT year,
       COUNT(*)                                       AS games,
       ROUND(AVG(avg_rating), 2)                      AS avg_rating,
       ROUND(AVG(bayes_rating), 2)                    AS avg_bayes_rating,
       CAST(ROUND(AVG(users_rated)) AS INT)             AS ratings_per_game,
       ROUND(100.0 * AVG(avg_rating >= 7.0), 1)       AS pct_rated_7_plus
FROM games
WHERE year BETWEEN 1990 AND 2021
GROUP BY year
ORDER BY year;


-- 3. Reverse-engineering the Bayesian average
WITH RECURSIVE p AS (
    SELECT bayes_rating AS x, users_rated * (bayes_rating - avg_rating) AS y FROM games WHERE bayes_rating > 0
),
s AS (
    SELECT COUNT(*) AS n, SUM(x) AS sx, SUM(y) AS sy, SUM(x * x) AS sxx, SUM(x * y) AS sxy, SUM(y * y) AS syy FROM p
),
fit AS (
    SELECT n, sx, sy, (n * sxy - sx * sy) / (n * sxx - sx * sx)                                       AS slope,
           (n * sxy - sx * sy) / SQRT((n * sxx - sx * sx) * (n * syy - sy * sy))                     AS r
    FROM s
),
est AS (
    SELECT -slope AS k, ((sy - slope * sx) / n) / -slope AS m, r * r AS r2 FROM fit
),
series(avg_rating) AS (
    SELECT 7.25 UNION ALL SELECT avg_rating + 0.25 FROM series WHERE avg_rating < 9.0
)
SELECT CAST(ROUND(e.k) AS INT)                             AS dummy_ratings,
       ROUND(e.m, 2)                                       AS dummy_rating_mean,
       ROUND(e.r2, 2)                                      AS r_squared,
       s.avg_rating,
       CAST(ROUND(e.k * (7.0 - e.m) / (s.avg_rating - 7.0)) AS INT) AS ratings_needed_for_bayes_7
FROM est e
CROSS JOIN series s
ORDER BY s.avg_rating;


-- 4. One-vote wonders: places lost between the raw and the adjusted ranking
WITH r AS (
    SELECT name, year, avg_rating, bayes_rating, users_rated,
           RANK() OVER (ORDER BY avg_rating DESC)   AS rank_raw,
           RANK() OVER (ORDER BY bayes_rating DESC) AS rank_bayes
    FROM games
)
SELECT name, year, avg_rating, users_rated, rank_raw, rank_bayes, rank_bayes - rank_raw AS places_lost
FROM r
WHERE rank_raw <= 100
ORDER BY places_lost DESC
LIMIT 8;


-- 5. From a text to rows: a recursive split
WITH RECURSIVE split(item, rest) AS (
    SELECT '', mechanics || '|' FROM games WHERE game_id = 30549
    UNION ALL
    SELECT substr(rest, 1, instr(rest, '|') - 1), substr(rest, instr(rest, '|') + 1)
    FROM split
    WHERE rest <> ''
)
SELECT item AS mechanic FROM split WHERE item <> '';


-- 6. Which mechanics go with the best-rated games?
SELECT m.mechanic,
       COUNT(*)                                       AS games,
       ROUND(AVG(g.avg_rating), 2)                    AS avg_rating,
       ROUND(100.0 * AVG(g.rank <= 1000), 1)          AS pct_in_top1000,
       CAST(ROUND(AVG(g.year)) AS INT)                  AS avg_year
FROM game_mechanics m
JOIN games g USING (game_id)
GROUP BY m.mechanic
HAVING COUNT(*) >= 500
ORDER BY avg_rating DESC;


-- 7. Rising and falling mechanics
WITH tot AS (
    SELECT SUM(year BETWEEN 2000 AND 2009) AS early, SUM(year BETWEEN 2015 AND 2021) AS late
    FROM games WHERE mechanics IS NOT NULL
),
m AS (
    SELECT mechanic, SUM(year BETWEEN 2000 AND 2009) AS early, SUM(year BETWEEN 2015 AND 2021) AS late
    FROM game_mechanics
    JOIN games USING (game_id)
    GROUP BY mechanic
)
SELECT m.mechanic, m.early AS games_2000_09, m.late AS games_2015_21,
       ROUND(100.0 * m.early / t.early, 1)                         AS share_2000_09_pct,
       ROUND(100.0 * m.late / t.late, 1)                           AS share_2015_21_pct,
       ROUND((1.0 * m.late / t.late) / (1.0 * m.early / t.early), 2) AS growth_x
FROM m CROSS JOIN tot t
WHERE m.early >= 40 AND m.late >= 100
ORDER BY growth_x DESC;


-- 8. Solo and cooperative games by year
SELECT year, COUNT(*) AS games,
       ROUND(100.0 * AVG(min_players = 1), 1)                          AS solo_capable_pct,
       ROUND(100.0 * AVG(mechanics LIKE '%Cooperative Game%'), 1)      AS cooperative_pct
FROM games
WHERE year BETWEEN 1995 AND 2021 AND mechanics IS NOT NULL
GROUP BY year
ORDER BY year;


-- 9. Designers with the best games
WITH d AS (
    SELECT s.designer, g.name, g.rank, g.bayes_rating, g.users_rated,
           ROW_NUMBER() OVER (PARTITION BY s.designer ORDER BY g.rank) AS rn
    FROM game_designers s
    JOIN games g USING (game_id)
    WHERE s.designer NOT LIKE '(%'
),
a AS (
    SELECT designer, COUNT(*) AS games, ROUND(AVG(bayes_rating), 2) AS avg_bayes_rating, SUM(rank <= 500) AS games_in_top500, SUM(users_rated) AS ratings
    FROM d GROUP BY designer HAVING COUNT(*) >= 6
)
SELECT a.designer, a.games, a.avg_bayes_rating, a.games_in_top500, a.ratings,
       (SELECT name FROM d WHERE d.designer = a.designer AND rn = 1) AS best_game
FROM a
ORDER BY a.avg_bayes_rating DESC
LIMIT 10;


-- 10. A long tail of ratings
WITH n AS (
    SELECT users_rated, NTILE(100) OVER (ORDER BY users_rated DESC) AS pct FROM games
)
SELECT pct AS top_percent_of_games, COUNT(*) AS games, SUM(users_rated) AS ratings, MAX(users_rated) AS most_ratings,
       ROUND(100.0 * SUM(SUM(users_rated)) OVER (ORDER BY pct) / SUM(SUM(users_rated)) OVER (), 1) AS cumulative_pct_of_ratings
FROM n
GROUP BY pct
ORDER BY pct;


-- 11. Which years made the best games?
SELECT year, COUNT(*) AS games_in_top500,
       ROUND(100.0 * SUM(COUNT(*)) OVER (ORDER BY year) / SUM(COUNT(*)) OVER (), 1) AS cumulative_pct
FROM games
WHERE rank <= 500 AND year IS NOT NULL
GROUP BY year
ORDER BY year;
