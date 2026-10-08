-- Every ball of the Indian Premier League, in plain SQL (SQLite dialect)
-- Run against ipl.db: https://cricsheet.org/

-- 1. How has the game changed, season by season?
WITH innings AS (
    SELECT d.match_id, d.innings,
           SUM(d.runs_off_bat + d.extras)                           AS runs,
           SUM(CASE WHEN d.wides = 0 AND d.noballs = 0 THEN 1 ELSE 0 END) AS legal_balls,
           SUM(d.runs_off_bat = 6)                                  AS sixes
    FROM deliveries d
    WHERE d.innings <= 2
    GROUP BY d.match_id, d.innings
),
yearly AS (
    SELECT CAST(substr(m.date, 1, 4) AS INT)                                                  AS year,
           COUNT(DISTINCT m.match_id)                              AS matches,
           ROUND(AVG(CASE WHEN i.innings = 1 THEN i.runs END), 1)  AS avg_first_innings,
           ROUND(6.0 * SUM(i.runs) / SUM(i.legal_balls), 2)        AS runs_per_over,
           ROUND(1.0 * SUM(i.sixes) / COUNT(DISTINCT m.match_id), 1) AS sixes_per_match
    FROM innings i
    JOIN matches m USING (match_id)
    GROUP BY year
)
SELECT year, matches, avg_first_innings, runs_per_over, sixes_per_match,
       ROUND(avg_first_innings - LAG(avg_first_innings) OVER (ORDER BY year), 1) AS change_in_first_innings
FROM yearly
ORDER BY year;


-- 2. Who has scored the most runs?
WITH batting AS (
    SELECT striker                                         AS player,
           COUNT(DISTINCT match_id)                        AS matches,
           SUM(runs_off_bat)                               AS runs,
           SUM(CASE WHEN wides = 0 THEN 1 ELSE 0 END)      AS balls_faced,
           SUM(runs_off_bat = 4)                           AS fours,
           SUM(runs_off_bat = 6)                           AS sixes
    FROM deliveries
    WHERE innings <= 2
    GROUP BY striker
),
outs AS (
    SELECT player_dismissed AS player, COUNT(*) AS dismissals
    FROM deliveries
    WHERE innings <= 2 AND wicket_type IS NOT NULL AND wicket_type NOT IN ('retired hurt', 'retired out')
    GROUP BY player_dismissed
)
SELECT RANK() OVER (ORDER BY b.runs DESC)                 AS rank,
       b.player, b.matches, b.runs,
       ROUND(1.0 * b.runs / NULLIF(o.dismissals, 0), 1)   AS average,
       ROUND(100.0 * b.runs / b.balls_faced, 1)           AS strike_rate,
       b.fours, b.sixes
FROM batting b
LEFT JOIN outs o USING (player)
ORDER BY b.runs DESC
LIMIT 15;


-- 3. Who has taken the most wickets, and at what cost?
WITH bowling AS (
    SELECT bowler,
           COUNT(DISTINCT match_id)                                           AS matches,
           SUM(CASE WHEN wides = 0 AND noballs = 0 THEN 1 ELSE 0 END)         AS legal_balls,
           SUM(runs_off_bat + wides + noballs)                                AS runs_conceded,
           SUM(CASE WHEN wicket_type IN ('bowled', 'caught', 'lbw', 'stumped', 'caught and bowled', 'hit wicket') THEN 1 ELSE 0 END)   AS wickets
    FROM deliveries
    WHERE innings <= 2
    GROUP BY bowler
    HAVING legal_balls >= 1000
)
SELECT RANK() OVER (ORDER BY wickets DESC)           AS rank,
       bowler, matches, wickets,
       ROUND(6.0 * runs_conceded / legal_balls, 2)   AS economy,
       ROUND(1.0 * runs_conceded / wickets, 1)       AS average,
       ROUND(1.0 * legal_balls / wickets, 1)         AS balls_per_wicket
FROM bowling
ORDER BY wickets DESC
LIMIT 15;


-- 4. How do the powerplay, the middle overs and the death overs differ?
WITH phased AS (
    SELECT CAST(substr(m.date, 1, 4) AS INT) AS year,
           CASE WHEN d.over < 6  THEN '1 Powerplay (overs 1-6)'
                WHEN d.over < 15 THEN '2 Middle (overs 7-15)'
                ELSE                  '3 Death (overs 16-20)' END                  AS phase,
           d.match_id,
           d.runs_off_bat + d.extras                                               AS runs,
           CASE WHEN d.wides = 0 AND d.noballs = 0 THEN 1 ELSE 0 END               AS legal,
           CASE WHEN d.wicket_type IS NOT NULL
                 AND d.wicket_type NOT IN ('retired hurt', 'retired out') THEN 1 ELSE 0 END AS wicket
    FROM deliveries d
    JOIN matches m USING (match_id)
    WHERE d.innings <= 2
)
SELECT year,
       substr(phase, 3)                                                     AS phase,
       ROUND(6.0 * SUM(runs) / SUM(legal), 2)                               AS runs_per_over,
       ROUND(1.0 * SUM(wicket) / (2 * COUNT(DISTINCT match_id)), 2)         AS wickets_per_innings
FROM phased
GROUP BY year, phase
ORDER BY year, phase;


-- 5. Is it easier to chase, and does it depend on the ground?
WITH second AS (
    SELECT DISTINCT match_id, batting_team AS chasing_team
    FROM deliveries
    WHERE innings = 2
)
SELECT m.venue,
       COUNT(*)                                                       AS matches,
       SUM(m.winner = s.chasing_team)                                 AS won_by_chasing_side,
       ROUND(100.0 * SUM(m.winner = s.chasing_team) / COUNT(*), 1)    AS chasing_win_pct
FROM matches m
JOIN second s USING (match_id)
WHERE m.winner IS NOT NULL
GROUP BY m.venue
HAVING matches >= 25
ORDER BY chasing_win_pct DESC;


-- 6. Does winning the toss matter?
SELECT CAST(substr(date, 1, 4) AS INT)                                              AS year,
       COUNT(*)                                                                     AS matches,
       ROUND(100.0 * AVG(toss_decision = 'field'), 1)                               AS chose_to_field_pct,
       ROUND(100.0 * AVG(CASE WHEN winner IS NOT NULL THEN toss_winner = winner END), 1) AS toss_winner_won_pct
FROM matches
GROUP BY year
ORDER BY year;


-- 7. Who won the orange and purple caps each season?
WITH runs AS (
    SELECT CAST(substr(m.date, 1, 4) AS INT) AS year, d.striker AS player, SUM(d.runs_off_bat) AS runs
    FROM deliveries d JOIN matches m USING (match_id)
    WHERE d.innings <= 2
    GROUP BY year, d.striker
),
top_bat AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY year ORDER BY runs DESC) AS rn FROM runs
),
wickets AS (
    SELECT CAST(substr(m.date, 1, 4) AS INT) AS year, d.bowler AS player,
           SUM(CASE WHEN d.wicket_type IN ('bowled', 'caught', 'lbw', 'stumped', 'caught and bowled', 'hit wicket') THEN 1 ELSE 0 END) AS wickets
    FROM deliveries d JOIN matches m USING (match_id)
    WHERE d.innings <= 2
    GROUP BY year, d.bowler
),
top_bowl AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY year ORDER BY wickets DESC) AS rn FROM wickets
)
SELECT b.year,
       b.player AS top_run_scorer, b.runs,
       w.player AS top_wicket_taker, w.wickets
FROM top_bat b
JOIN top_bowl w ON w.year = b.year AND w.rn = 1
WHERE b.rn = 1
ORDER BY b.year;


-- 8. Which bowlers keep getting the same batter out?
SELECT RANK() OVER (ORDER BY SUM(CASE WHEN player_dismissed = striker AND wicket_type IN ('bowled', 'caught', 'lbw', 'stumped', 'caught and bowled', 'hit wicket') THEN 1 ELSE 0 END) DESC,
                             SUM(CASE WHEN wides = 0 THEN 1 ELSE 0 END) DESC)         AS rank,
       striker                                                                        AS batter,
       bowler,
       SUM(CASE WHEN wides = 0 THEN 1 ELSE 0 END)                                     AS balls,
       SUM(runs_off_bat)                                                              AS runs,
       SUM(CASE WHEN player_dismissed = striker AND wicket_type IN ('bowled', 'caught', 'lbw', 'stumped', 'caught and bowled', 'hit wicket') THEN 1 ELSE 0 END) AS dismissals,
       ROUND(100.0 * SUM(runs_off_bat) / SUM(CASE WHEN wides = 0 THEN 1 ELSE 0 END), 0) AS strike_rate
FROM deliveries
WHERE innings <= 2
GROUP BY striker, bowler
HAVING balls >= 40
ORDER BY dismissals DESC, balls DESC
LIMIT 12;


-- 9. How do the great careers build up?
WITH yearly AS (
    SELECT d.striker AS player, CAST(substr(m.date, 1, 4) AS INT) AS year, SUM(d.runs_off_bat) AS runs
    FROM deliveries d JOIN matches m USING (match_id)
    WHERE d.innings <= 2
    GROUP BY d.striker, year
),
top5 AS (
    SELECT striker FROM deliveries WHERE innings <= 2 GROUP BY striker ORDER BY SUM(runs_off_bat) DESC LIMIT 5
)
SELECT player, year, runs,
       SUM(runs) OVER (PARTITION BY player ORDER BY year) AS career_runs
FROM yearly
WHERE player IN (SELECT striker FROM top5)
ORDER BY player, year;
