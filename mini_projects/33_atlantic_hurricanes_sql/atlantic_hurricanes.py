"""175 years of Atlantic hurricanes, in plain SQL.

Plain-script version of the notebook. Set HURRICANE_DB to the path of hurricanes.db.
"""


import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DB_PATH = Path(os.environ.get("HURRICANE_DB", "hurricanes.db"))
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
WITH y AS (
    SELECT year,
           SUM(peak_wind_kt >= 34) AS named_storms,
           SUM(peak_wind_kt >= 64) AS hurricanes,
           SUM(peak_wind_kt >= 96) AS major_hurricanes
    FROM storms
    GROUP BY year
)
SELECT year, named_storms, hurricanes, major_hurricanes,
       ROUND(AVG(named_storms) OVER (ORDER BY year ROWS BETWEEN 9 PRECEDING AND CURRENT ROW), 1) AS named_storms_10yr_avg
FROM y
ORDER BY year
""")

fig, ax = plt.subplots(figsize=(10.5, 4))
ax.bar(seasons.year, seasons.named_storms, color=GREY, label="Named storms per season")
ax.bar(seasons.year, seasons.hurricanes, color=AMBER, label="Hurricanes")
ax.plot(seasons.year, seasons.named_storms_10yr_avg, color=BLUE, lw=2.4, label="10-season average of named storms")
ax.axvline(1966, color="#4a5568", ls="--", lw=1)
ax.text(1967, seasons.named_storms.max() * 0.98, "satellite era", fontsize=8, va="top")
ax.set_ylabel("Storms per season")
ax.legend(frameon=False, fontsize=8, loc="upper left")
ax.set_title("Atlantic named storms and hurricanes, 1851 to 2025")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
seasons.sort_values("named_storms", ascending=False).head(5)

era = q("""
SELECT CASE WHEN year < 1900 THEN '1) 1851-1899'
            WHEN year < 1966 THEN '2) 1900-1965 (ships and aircraft)'
            WHEN year < 1995 THEN '3) 1966-1994 (satellites)'
            ELSE                  '4) 1995-2025 (satellites, active era)' END      AS period,
       COUNT(DISTINCT year)                                                          AS seasons,
       ROUND(1.0 * SUM(peak_wind_kt >= 34) / COUNT(DISTINCT year), 1)                AS named_storms_per_season,
       ROUND(1.0 * SUM(peak_wind_kt >= 64) / COUNT(DISTINCT year), 1)                AS hurricanes_per_season,
       ROUND(1.0 * SUM(peak_wind_kt >= 96) / COUNT(DISTINCT year), 1)                AS major_per_season,
       ROUND(100.0 * AVG(julianday(last_obs) - julianday(first_obs) < 2), 1)         AS short_lived_pct
FROM storms
GROUP BY period
ORDER BY period
""")

fig, ax = plt.subplots(figsize=(9.5, 3.9))
x = np.arange(len(era))
ax.bar(x - 0.2, era.named_storms_per_season, width=0.4, color=GREY, label="Named storms per season")
ax.bar(x + 0.2, era.hurricanes_per_season, width=0.4, color=AMBER, label="Hurricanes per season")
for i, s in enumerate(era.short_lived_pct):
    ax.text(i, era.named_storms_per_season.max() * 1.05, f"{s:.0f}% last under 2 days", ha="center", fontsize=8)
ax.set_xticks(x)
ax.set_xticklabels(era.period.str[3:], fontsize=7.5)
ax.set_ylim(0, era.named_storms_per_season.max() * 1.2)
ax.legend(frameon=False, fontsize=8, loc="upper left")
ax.set_title("Storms per season by period of the record")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
era

categories = q("""
SELECT (year / 10) * 10                                                     AS decade,
       COUNT(*)                                                             AS storms,
       SUM(peak_wind_kt < 34)                                               AS depression,
       SUM(peak_wind_kt >= 34 AND peak_wind_kt < 64)                        AS tropical_storm,
       SUM(peak_wind_kt >= 64 AND peak_wind_kt < 96)                        AS cat_1_2,
       SUM(peak_wind_kt >= 96 AND peak_wind_kt < 137)                       AS cat_3_4,
       SUM(peak_wind_kt >= 137)                                             AS cat_5,
       ROUND(100.0 * SUM(peak_wind_kt >= 96) / NULLIF(SUM(peak_wind_kt >= 64), 0), 1) AS major_share_of_hurricanes_pct
FROM storms
GROUP BY decade
ORDER BY decade
""")

cols = ["depression", "tropical_storm", "cat_1_2", "cat_3_4", "cat_5"]
labels = ["Depression", "Tropical storm", "Category 1-2", "Category 3-4", "Category 5"]
fig, ax = plt.subplots(figsize=(10, 4.2))
bottom = np.zeros(len(categories))
for col, lab, color in zip(cols, labels, [GREY, "#68a357", AMBER, BLUE, "#c0392b"]):
    ax.bar(categories.decade.astype(str) + "s", categories[col], bottom=bottom, label=lab, color=color)
    bottom += categories[col].values
ax.set_ylabel("Storms in the decade")
ax.tick_params(axis="x", labelsize=7)
ax.legend(frameon=False, fontsize=8, ncol=5, loc="upper left")
ax.set_title("Atlantic storms by peak strength and decade")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
categories.tail(6)

months = q("""
SELECT CAST(substr(first_obs, 6, 2) AS INT)                          AS month,
       COUNT(*)                                                      AS storms,
       SUM(peak_wind_kt >= 64)                                       AS hurricanes,
       SUM(peak_wind_kt >= 96)                                       AS major_hurricanes,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)            AS share_pct,
       ROUND(AVG(peak_wind_kt))                                      AS avg_peak_wind_kt,
       RANK() OVER (ORDER BY COUNT(*) DESC)                          AS busiest_rank
FROM storms
GROUP BY month
ORDER BY month
""")

names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
fig, ax = plt.subplots(figsize=(9, 3.9))
ax.bar([names[m - 1] for m in months.month], months.storms, color=GREY, label="Storms")
ax.bar([names[m - 1] for m in months.month], months.hurricanes, color=AMBER, label="Hurricanes")
ax.bar([names[m - 1] for m in months.month], months.major_hurricanes, color="#c0392b", label="Major hurricanes")
ax.set_ylabel("Storms formed in the month, 1851 to 2025")
ax.legend(frameon=False, fontsize=8)
ax.set_title("The hurricane season by month of formation")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
months[["month", "storms", "share_pct", "busiest_rank"]]

ace = q("""
WITH a AS (
    SELECT s.year, t.storm_id, t.max_wind_kt
    FROM tracks t
    JOIN storms s USING (storm_id)
    WHERE substr(t.obs_time, 12, 5) IN ('00:00', '06:00', '12:00', '18:00') AND t.max_wind_kt >= 34 AND t.status IN ('TS', 'HU')
)
SELECT year,
       COUNT(DISTINCT storm_id)                          AS storms,
       ROUND(SUM(max_wind_kt * max_wind_kt) / 10000.0)   AS ace,
       RANK() OVER (ORDER BY SUM(max_wind_kt * max_wind_kt) DESC) AS rank
FROM a
GROUP BY year
ORDER BY year
""")

fig, ax = plt.subplots(figsize=(10.5, 4))
ax.bar(ace.year, ace.ace, color=[AMBER if r <= 10 else GREY for r in ace["rank"]])
top = ace.sort_values("rank").head(10)
for _, r in top.iterrows():
    ax.text(r.year, r.ace + 6, int(r.year), ha="center", fontsize=7, rotation=90)
ax.set_ylabel("Accumulated cyclone energy")
ax.set_ylim(0, ace.ace.max() * 1.18)
ax.set_title("Season energy (orange = ten highest)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
ace.sort_values("rank").head(10)

intense = q("""
SELECT RANK() OVER (ORDER BY peak_wind_kt DESC, lowest_pressure_mb)  AS rank_by_wind,
       RANK() OVER (ORDER BY lowest_pressure_mb)                     AS rank_by_pressure,
       COALESCE(name, 'unnamed') || ' ' || year                      AS storm,
       peak_wind_kt,
       ROUND(peak_wind_kt * 1.15078)                                 AS peak_wind_mph,
       lowest_pressure_mb,
       ROUND(julianday(last_obs) - julianday(first_obs), 1)          AS lifetime_days
FROM storms
WHERE lowest_pressure_mb IS NOT NULL
ORDER BY rank_by_wind
LIMIT 12
""")

intense

rapid = q("""
WITH gains AS (
    SELECT a.storm_id, MAX(a.max_wind_kt - b.max_wind_kt) AS max_gain_24h
    FROM tracks a
    JOIN tracks b ON b.storm_id = a.storm_id AND b.obs_time = strftime('%Y-%m-%d %H:%M', a.obs_time, '-24 hours')
    WHERE a.max_wind_kt IS NOT NULL AND b.max_wind_kt IS NOT NULL
    GROUP BY a.storm_id
)
SELECT (s.year / 10) * 10                                         AS decade,
       COUNT(*)                                                    AS hurricanes,
       SUM(g.max_gain_24h >= 30)                                   AS rapidly_intensifying,
       ROUND(100.0 * AVG(g.max_gain_24h >= 30), 1)                 AS rapid_pct,
       ROUND(AVG(g.max_gain_24h), 1)                               AS avg_max_gain_kt,
       MAX(g.max_gain_24h)                                         AS fastest_gain_kt
FROM storms s
JOIN gains g USING (storm_id)
WHERE s.peak_wind_kt >= 64
GROUP BY decade
ORDER BY decade
""")

fig, ax = plt.subplots(figsize=(9.5, 3.9))
ax.bar(rapid.decade.astype(str) + "s", rapid.rapid_pct, color=AMBER)
for x, (p, n) in enumerate(zip(rapid.rapid_pct, rapid.hurricanes)):
    ax.text(x, p + 1, f"{p:.0f}%\n(n={n})", ha="center", fontsize=7.5)
ax.set_ylim(0, rapid.rapid_pct.max() * 1.25)
ax.set_ylabel("% of hurricanes with a 24-hour gain of 30 knots or more")
ax.tick_params(axis="x", labelsize=7)
ax.set_title("Rapid intensification among Atlantic hurricanes")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
rapid.tail(6)

landfalls = q("""
SELECT (CAST(substr(obs_time, 1, 4) AS INT) / 10) * 10         AS decade,
       COUNT(*)                                                AS landfalls,
       SUM(max_wind_kt >= 64)                                  AS hurricane_landfalls,
       SUM(max_wind_kt >= 96)                                  AS major_landfalls,
       MAX(max_wind_kt)                                        AS strongest_kt
FROM tracks
WHERE record_id = 'L'
GROUP BY decade
ORDER BY decade
""")

fig, ax = plt.subplots(figsize=(10, 4))
ax.bar(landfalls.decade.astype(str) + "s", landfalls.landfalls, color=GREY, label="All landfalls")
ax.bar(landfalls.decade.astype(str) + "s", landfalls.hurricane_landfalls, color=AMBER, label="At hurricane strength")
ax.bar(landfalls.decade.astype(str) + "s", landfalls.major_landfalls, color="#c0392b", label="At major hurricane strength")
ax.tick_params(axis="x", labelsize=7)
ax.set_ylabel("Landfall observations in the decade")
ax.legend(frameon=False, fontsize=8, loc="upper left")
ax.set_title("Landfalls of Atlantic tropical cyclones by decade")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
landfalls.tail(6)

streaks = q("""
WITH y AS (
    SELECT year, MAX(peak_wind_kt >= 96) AS had_major
    FROM storms
    GROUP BY year
),
n AS (
    SELECT year, had_major,
           ROW_NUMBER() OVER (ORDER BY year)                         AS rn,
           ROW_NUMBER() OVER (PARTITION BY had_major ORDER BY year)  AS rn_kind
    FROM y
),
runs AS (
    SELECT had_major, rn - rn_kind AS grp, MIN(year) AS from_year, MAX(year) AS to_year, COUNT(*) AS seasons
    FROM n
    GROUP BY had_major, grp
)
SELECT CASE WHEN had_major = 1 THEN 'with a major hurricane' ELSE 'without one' END AS kind,
       from_year, to_year, seasons
FROM runs
ORDER BY seasons DESC
LIMIT 10
""")

streaks

genesis = q("""
WITH first_ts AS (
    SELECT storm_id, lat, lon,
           ROW_NUMBER() OVER (PARTITION BY storm_id ORDER BY obs_time) AS rn
    FROM tracks
    WHERE max_wind_kt >= 34
),
peak AS (
    SELECT storm_id, lat, lon,
           ROW_NUMBER() OVER (PARTITION BY storm_id ORDER BY max_wind_kt DESC, obs_time) AS rn
    FROM tracks
    WHERE max_wind_kt IS NOT NULL
)
SELECT (s.year / 10) * 10                  AS decade,
       COUNT(*)                            AS storms,
       ROUND(AVG(f.lat), 1)                AS avg_lat_named,
       ROUND(AVG(f.lon), 1)                AS avg_lon_named,
       ROUND(AVG(p.lat), 1)                AS avg_lat_at_peak,
       ROUND(AVG(p.lon), 1)                AS avg_lon_at_peak
FROM storms s
JOIN first_ts f ON f.storm_id = s.storm_id AND f.rn = 1
JOIN peak     p ON p.storm_id = s.storm_id AND p.rn = 1
WHERE s.year >= 1900
GROUP BY decade
ORDER BY decade
""")

fig, ax = plt.subplots(figsize=(10, 4))
ax.plot(genesis.decade, genesis.avg_lat_at_peak, color=AMBER, marker="o", lw=2.2, label="Average latitude where the storm peaked")
ax.plot(genesis.decade, genesis.avg_lat_named, color=BLUE, marker="s", lw=2.2, label="Average latitude where it became a named storm")
ax.set_ylabel("Degrees north")
ax.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
ax.legend(frameon=False, fontsize=8)
ax.set_title("Where Atlantic storms start and peak, by decade")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
genesis
