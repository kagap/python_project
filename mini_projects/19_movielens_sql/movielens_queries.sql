-- 100,000 movie ratings, in plain SQL (SQLite dialect)
-- Run against movielens.db: https://grouplens.org/datasets/movielens/latest/

-- 1. How do people rate movies?
SELECT rating,
       COUNT(*)                                            AS ratings,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)  AS share_pct
FROM ratings
GROUP BY rating
ORDER BY rating;


-- 2. What are the best movies, once one-vote wonders are handled?
WITH stats AS (
    SELECT movie_id, COUNT(*) AS votes, AVG(rating) AS avg_rating
    FROM ratings
    GROUP BY movie_id
),
scored AS (
    SELECT movie_id, votes, avg_rating,
           RANK() OVER (ORDER BY avg_rating DESC) AS raw_rank,
           1.0 * votes / (votes + 50) * avg_rating
             + 50.0 / (votes + 50) * (SELECT AVG(rating) FROM ratings) AS weighted_rating
    FROM stats
)
SELECT m.title,
       s.votes,
       ROUND(s.avg_rating, 2)       AS avg_rating,
       ROUND(s.weighted_rating, 2)  AS weighted_rating,
       s.raw_rank
FROM scored s
JOIN movies m USING (movie_id)
WHERE s.votes >= 50
ORDER BY s.weighted_rating DESC
LIMIT 10;


-- 3. Which genres are watched most, and rated best?
SELECT g.genre,
       COUNT(DISTINCT g.movie_id)                          AS movies,
       COUNT(*)                                            AS ratings,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)  AS share_of_genre_ratings_pct,
       ROUND(AVG(r.rating), 2)                             AS avg_rating
FROM movie_genres g
JOIN ratings r USING (movie_id)
GROUP BY g.genre
ORDER BY ratings DESC;


-- 4. Has rating behaviour changed over the years?
WITH yearly AS (
    SELECT CAST(substr(rated_at, 1, 4) AS INT)  AS year,
           COUNT(*)                             AS ratings,
           COUNT(DISTINCT user_id)              AS active_users,
           ROUND(AVG(rating), 2)                AS avg_rating
    FROM ratings
    GROUP BY year
)
SELECT year, ratings, active_users, avg_rating,
       ROUND(avg_rating - LAG(avg_rating) OVER (ORDER BY year), 2) AS change_vs_prev_year
FROM yearly
ORDER BY year;


-- 5. How concentrated is the activity?
WITH per_user AS (
    SELECT user_id, COUNT(*) AS ratings, AVG(rating) AS avg_rating
    FROM ratings
    GROUP BY user_id
),
bucketed AS (
    SELECT *, NTILE(10) OVER (ORDER BY ratings DESC) AS decile
    FROM per_user
)
SELECT decile,
       COUNT(*)                                                             AS users,
       SUM(ratings)                                                         AS ratings,
       ROUND(100.0 * SUM(ratings) / SUM(SUM(ratings)) OVER (), 1)           AS share_pct,
       ROUND(100.0 * SUM(SUM(ratings)) OVER (ORDER BY decile)
                   / SUM(SUM(ratings)) OVER (), 1)                          AS cumulative_pct,
       ROUND(AVG(avg_rating), 2)                                            AS avg_rating
FROM bucketed
GROUP BY decile
ORDER BY decile;


-- 6. Are older movies rated higher?
SELECT (m.release_year / 10) * 10          AS decade,
       COUNT(DISTINCT m.movie_id)          AS movies,
       COUNT(*)                            AS ratings,
       ROUND(AVG(r.rating), 2)             AS avg_rating
FROM movies m
JOIN ratings r USING (movie_id)
WHERE m.release_year >= 1920
GROUP BY decade
ORDER BY decade;


-- 7. Which movies split the audience?
SELECT m.title,
       COUNT(*)                                                      AS ratings,
       ROUND(AVG(r.rating), 2)                                       AS avg_rating,
       ROUND(sqrt(AVG(r.rating * r.rating) - AVG(r.rating) * AVG(r.rating)), 2) AS spread,
       ROUND(100.0 * AVG(r.rating <= 2), 0)                          AS rated_2_or_less_pct,
       ROUND(100.0 * AVG(r.rating >= 4.5), 0)                        AS rated_4_5_or_more_pct
FROM ratings r
JOIN movies m USING (movie_id)
GROUP BY r.movie_id
HAVING ratings >= 100
ORDER BY spread DESC
LIMIT 10;
