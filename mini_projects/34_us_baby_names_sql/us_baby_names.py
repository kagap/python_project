"""131 years of American baby names, in plain SQL.

Plain-script version of the notebook. Set BABY_NAMES_DB to the path of baby_names.db.
"""

import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DB_PATH = Path(os.environ.get("BABY_NAMES_DB", "baby_names.db"))
PINK, BLUE, GREY, AMBER = "#d6678f", "#2b6cb0", "#a0aec0", "#e8a33d"
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

concentration = q("""
WITH ranked AS (
    SELECT year, sex, births,
           RANK() OVER (PARTITION BY year, sex ORDER BY births DESC) AS rnk,
           SUM(births) OVER (PARTITION BY year, sex)                 AS total_births
    FROM names
)
SELECT year, sex,
       COUNT(*)                                                                         AS distinct_names,
       ROUND(100.0 * SUM(CASE WHEN rnk <= 10 THEN births END) / MAX(total_births), 1)   AS top10_pct,
       ROUND(1000.0 * COUNT(*) / MAX(total_births), 1)                                  AS names_per_1000_births
FROM ranked
GROUP BY year, sex
ORDER BY year, sex
""")

fig, ax = plt.subplots(figsize=(10.5, 4))
for sex, color, label in (("M", BLUE, "Boys"), ("F", PINK, "Girls")):
    d = concentration[concentration.sex == sex]
    ax.plot(d.year, d.top10_pct, color=color, lw=2.4, label=label)
ax.set_ylabel("Share of births held by the ten most popular names (%)")
ax.legend(frameon=False, fontsize=9)
ax.set_title("The ten most popular names, as a share of all babies of that sex")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

concentration[concentration.year.isin([1880, 1925, 1950, 1975, 2000, 2010])]

half = q("""
WITH r AS (
    SELECT year, sex,
           ROW_NUMBER() OVER (PARTITION BY year, sex ORDER BY births DESC, name) AS n,
           1.0 * SUM(births) OVER (PARTITION BY year, sex ORDER BY births DESC, name ROWS UNBOUNDED PRECEDING)
               / SUM(births) OVER (PARTITION BY year, sex)                       AS cum_share
    FROM names
)
SELECT year, sex, MIN(n) AS names_for_half
FROM r
WHERE cum_share >= 0.5
GROUP BY year, sex
ORDER BY year, sex
""")

fig, ax = plt.subplots(figsize=(10, 3.9))
for sex, color, label in (("M", BLUE, "Boys"), ("F", PINK, "Girls")):
    d = half[half.sex == sex]
    ax.plot(d.year, d.names_for_half, color=color, lw=2.4, label=label)
ax.set_ylabel("Names needed to cover half of the babies")
ax.legend(frameon=False, fontsize=9)
ax.set_title("Half of all children, by number of names")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

half[half.year.isin([1880, 1925, 1950, 1975, 2000, 2010])].pivot(index="year", columns="sex", values="names_for_half")

alltime = q("""
SELECT RANK() OVER (ORDER BY SUM(births) DESC)                                        AS rank,
       name, sex,
       SUM(births)                                                                    AS births,
       ROUND(100.0 * SUM(births) / SUM(SUM(births)) OVER (PARTITION BY sex), 2)       AS pct_of_sex,
       COUNT(*)                                                                       AS years_listed
FROM names
GROUP BY name, sex
ORDER BY rank
LIMIT 10
""")

alltime

streaks = q("""
WITH top AS (
    SELECT year, sex, name
    FROM (SELECT year, sex, name, RANK() OVER (PARTITION BY year, sex ORDER BY births DESC) AS rnk FROM names)
    WHERE rnk = 1
),
n AS (
    SELECT sex, name, year, year - ROW_NUMBER() OVER (PARTITION BY sex, name ORDER BY year) AS grp
    FROM top
)
SELECT sex, name, MIN(year) AS from_year, MAX(year) AS to_year, COUNT(*) AS years_at_no1
FROM n
GROUP BY sex, name, grp
ORDER BY years_at_no1 DESC
LIMIT 12
""")

top = streaks.head(8).iloc[::-1]
fig, ax = plt.subplots(figsize=(10, 4))
ax.barh([f"{n} ({s})" for n, s in zip(top.name, top.sex)], top.years_at_no1, left=top.from_year,
        color=[PINK if s == "F" else BLUE for s in top.sex])
for i, (to, yrs) in enumerate(zip(top.to_year, top.years_at_no1)):
    ax.text(to + 1, i, f"{yrs} years", va="center", fontsize=8)
ax.set_xlim(1875, 2025)
ax.set_title("The longest unbroken spells at number one")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

streaks

jumps = q("""
WITH s AS (
    SELECT name, sex, year, births,
           LAG(births) OVER (PARTITION BY name, sex ORDER BY year) AS prev,
           LAG(year)   OVER (PARTITION BY name, sex ORDER BY year) AS prev_year
    FROM names
)
SELECT name, sex, year, prev AS births_year_before, births, ROUND(1.0 * births / prev, 1) AS times_more, births - prev AS extra_births
FROM s
WHERE prev_year = year - 1 AND prev >= 200 AND births - prev >= 1500
ORDER BY times_more DESC
LIMIT 12
""")

jumps

spikes = q("""
SELECT name, year, births
FROM names
WHERE (name = 'Tammy' AND sex = 'F' AND year BETWEEN 1945 AND 1975)
   OR (name = 'Jaime' AND sex = 'F' AND year BETWEEN 1965 AND 1995)
   OR (name = 'Dawson' AND sex = 'M' AND year BETWEEN 1985 AND 2010)
   OR (name = 'Ashanti' AND sex = 'F' AND year BETWEEN 1990 AND 2010)
ORDER BY name, year
""")

fig, axes = plt.subplots(1, 4, figsize=(11, 3.2))
for ax, (name, jump) in zip(axes, (("Tammy", 1957), ("Jaime", 1976), ("Dawson", 1998), ("Ashanti", 2002))):
    d = spikes[spikes.name == name]
    ax.fill_between(d.year, d.births, color=AMBER, alpha=0.5)
    ax.plot(d.year, d.births, color=AMBER, lw=2)
    ax.axvline(jump, color="#4a5568", ls="--", lw=1)
    ax.set_title(f"{name} ({jump})", fontsize=10)
    ax.tick_params(labelsize=7)
fig.suptitle("Births per year around the jump", fontsize=11)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

spikes.groupby('name').births.agg(['min', 'max'])

flips = q("""
WITH big AS (
    SELECT name FROM names GROUP BY name HAVING SUM(births) >= 50000
),
d AS (
    SELECT name, (year / 10) * 10 AS decade,
           SUM(CASE WHEN sex = 'F' THEN births ELSE 0 END) AS girls,
           SUM(births)                                      AS births
    FROM names
    WHERE name IN (SELECT name FROM big)
    GROUP BY name, decade
    HAVING SUM(births) >= 500
),
s AS (
    SELECT name, decade, births, ROUND(100.0 * girls / births, 1) AS girls_pct,
           MIN(100.0 * girls / births) OVER (PARTITION BY name) AS lowest,
           MAX(100.0 * girls / births) OVER (PARTITION BY name) AS highest
    FROM d
)
SELECT name, decade, births, girls_pct
FROM s
WHERE lowest < 10 AND highest > 90
ORDER BY name, decade
""")

names = flips.name.unique()
fig, axes = plt.subplots(2, 5, figsize=(12, 4.6), sharey=True)
for ax, name in zip(axes.ravel(), names):
    d = flips[flips.name == name]
    ax.step(d.decade, d.girls_pct, where="mid", color=PINK, lw=2)
    ax.fill_between(d.decade, d.girls_pct, step="mid", color=PINK, alpha=0.2)
    ax.set_title(name, fontsize=10)
    ax.set_ylim(0, 100)
    ax.tick_params(labelsize=7)
for ax in axes.ravel()[len(names):]:
    ax.axis("off")
axes[0, 0].set_ylabel("% girls")
axes[1, 0].set_ylabel("% girls")
fig.suptitle("Names that went from boys to girls (or back), by decade", fontsize=11)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

flips.pivot(index='name', columns='decade', values='girls_pct')

letters = q("""
SELECT year,
       ROUND(100.0 * SUM(CASE WHEN sex = 'M' AND substr(name, -1) = 'n' THEN births END)
             / SUM(CASE WHEN sex = 'M' THEN births END), 1) AS boys_ending_n_pct,
       ROUND(100.0 * SUM(CASE WHEN sex = 'F' AND substr(name, -1) = 'a' THEN births END)
             / SUM(CASE WHEN sex = 'F' THEN births END), 1) AS girls_ending_a_pct,
       ROUND(100.0 * SUM(CASE WHEN sex = 'F' AND substr(name, 1, 1) = 'A' THEN births END)
             / SUM(CASE WHEN sex = 'F' THEN births END), 1) AS girls_starting_a_pct
FROM names
GROUP BY year
ORDER BY year
""")

fig, ax = plt.subplots(figsize=(10.5, 4))
ax.plot(letters.year, letters.boys_ending_n_pct, color=BLUE, lw=2.4, label="Boys: name ends in n")
ax.plot(letters.year, letters.girls_ending_a_pct, color=PINK, lw=2.4, label="Girls: name ends in a")
ax.plot(letters.year, letters.girls_starting_a_pct, color=AMBER, lw=2.4, label="Girls: name starts with A")
ax.set_ylabel("% of babies of that sex")
ax.legend(frameon=False, fontsize=8, loc="upper left")
ax.set_title("The sound of a name, 1880 to 2010")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

letters[letters.year.isin([1880, 1925, 1950, 1975, 2000, 2010])]

fads = q("""
WITH RECURSIVE years(y) AS (
    SELECT 1880 UNION ALL SELECT y + 1 FROM years WHERE y < 2010
),
big AS (
    SELECT name, sex, SUM(births) AS total FROM names GROUP BY name, sex HAVING SUM(births) >= 100000
),
filled AS (
    SELECT b.name, b.sex, y.y AS year, COALESCE(n.births, 0) AS births
    FROM big b
    CROSS JOIN years y
    LEFT JOIN names n ON n.name = b.name AND n.sex = b.sex AND n.year = y.y
),
smooth AS (
    SELECT name, sex, year, births,
           AVG(births) OVER (PARTITION BY name, sex ORDER BY year ROWS BETWEEN 2 PRECEDING AND 2 FOLLOWING) AS avg5
    FROM filled
),
peak AS (
    SELECT name, sex, year AS peak_year, avg5 AS peak_avg5,
           ROW_NUMBER() OVER (PARTITION BY name, sex ORDER BY avg5 DESC, year) AS rn
    FROM smooth
),
fall AS (
    SELECT p.name, p.sex, MIN(s.year) AS half_year
    FROM peak p
    JOIN smooth s ON s.name = p.name AND s.sex = p.sex AND s.year > p.peak_year AND s.avg5 * 2 < p.peak_avg5
    WHERE p.rn = 1
    GROUP BY p.name, p.sex
)
SELECT p.name, p.sex, p.peak_year, ROUND(p.peak_avg5) AS peak_births_per_year,
       f.half_year - p.peak_year AS years_to_halve
FROM peak p
LEFT JOIN fall f USING (name, sex)
WHERE p.rn = 1 AND p.peak_year <= 1990
ORDER BY years_to_halve IS NULL, years_to_halve, p.peak_avg5 DESC
""")

d = fads.dropna(subset=["years_to_halve"])
fig, ax = plt.subplots(figsize=(10, 4.4))
ax.scatter(d.peak_year, d.years_to_halve, s=d.peak_births_per_year / 600 + 6, c=[PINK if s == "F" else BLUE for s in d.sex], alpha=0.55, edgecolor="none")
for name in ("Brittany", "Linda", "Ashley", "Jennifer", "John", "Michael", "Mary", "Debbie"):
    r = d[d.name == name].iloc[0]
    ax.annotate(name, (r.peak_year, r.years_to_halve), xytext=(5, 4), textcoords="offset points", fontsize=8)
ax.set_xlabel("Peak year (centre of the best five-year stretch)")
ax.set_ylabel("Years until births fall below half of the peak")
ax.set_title("How long a name stays big: names of 100,000+ babies (dot size = births at the peak)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

fads.assign(peak_decade=fads.peak_year // 10 * 10).groupby('peak_decade').years_to_halve.agg(names='size', fell_by_half='count', median_years='median')
