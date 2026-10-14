"""Twenty thousand board games, in plain SQL.

Plain-script version of the notebook. Set BOARDGAMES_DB to the path of boardgames.db.
"""

import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DB_PATH = Path(os.environ.get("BOARDGAMES_DB", "boardgames.db"))
BLUE, AMBER, GREY, RED, GREEN = "#2b6cb0", "#e8a33d", "#a0aec0", "#c0392b", "#68a357"
plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
                     "grid.alpha": 0.25, "figure.dpi": 110})
pd.set_option("display.width", 120)
pd.set_option("display.max_columns", 20)

con = sqlite3.connect(DB_PATH)


def q(sql):
    """Run a query and return the result as a DataFrame."""
    return pd.read_sql_query(sql, con)

tables = q("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY type DESC, name")
tables["rows"] = [con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables.name]
tables

boom = q("""
WITH y AS (
    SELECT year, COUNT(*) AS games FROM games WHERE year BETWEEN 1980 AND 2021 GROUP BY year
)
SELECT year, games,
       ROUND(AVG(games) OVER (ORDER BY year ROWS BETWEEN 4 PRECEDING AND CURRENT ROW), 1)       AS games_5yr_avg,
       ROUND(100.0 * (games - LAG(games) OVER (ORDER BY year)) / LAG(games) OVER (ORDER BY year), 1) AS growth_pct
FROM y
ORDER BY year
""")

fig, ax = plt.subplots(figsize=(10.5, 4))
ax.bar(boom.year, boom.games, color=GREY, label="Games rated 30+ times, by publication year")
ax.plot(boom.year, boom.games_5yr_avg, color=BLUE, lw=2.4, label="Five-year average")
ax.set_ylabel("Games")
ax.legend(frameon=False, fontsize=8, loc="upper left")
ax.set_title("Games per publication year (the last years are thin: new games need 30 ratings to appear)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

boom[boom.year.isin([1980, 1990, 2000, 2010, 2017, 2019, 2021])]

creep = q("""
SELECT year,
       COUNT(*)                                       AS games,
       ROUND(AVG(avg_rating), 2)                      AS avg_rating,
       ROUND(AVG(bayes_rating), 2)                    AS avg_bayes_rating,
       CAST(ROUND(AVG(users_rated)) AS INT)             AS ratings_per_game,
       ROUND(100.0 * AVG(avg_rating >= 7.0), 1)       AS pct_rated_7_plus
FROM games
WHERE year BETWEEN 1990 AND 2021
GROUP BY year
ORDER BY year
""")

fig, ax = plt.subplots(figsize=(10.5, 4))
ax.plot(creep.year, creep.avg_rating, color=AMBER, lw=2.6, label="Average rating")
ax.plot(creep.year, creep.avg_bayes_rating, color=BLUE, lw=2.6, label="Bayesian average (BoardGameGeek's adjusted rating)")
ax.set_ylabel("Rating (1 to 10)")
ax.set_ylim(5, 8)
ax.legend(frameon=False, fontsize=8, loc="upper left")
ax.set_title("Games rated 30+ times: raw rating climbs, the adjusted rating does not")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

creep[creep.year.isin([1990, 2000, 2010, 2015, 2018, 2019, 2020, 2021])]

bayes = q("""
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
ORDER BY s.avg_rating
""")

bayes

movers = q("""
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
LIMIT 8
""")

movers.assign(year=movers.year.astype('Int64'))

split_demo = q("""
WITH RECURSIVE split(item, rest) AS (
    SELECT '', mechanics || '|' FROM games WHERE game_id = 30549
    UNION ALL
    SELECT substr(rest, 1, instr(rest, '|') - 1), substr(rest, instr(rest, '|') + 1)
    FROM split
    WHERE rest <> ''
)
SELECT item AS mechanic FROM split WHERE item <> ''
""")

split_demo

mechanics = q("""
SELECT m.mechanic,
       COUNT(*)                                       AS games,
       ROUND(AVG(g.avg_rating), 2)                    AS avg_rating,
       ROUND(100.0 * AVG(g.rank <= 1000), 1)          AS pct_in_top1000,
       CAST(ROUND(AVG(g.year)) AS INT)                  AS avg_year
FROM game_mechanics m
JOIN games g USING (game_id)
GROUP BY m.mechanic
HAVING COUNT(*) >= 500
ORDER BY avg_rating DESC
""")

d = mechanics.iloc[::-1]
fig, ax = plt.subplots(figsize=(10, 5.6))
ax.barh(d.mechanic, d.avg_rating, color=[AMBER if p >= 10 else GREY for p in d.pct_in_top1000])
for y, v in enumerate(d.avg_rating):
    ax.text(v + 0.02, y, f"{v:.2f}", va="center", fontsize=7.5)
ax.set_xlim(5, d.avg_rating.max() + 0.35)
ax.set_xlabel("Average rating of games with the mechanic (orange: 10%+ of them are in the top 1,000)")
ax.tick_params(axis="y", labelsize=8)
ax.set_title("Mechanics and ratings (mechanics used by 500+ games)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

mechanics.head(10)

trends = q("""
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
ORDER BY growth_x DESC
""")

d = pd.concat([trends.head(8), trends.tail(6)]).iloc[::-1]
fig, ax = plt.subplots(figsize=(10, 4.8))
ax.barh(d.mechanic, d.growth_x, color=[GREEN if g > 1 else RED for g in d.growth_x])
ax.set_xscale("log")
ax.axvline(1, color="#4a5568", lw=1)
for y, v in enumerate(d.growth_x):
    ax.text(v * (1.06 if v > 1 else 0.94), y, f"{v:.1f}x", va="center", ha="left" if v > 1 else "right", fontsize=8)
ax.set_xlabel("Share of new games with the mechanic, 2015-2021 versus 2000-2009 (log scale)")
ax.set_title("The mechanics that took over, and the ones that faded")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

trends.head(8)

solo_coop = q("""
SELECT year, COUNT(*) AS games,
       ROUND(100.0 * AVG(min_players = 1), 1)                          AS solo_capable_pct,
       ROUND(100.0 * AVG(mechanics LIKE '%Cooperative Game%'), 1)      AS cooperative_pct
FROM games
WHERE year BETWEEN 1995 AND 2021 AND mechanics IS NOT NULL
GROUP BY year
ORDER BY year
""")

fig, ax = plt.subplots(figsize=(10, 4))
ax.plot(solo_coop.year, solo_coop.solo_capable_pct, color=BLUE, lw=2.6, label="Can be played solo (minimum players = 1)")
ax.plot(solo_coop.year, solo_coop.cooperative_pct, color=AMBER, lw=2.6, label="Cooperative games")
ax.set_ylabel("% of games published that year")
ax.legend(frameon=False, fontsize=9, loc="upper left")
ax.set_title("Playing alone and playing together became mainstream")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

solo_coop[solo_coop.year.isin([1995, 2000, 2005, 2010, 2015, 2019, 2021])]

designers = q("""
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
LIMIT 10
""")

designers

tail = q("""
WITH n AS (
    SELECT users_rated, NTILE(100) OVER (ORDER BY users_rated DESC) AS pct FROM games
)
SELECT pct AS top_percent_of_games, COUNT(*) AS games, SUM(users_rated) AS ratings, MAX(users_rated) AS most_ratings,
       ROUND(100.0 * SUM(SUM(users_rated)) OVER (ORDER BY pct) / SUM(SUM(users_rated)) OVER (), 1) AS cumulative_pct_of_ratings
FROM n
GROUP BY pct
ORDER BY pct
""")

fig, ax = plt.subplots(figsize=(8.5, 4.4))
ax.plot(tail.top_percent_of_games, tail.cumulative_pct_of_ratings, color=BLUE, lw=2.6)
ax.plot([0, 100], [0, 100], color=GREY, ls="--", lw=1)
for p in (1, 10, 50):
    r = tail[tail.top_percent_of_games == p].iloc[0]
    ax.scatter([p], [r.cumulative_pct_of_ratings], color=AMBER, zorder=3)
    ax.annotate(f"top {p}% of games: {r.cumulative_pct_of_ratings:.0f}% of ratings", (p, r.cumulative_pct_of_ratings), xytext=(8, -14), textcoords="offset points", fontsize=8)
ax.set_xlabel("Share of games, most rated first (%)")
ax.set_ylabel("Share of all ratings (%)")
ax.set_title("A few games collect most of the ratings")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

tail[tail.top_percent_of_games.isin([1, 5, 10, 25, 50])]

best_years = q("""
SELECT year, COUNT(*) AS games_in_top500,
       ROUND(100.0 * SUM(COUNT(*)) OVER (ORDER BY year) / SUM(COUNT(*)) OVER (), 1) AS cumulative_pct
FROM games
WHERE rank <= 500 AND year IS NOT NULL
GROUP BY year
ORDER BY year
""")

d = best_years[best_years.year >= 1995]
fig, ax = plt.subplots(figsize=(10, 4))
ax.bar(d.year, d.games_in_top500, color=[AMBER if y >= 2011 else GREY for y in d.year])
ax.set_ylabel("Games in the all-time top 500")
ax.set_title("When the top 500 games were published (orange: 2011 and later)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

best_years[best_years.year >= 2008]
