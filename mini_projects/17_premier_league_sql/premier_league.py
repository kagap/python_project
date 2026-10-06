"""25 seasons of the Premier League, in plain SQL.

Plain-script version of the notebook. Set PL_DB to the path of premier_league.db.
"""


import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DB_PATH = Path(os.environ.get("PL_DB", "premier_league.db"))
AMBER, BLUE, GREY = "#e8a33d", "#2b6cb0", "#a0aec0"
plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
                     "grid.alpha": 0.25, "figure.dpi": 110})
pd.set_option("display.width", 120)
pd.set_option("display.max_columns", 20)

con = sqlite3.connect(DB_PATH)


def q(sql):
    """Run a query and return the result as a DataFrame."""
    return pd.read_sql_query(sql, con)

tables = q("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").name
pd.DataFrame({"table": tables,
              "rows": [con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables]})

home = q("""
SELECT season,
       COUNT(*)                                             AS matches,
       ROUND(100.0 * AVG(result = 'H'), 1)                  AS home_win_pct,
       ROUND(100.0 * AVG(result = 'D'), 1)                  AS draw_pct,
       ROUND(100.0 * AVG(result = 'A'), 1)                  AS away_win_pct,
       ROUND(AVG(home_goals + away_goals), 2)               AS goals_per_match,
       ROUND(AVG(home_goals) - AVG(away_goals), 2)          AS home_goal_edge
FROM matches
GROUP BY season
ORDER BY season
""")

fig, ax = plt.subplots(figsize=(10, 4))
ax.plot(home.season, home.home_win_pct, marker="o", ms=4, color=AMBER, label="Home wins")
ax.plot(home.season, home.away_win_pct, marker="o", ms=4, color=BLUE, label="Away wins")
ax.plot(home.season, home.draw_pct, marker="o", ms=4, color=GREY, label="Draws")
i = home.season.tolist().index("2020/21")
ax.axvspan(i - 0.5, i + 0.5, color=GREY, alpha=0.2)
ax.text(i, 52.5, "closed doors", ha="center", fontsize=8)
ax.set_ylim(17, 55)
ax.set_ylabel("% of matches")
ax.tick_params(axis="x", rotation=60)
ax.set_title("Home wins, draws and away wins by season")
ax.legend(frameon=False, ncol=3)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
home.iloc[[0, 10, 20, -4]]

champions = q("""
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
ORDER BY season
""")

fig, ax = plt.subplots(figsize=(10, 4.4))
colors = {"Man United": "#c0392b", "Chelsea": BLUE, "Man City": "#5dade2", "Arsenal": "#e74c3c",
          "Liverpool": "#922b21", "Leicester": "#2e86c1"}
ax.bar(champions.season, champions.points, color=[colors.get(t, GREY) for t in champions.champion])
for x, (t, p) in enumerate(zip(champions.champion, champions.points)):
    ax.text(x, p + 1, t, rotation=90, ha="center", va="bottom", fontsize=7)
ax.set_ylim(60, 115)
ax.set_ylabel("Champion's points")
ax.tick_params(axis="x", rotation=60)
ax.set_title("Champions and their points total")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
champions.groupby("champion").size().sort_values(ascending=False).to_frame("titles")

race = q("""
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
ORDER BY team, matchweek
""")

fig, ax = plt.subplots(figsize=(9.5, 4.4))
for team, g in race.groupby("team"):
    ax.plot(g.matchweek, g.cumulative_points, lw=3 if team == "Leicester" else 1.8,
            color=AMBER if team == "Leicester" else None, label=team)
ax.set_xlabel("Matchweek")
ax.set_ylabel("Points")
ax.set_title("Points after each match, 2015/16 top three")
ax.legend(frameon=False)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
race[race.matchweek == 38]

streaks = q("""
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
LIMIT 8
""")

streaks

comebacks = q("""
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
LIMIT 12
""")

fig, ax = plt.subplots(figsize=(9, 4.6))
y = np.arange(len(comebacks))
ax.barh(y + 0.2, comebacks.comeback_wins, height=0.4, color=AMBER, label="Comeback wins")
ax.barh(y - 0.2, comebacks.blown_leads, height=0.4, color=BLUE, label="Blown leads")
ax.set_yticks(y, comebacks.team)
ax.invert_yaxis()
ax.set_title("Comebacks and blown half-time leads, 2000/01 to 2024/25")
ax.legend(frameon=False)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
comebacks

odds = q("""
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
ORDER BY bucket_from_pct
""")

fig, ax = plt.subplots(figsize=(6.5, 5))
ax.plot([0, 100], [0, 100], color=GREY, ls="--", label="Perfect calibration")
ax.plot(odds.predicted_home_win_pct, odds.actual_home_win_pct, marker="o", color=AMBER, lw=2, label="Premier League")
for _, r in odds.iterrows():
    ax.annotate(f"{int(r.matches):,}", (r.predicted_home_win_pct, r.actual_home_win_pct), textcoords="offset points",
                xytext=(6, -10), fontsize=7)
ax.set_xlabel("Chance of a home win implied by the odds (%)")
ax.set_ylabel("How often the home team won (%)")
ax.set_title("Bookmaker odds against reality (labels = matches)")
ax.legend(frameon=False)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
odds

referees = q("""
SELECT referee,
       COUNT(*)                                                     AS matches,
       ROUND(AVG(home_yellow + away_yellow + home_red + away_red), 2) AS cards_per_match,
       ROUND(AVG(home_fouls + away_fouls), 1)                       AS fouls_per_match,
       ROUND(AVG(home_yellow) - AVG(away_yellow), 2)                AS home_minus_away_yellows
FROM matches
WHERE referee IS NOT NULL
GROUP BY referee
HAVING matches >= 150
ORDER BY cards_per_match DESC
""")

fig, ax = plt.subplots(figsize=(9, 5.6))
ax.barh(referees.referee[::-1], referees.cards_per_match[::-1], color=AMBER)
for y, v in enumerate(referees.cards_per_match[::-1]):
    ax.text(v + 0.03, y, f"{v:.2f}", va="center", fontsize=8)
ax.set_xlim(0, referees.cards_per_match.max() * 1.12)
ax.set_title("Cards per match by referee (150+ matches)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
referees[["referee", "home_minus_away_yellows"]].describe().round(2).T

form = q("""
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
ORDER BY home_bonus DESC
""")

d = form.sort_values("home_bonus")
fig, ax = plt.subplots(figsize=(9, 7))
y = np.arange(len(d))
ax.hlines(y, d.away_points_per_game, d.home_points_per_game, color=GREY)
ax.scatter(d.home_points_per_game, y, color=AMBER, label="Home", zorder=3)
ax.scatter(d.away_points_per_game, y, color=BLUE, label="Away", zorder=3)
ax.set_yticks(y, d.team, fontsize=8)
ax.set_xlabel("Points per game")
ax.set_title("Home and away points per game (teams with 5+ seasons)")
ax.legend(frameon=False)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
form.head(5)
