"""A year of crime in Chicago, in plain SQL.

Plain-script version of the notebook. Set CHICAGO_DB to the path of chicago.db.
"""


import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DB_PATH = Path(os.environ.get("CHICAGO_DB", "chicago.db"))
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

daily = q("""
WITH daily AS (
    SELECT substr(date, 1, 10) AS day, COUNT(*) AS incidents
    FROM crimes
    GROUP BY day
)
SELECT day, incidents,
       ROUND(AVG(incidents) OVER (ORDER BY day ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 1) AS moving_avg_7d
FROM daily
ORDER BY day
""")

x = pd.to_datetime(daily.day)
fig, ax = plt.subplots(figsize=(10, 4))
ax.plot(x, daily.incidents, color=GREY, lw=0.8, label="Daily incidents")
ax.plot(x, daily.moving_avg_7d, color=AMBER, lw=2.2, label="7-day moving average")
ax.set_ylim(0, daily.incidents.max() * 1.1)
ax.set_title("Reported incidents per day, 2023")
ax.legend(frameon=False, loc="lower right")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
daily.incidents.describe().round(0)

types = q("""
SELECT primary_type                                          AS crime_type,
       COUNT(*)                                              AS incidents,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)    AS share_pct,
       ROUND(100.0 * AVG(arrest), 1)                         AS arrest_pct,
       ROUND(100.0 * AVG(domestic), 1)                       AS domestic_pct
FROM crimes
GROUP BY primary_type
ORDER BY incidents DESC
LIMIT 12
""")

fig, axes = plt.subplots(1, 2, figsize=(10, 4.8), sharey=True)
y = range(len(types))
axes[0].barh(y, types.incidents, color=AMBER)
axes[0].set_yticks(list(y), types.crime_type)
axes[0].invert_yaxis()
axes[0].set_title("Incidents")
axes[1].barh(y, types.arrest_pct, color=BLUE)
axes[1].set_title("Share that ended in an arrest (%)")
for ax in axes:
    ax.grid(axis="y", visible=False)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
types

hours = q("""
SELECT CAST(strftime('%w', date) AS INT) AS weekday,    -- 0 = Sunday
       CAST(strftime('%H', date) AS INT) AS hour,
       COUNT(*)                          AS incidents
FROM crimes
GROUP BY weekday, hour
ORDER BY weekday, hour
""")

grid = hours.pivot(index="weekday", columns="hour", values="incidents").fillna(0)
fig, ax = plt.subplots(figsize=(10, 3.6))
im = ax.imshow(grid.values, cmap="Oranges", aspect="auto")
ax.set_yticks(range(7), ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"])
ax.set_xticks(range(0, 24, 2), range(0, 24, 2))
ax.set_xlabel("Hour of day")
ax.set_title("Reported incidents by weekday and hour")
ax.grid(False)
fig.colorbar(im, ax=ax, label="Incidents", pad=0.01)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

areas = q("""
WITH area_type AS (
    SELECT community_area, primary_type, COUNT(*) AS n
    FROM crimes
    WHERE community_area IS NOT NULL
    GROUP BY community_area, primary_type
),
ranked AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY community_area ORDER BY n DESC) AS rn,
           SUM(n)       OVER (PARTITION BY community_area)                 AS total
    FROM area_type
)
SELECT RANK() OVER (ORDER BY r.total DESC)  AS rank,
       a.community,
       r.total                              AS incidents,
       r.primary_type                       AS most_common_type,
       ROUND(100.0 * r.n / r.total, 1)      AS its_share_pct
FROM ranked r
JOIN community_areas a ON a.area_number = r.community_area
WHERE r.rn = 1
ORDER BY r.total DESC
LIMIT 10
""")

areas

theft = q("""
SELECT location_description                                      AS location,
       COUNT(*)                                                  AS thefts,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)        AS share_pct,
       ROUND(100.0 * SUM(COUNT(*)) OVER (ORDER BY COUNT(*) DESC)
                   / SUM(COUNT(*)) OVER (), 1)                   AS cumulative_pct
FROM crimes
WHERE primary_type = 'THEFT' AND location_description IS NOT NULL
GROUP BY location_description
ORDER BY thefts DESC
LIMIT 10
""")

fig, ax = plt.subplots(figsize=(9, 4.4))
ax.barh(theft.location[::-1], theft.share_pct[::-1], color=BLUE)
for y, v in enumerate(theft.share_pct[::-1]):
    ax.text(v + 0.2, y, f"{v}%", va="center", fontsize=8)
ax.set_xlim(0, theft.share_pct.max() * 1.15)
ax.set_title("Where thefts are reported (top 10 locations, % of all thefts)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
theft

spikes = q("""
WITH daily AS (
    SELECT substr(date, 1, 10) AS day, COUNT(*) AS incidents
    FROM crimes
    GROUP BY day
),
stats AS (
    SELECT day, incidents,
           AVG(incidents)              OVER () AS mean,
           AVG(incidents * incidents)  OVER () AS mean_sq
    FROM daily
),
scored AS (
    SELECT day, incidents, mean,
           (incidents - mean) / sqrt(mean_sq - mean * mean) AS z
    FROM stats
)
SELECT day,
       CASE strftime('%w', day) WHEN '0' THEN 'Sun' WHEN '1' THEN 'Mon' WHEN '2' THEN 'Tue'
                                WHEN '3' THEN 'Wed' WHEN '4' THEN 'Thu' WHEN '5' THEN 'Fri' ELSE 'Sat' END AS weekday,
       incidents,
       ROUND(mean)       AS typical_day,
       ROUND(z, 2)       AS z_score
FROM scored
WHERE ABS(z) >= 2.5
ORDER BY ABS(z) DESC
LIMIT 10
""")

spikes

first = q("""
WITH daily AS (
    SELECT substr(date, 1, 10) AS day, primary_type, COUNT(*) AS n
    FROM crimes
    WHERE substr(date, 1, 10) <> '2023-01-01'
    GROUP BY day, primary_type
),
split AS (
    SELECT primary_type,
           AVG(CASE WHEN substr(day, 9, 2) =  '01' THEN n END) AS on_the_1st,
           AVG(CASE WHEN substr(day, 9, 2) <> '01' THEN n END) AS other_days
    FROM daily
    GROUP BY primary_type
)
SELECT primary_type                         AS crime_type,
       ROUND(on_the_1st)                    AS avg_on_the_1st,
       ROUND(other_days)                    AS avg_other_days,
       ROUND(on_the_1st - other_days)       AS extra_per_day,
       ROUND(on_the_1st / other_days, 1)    AS ratio
FROM split
WHERE other_days >= 20
ORDER BY extra_per_day DESC
LIMIT 6
""")

first

streaks = q("""
WITH daily AS (
    SELECT substr(date, 1, 10) AS day, COUNT(*) AS incidents
    FROM crimes
    GROUP BY day
),
ordered AS (
    SELECT incidents,
           ROW_NUMBER() OVER (ORDER BY incidents) AS rn,
           COUNT(*)     OVER ()                   AS n
    FROM daily
),
median AS (SELECT incidents AS m FROM ordered WHERE rn = (n + 1) / 2),
flagged AS (
    SELECT day, incidents,
           incidents > (SELECT m FROM median)      AS above,
           ROW_NUMBER() OVER (ORDER BY day)        AS day_no
    FROM daily
),
numbered AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY above ORDER BY day) AS flag_no
    FROM flagged
)
SELECT MIN(day)              AS first_day,
       MAX(day)              AS last_day,
       COUNT(*)              AS days_in_a_row,
       ROUND(AVG(incidents)) AS avg_incidents
FROM numbered
WHERE above = 1
GROUP BY day_no - flag_no
ORDER BY days_in_a_row DESC, first_day
LIMIT 6
""")

streaks

season = q("""
WITH top_types AS (
    SELECT primary_type FROM crimes GROUP BY primary_type ORDER BY COUNT(*) DESC LIMIT 5
),
monthly AS (
    SELECT primary_type, CAST(strftime('%m', date) AS INT) AS month, COUNT(*) AS n
    FROM crimes
    WHERE primary_type IN (SELECT primary_type FROM top_types)
    GROUP BY primary_type, month
)
SELECT primary_type AS crime_type, month, n,
       ROUND(100.0 * n / SUM(n) OVER (PARTITION BY primary_type), 2) AS share_of_year_pct
FROM monthly
ORDER BY primary_type, month
""")

wide = season.pivot(index="month", columns="crime_type", values="share_of_year_pct")
fig, ax = plt.subplots(figsize=(9.5, 4.2))
for col, color in zip(wide.columns, [AMBER, BLUE, "#38a169", "#c05621", GREY]):
    ax.plot(wide.index, wide[col], marker="o", ms=4, label=col.title(), color=color)
ax.axhline(100 / 12, color="#4a5568", ls="--", lw=0.8)
ax.set_xticks(range(1, 13), ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
ax.set_ylabel("% of the type's year")
ax.set_title("Seasonality of the five most common crime types (dashed = an even split)")
ax.legend(frameon=False, ncol=3, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.1))
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
