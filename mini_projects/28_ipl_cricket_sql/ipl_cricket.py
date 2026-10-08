"""Every ball of the Indian Premier League, in plain SQL.

Plain-script version of the notebook. Set IPL_DB to the path of ipl.db.
"""


import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DB_PATH = Path(os.environ.get("IPL_DB", "ipl.db"))
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

seasons = q("""
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
ORDER BY year
""")

fig, ax = plt.subplots(figsize=(10, 4))
ax.bar(seasons.year, seasons.sixes_per_match, color=GREY, label="Sixes per match")
ax.set_ylabel("Sixes per match")
ax2 = ax.twinx()
ax2.plot(seasons.year, seasons.avg_first_innings, color=AMBER, marker="o", lw=2.2, label="Average first-innings score")
ax2.set_ylabel("Average first-innings score")
ax2.set_ylim(130, seasons.avg_first_innings.max() * 1.05)
ax2.grid(False)
ax2.spines["right"].set_visible(True)
ax.set_title("More sixes, bigger totals")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
seasons.tail(6)

batters = q("""
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
LIMIT 15
""")

fig, ax = plt.subplots(figsize=(9, 5))
ax.barh(batters.player[::-1], batters.runs[::-1], color=AMBER)
for y, (r, sr) in enumerate(zip(batters.runs[::-1], batters.strike_rate[::-1])):
    ax.text(r + 40, y, f"{r:,}  (strike rate {sr:.0f})", va="center", fontsize=8)
ax.set_xlim(0, batters.runs.max() * 1.3)
ax.set_title("Top 15 run scorers in the IPL")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
batters

bowlers = q("""
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
LIMIT 15
""")

fig, ax = plt.subplots(figsize=(9, 5))
ax.barh(bowlers.bowler[::-1], bowlers.wickets[::-1], color=BLUE)
for y, (w, e) in enumerate(zip(bowlers.wickets[::-1], bowlers.economy[::-1])):
    ax.text(w + 1.5, y, f"{w}  (economy {e:.2f})", va="center", fontsize=8)
ax.set_xlim(0, bowlers.wickets.max() * 1.3)
ax.set_title("Top 15 wicket-takers in the IPL")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
bowlers

phases = q("""
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
ORDER BY year, phase
""")

wide = phases.pivot(index="year", columns="phase", values="runs_per_over")
fig, ax = plt.subplots(figsize=(9.5, 4))
for col, color in zip(wide.columns, [BLUE, GREY, AMBER]):
    ax.plot(wide.index, wide[col], marker="o", ms=4, lw=2.2, color=color, label=col)
ax.set_ylabel("Runs per over")
ax.set_title("Run rate by phase of the innings")
ax.legend(frameon=False, fontsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
phases[phases.year == phases.year.max()]

chase = q("""
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
ORDER BY chasing_win_pct DESC
""")

fig, ax = plt.subplots(figsize=(9, 4.8))
ax.barh(chase.venue[::-1], chase.chasing_win_pct[::-1], color=[AMBER if v >= 50 else BLUE for v in chase.chasing_win_pct[::-1]])
ax.axvline(50, color="#4a5568", lw=1, ls="--")
for y, (v, n) in enumerate(zip(chase.chasing_win_pct[::-1], chase.matches[::-1])):
    ax.text(v + 0.5, y, f"{v:.0f}%  ({n})", va="center", fontsize=8)
ax.set_xlim(0, 75)
ax.set_title("How often the team batting second won (grounds with 25+ matches)")
ax.tick_params(axis="y", labelsize=7)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
chase.agg({"matches": "sum", "won_by_chasing_side": "sum"})

toss = q("""
SELECT CAST(substr(date, 1, 4) AS INT)                                              AS year,
       COUNT(*)                                                                     AS matches,
       ROUND(100.0 * AVG(toss_decision = 'field'), 1)                               AS chose_to_field_pct,
       ROUND(100.0 * AVG(CASE WHEN winner IS NOT NULL THEN toss_winner = winner END), 1) AS toss_winner_won_pct
FROM matches
GROUP BY year
ORDER BY year
""")

fig, ax = plt.subplots(figsize=(10, 4))
ax.plot(toss.year, toss.chose_to_field_pct, marker="o", ms=4, lw=2.2, color=AMBER, label="Toss winners who chose to field (%)")
ax.plot(toss.year, toss.toss_winner_won_pct, marker="s", ms=4, lw=2.2, color=BLUE, label="Toss winners who won the match (%)")
ax.axhline(50, color="#4a5568", lw=0.8, ls="--")
ax.set_ylim(30, 100)
ax.set_title("The toss: what captains choose, and what it is worth")
ax.legend(frameon=False, fontsize=8, loc="lower right")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
toss[["matches", "chose_to_field_pct", "toss_winner_won_pct"]].describe().round(1).loc[["mean", "min", "max"]]

caps = q("""
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
ORDER BY b.year
""")

caps

duels = q("""
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
LIMIT 12
""")

duels

career = q("""
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
ORDER BY player, year
""")

fig, ax = plt.subplots(figsize=(10, 4.6))
for player, g in career.groupby("player"):
    ax.plot(g.year, g.career_runs, marker="o", ms=3, lw=2, label=player)
ax.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
ax.set_ylabel("Career runs in the IPL")
ax.set_title("Career run totals of the five leading scorers")
ax.legend(frameon=False, fontsize=8, ncol=2)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
career.sort_values("career_runs", ascending=False).groupby("player").head(1).sort_values("career_runs", ascending=False)
