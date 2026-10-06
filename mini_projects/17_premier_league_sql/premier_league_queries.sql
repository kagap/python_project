-- 25 seasons of the Premier League, in plain SQL (SQLite dialect)
-- Run against premier_league.db: https://www.football-data.co.uk/englandm.php

-- 1. How big is home advantage, and what happened without crowds?
SELECT season,
       COUNT(*)                                             AS matches,
       ROUND(100.0 * AVG(result = 'H'), 1)                  AS home_win_pct,
       ROUND(100.0 * AVG(result = 'D'), 1)                  AS draw_pct,
       ROUND(100.0 * AVG(result = 'A'), 1)                  AS away_win_pct,
       ROUND(AVG(home_goals + away_goals), 2)               AS goals_per_match,
       ROUND(AVG(home_goals) - AVG(away_goals), 2)          AS home_goal_edge
FROM matches
GROUP BY season
ORDER BY season;


-- 2. Who won the league, and by how much?
WITH team_matches AS (
    SELECT season, match_date, home_team AS team, away_team AS opponent, 1 AS is_home,
           home_goals AS gf, away_goals AS ga, ht_home_goals AS ht_gf, ht_away_goals AS ht_ga,
           CASE result WHEN 'H' THEN 3 WHEN 'D' THEN 1 ELSE 0 END AS points
    FROM matches
    UNION ALL
    SELECT season, match_date, away_team, home_team, 0,
           away_goals, home_goals, ht_away_goals, ht_home_goals,
           CASE result WHEN 'A' THEN 3 WHEN 'D' THEN 1 ELSE 0 END
    FROM matches
),
table_ AS (
    SELECT season, team, SUM(points) AS pts, SUM(gf) - SUM(ga) AS gd
    FROM team_matches
    GROUP BY season, team
),
ranked AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY season ORDER BY pts DESC, gd DESC) AS pos,
           LEAD(team)   OVER (PARTITION BY season ORDER BY pts DESC, gd DESC) AS runner_up,
           LEAD(pts)    OVER (PARTITION BY season ORDER BY pts DESC, gd DESC) AS runner_up_pts
    FROM table_
)
SELECT season, team AS champion, pts AS points, gd AS goal_difference,
       runner_up, pts - runner_up_pts AS margin
FROM ranked
WHERE pos = 1
ORDER BY season;


-- 3. How did Leicester's 2015/16 title race unfold?
WITH team_matches AS (
    SELECT season, match_date, home_team AS team, away_team AS opponent, 1 AS is_home,
           home_goals AS gf, away_goals AS ga, ht_home_goals AS ht_gf, ht_away_goals AS ht_ga,
           CASE result WHEN 'H' THEN 3 WHEN 'D' THEN 1 ELSE 0 END AS points
    FROM matches
    UNION ALL
    SELECT season, match_date, away_team, home_team, 0,
           away_goals, home_goals, ht_away_goals, ht_home_goals,
           CASE result WHEN 'A' THEN 3 WHEN 'D' THEN 1 ELSE 0 END
    FROM matches
),
games AS (
    SELECT team, points,
           ROW_NUMBER() OVER (PARTITION BY team ORDER BY match_date)        AS matchweek,
           SUM(points)  OVER (PARTITION BY team ORDER BY match_date)        AS cumulative_points
    FROM team_matches
    WHERE season = '2015/16'
),
top3 AS (
    SELECT team FROM games WHERE matchweek = 38 ORDER BY cumulative_points DESC LIMIT 3
)
SELECT matchweek, team, cumulative_points
FROM games
WHERE team IN (SELECT team FROM top3)
ORDER BY team, matchweek;


-- 4. What were the longest unbeaten runs?
WITH team_matches AS (
    SELECT season, match_date, home_team AS team, away_team AS opponent, 1 AS is_home,
           home_goals AS gf, away_goals AS ga, ht_home_goals AS ht_gf, ht_away_goals AS ht_ga,
           CASE result WHEN 'H' THEN 3 WHEN 'D' THEN 1 ELSE 0 END AS points
    FROM matches
    UNION ALL
    SELECT season, match_date, away_team, home_team, 0,
           away_goals, home_goals, ht_away_goals, ht_home_goals,
           CASE result WHEN 'A' THEN 3 WHEN 'D' THEN 1 ELSE 0 END
    FROM matches
),
ordered AS (
    SELECT team, match_date, points > 0 AS unbeaten,
           ROW_NUMBER() OVER (PARTITION BY team ORDER BY match_date) AS game_no
    FROM team_matches
),
numbered AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY team, unbeaten ORDER BY match_date) AS flag_no
    FROM ordered
)
SELECT team,
       COUNT(*)        AS matches_unbeaten,
       MIN(match_date) AS from_date,
       MAX(match_date) AS to_date
FROM numbered
WHERE unbeaten = 1
GROUP BY team, game_no - flag_no
ORDER BY matches_unbeaten DESC, from_date
LIMIT 8;


-- 5. Who comes back from behind?
WITH team_matches AS (
    SELECT season, match_date, home_team AS team, away_team AS opponent, 1 AS is_home,
           home_goals AS gf, away_goals AS ga, ht_home_goals AS ht_gf, ht_away_goals AS ht_ga,
           CASE result WHEN 'H' THEN 3 WHEN 'D' THEN 1 ELSE 0 END AS points
    FROM matches
    UNION ALL
    SELECT season, match_date, away_team, home_team, 0,
           away_goals, home_goals, ht_away_goals, ht_home_goals,
           CASE result WHEN 'A' THEN 3 WHEN 'D' THEN 1 ELSE 0 END
    FROM matches
)
SELECT team,
       COUNT(*)                                               AS matches,
       SUM(ht_gf < ht_ga AND points = 3)                      AS comeback_wins,
       SUM(ht_gf > ht_ga AND points = 0)                      AS blown_leads,
       SUM(ht_gf < ht_ga AND points = 3)
         - SUM(ht_gf > ht_ga AND points = 0)                  AS net
FROM team_matches
GROUP BY team
HAVING matches >= 380
ORDER BY comeback_wins DESC
LIMIT 12;


-- 6. Are the bookmakers' odds well calibrated?
WITH probs AS (
    SELECT result,
           (1.0 / odds_home) / (1.0 / odds_home + 1.0 / odds_draw + 1.0 / odds_away) AS p_home
    FROM matches
    WHERE odds_home IS NOT NULL AND odds_draw IS NOT NULL AND odds_away IS NOT NULL
)
SELECT MIN(CAST(p_home * 10 AS INT), 8) * 10                  AS bucket_from_pct,
       COUNT(*)                                               AS matches,
       ROUND(100.0 * AVG(p_home), 1)                          AS predicted_home_win_pct,
       ROUND(100.0 * AVG(result = 'H'), 1)                    AS actual_home_win_pct
FROM probs
GROUP BY bucket_from_pct
ORDER BY bucket_from_pct;


-- 7. Which referees show the most cards?
SELECT referee,
       COUNT(*)                                                     AS matches,
       ROUND(AVG(home_yellow + away_yellow + home_red + away_red), 2) AS cards_per_match,
       ROUND(AVG(home_fouls + away_fouls), 1)                       AS fouls_per_match,
       ROUND(AVG(home_yellow) - AVG(away_yellow), 2)                AS home_minus_away_yellows
FROM matches
WHERE referee IS NOT NULL
GROUP BY referee
HAVING matches >= 150
ORDER BY cards_per_match DESC;


-- 8. Which teams depend most on their own ground?
WITH team_matches AS (
    SELECT season, match_date, home_team AS team, away_team AS opponent, 1 AS is_home,
           home_goals AS gf, away_goals AS ga, ht_home_goals AS ht_gf, ht_away_goals AS ht_ga,
           CASE result WHEN 'H' THEN 3 WHEN 'D' THEN 1 ELSE 0 END AS points
    FROM matches
    UNION ALL
    SELECT season, match_date, away_team, home_team, 0,
           away_goals, home_goals, ht_away_goals, ht_home_goals,
           CASE result WHEN 'A' THEN 3 WHEN 'D' THEN 1 ELSE 0 END
    FROM matches
)
SELECT team,
       SUM(is_home)                                                       AS home_games,
       ROUND(1.0 * SUM(CASE WHEN is_home = 1 THEN points END) / SUM(is_home), 2)     AS home_points_per_game,
       ROUND(1.0 * SUM(CASE WHEN is_home = 0 THEN points END) / SUM(1 - is_home), 2) AS away_points_per_game,
       ROUND(1.0 * SUM(CASE WHEN is_home = 1 THEN points END) / SUM(is_home)
           - 1.0 * SUM(CASE WHEN is_home = 0 THEN points END) / SUM(1 - is_home), 2) AS home_bonus
FROM team_matches
GROUP BY team
HAVING home_games >= 95
ORDER BY home_bonus DESC;
