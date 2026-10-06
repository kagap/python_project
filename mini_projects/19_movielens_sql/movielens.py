"""100,000 movie ratings, in plain SQL.

Plain-script version of the notebook. Set MOVIELENS_DB to the path of movielens.db.
"""


import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DB_PATH = Path(os.environ.get("MOVIELENS_DB", "movielens.db"))
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

distribution = q("""
SELECT rating,
       COUNT(*)                                            AS ratings,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)  AS share_pct
FROM ratings
GROUP BY rating
ORDER BY rating
""")

fig, ax = plt.subplots(figsize=(8.5, 3.9))
ax.bar(distribution.rating.astype(str), distribution.share_pct, color=AMBER)
for x, v in enumerate(distribution.share_pct):
    ax.text(x, v + 0.4, f"{v:.0f}%", ha="center", fontsize=9)
ax.set_xlabel("Stars")
ax.set_ylabel("% of ratings")
ax.set_title("Distribution of ratings")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
distribution

best = q("""
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
LIMIT 10
""")

fig, ax = plt.subplots(figsize=(9, 4.6))
ax.barh(best.title[::-1], best.weighted_rating[::-1], color=AMBER)
for y, (w, v) in enumerate(zip(best.weighted_rating[::-1], best.votes[::-1])):
    ax.text(w + 0.01, y, f"{w:.2f}  ({v} ratings)", va="center", fontsize=8)
ax.set_xlim(3.8, best.weighted_rating.max() + 0.35)
ax.set_title("Top 10 movies by weighted rating (50+ ratings)")
ax.tick_params(axis="y", labelsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
best

genres = q("""
SELECT g.genre,
       COUNT(DISTINCT g.movie_id)                          AS movies,
       COUNT(*)                                            AS ratings,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)  AS share_of_genre_ratings_pct,
       ROUND(AVG(r.rating), 2)                             AS avg_rating
FROM movie_genres g
JOIN ratings r USING (movie_id)
GROUP BY g.genre
ORDER BY ratings DESC
""")

fig, ax = plt.subplots(figsize=(9, 5.2))
sizes = genres.movies / genres.movies.max() * 900 + 40
ax.scatter(genres.ratings / 1000, genres.avg_rating, s=sizes, color=AMBER, alpha=0.6, edgecolor="white")
for _, r in genres.iterrows():
    ax.annotate(r.genre, (r.ratings / 1000, r.avg_rating), textcoords="offset points", xytext=(6, 4), fontsize=8)
ax.set_xlabel("Ratings (thousand)")
ax.set_ylabel("Average rating")
ax.set_title("Genres by popularity and rating (bubble = number of movies)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
genres.sort_values("avg_rating").iloc[[0, -1]]

yearly = q("""
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
ORDER BY year
""")

fig, ax = plt.subplots(figsize=(10, 4))
ax.bar(yearly.year, yearly.ratings / 1000, color=GREY)
ax.set_ylabel("Ratings (thousand)")
ax2 = ax.twinx()
ax2.plot(yearly.year, yearly.avg_rating, color=AMBER, marker="o", lw=2)
ax2.set_ylim(3, 4.2)
ax2.set_ylabel("Average rating")
ax2.grid(False)
ax2.spines["right"].set_visible(True)
ax.set_title("Ratings per year (bars) and their average (line)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
yearly.sort_values("ratings", ascending=False).head(4)

users = q("""
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
ORDER BY decile
""")

fig, ax = plt.subplots(figsize=(8.5, 4))
ax.bar(users.decile.astype(str), users.share_pct, color=AMBER)
ax.set_xlabel("User decile (1 = most active)")
ax.set_ylabel("Share of all ratings (%)")
ax2 = ax.twinx()
ax2.plot(users.decile.astype(str), users.cumulative_pct, color=BLUE, marker="o")
ax2.set_ylim(0, 105)
ax2.set_ylabel("Cumulative share (%)")
ax2.grid(False)
ax2.spines["right"].set_visible(True)
ax.set_title("Who produces the ratings?")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
users

decades = q("""
SELECT (m.release_year / 10) * 10          AS decade,
       COUNT(DISTINCT m.movie_id)          AS movies,
       COUNT(*)                            AS ratings,
       ROUND(AVG(r.rating), 2)             AS avg_rating
FROM movies m
JOIN ratings r USING (movie_id)
WHERE m.release_year >= 1920
GROUP BY decade
ORDER BY decade
""")

fig, ax = plt.subplots(figsize=(9, 4))
ax.bar(decades.decade.astype(str) + "s", decades.avg_rating, color=AMBER)
for x, (v, n) in enumerate(zip(decades.avg_rating, decades.ratings)):
    ax.text(x, v + 0.03, f"{v:.2f}\n({n:,})", ha="center", fontsize=7)
ax.set_ylim(3, 4.1)
ax.set_ylabel("Average rating")
ax.set_title("Average rating by release decade (labels = number of ratings)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
decades

divisive = q("""
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
LIMIT 10
""")

divisive
