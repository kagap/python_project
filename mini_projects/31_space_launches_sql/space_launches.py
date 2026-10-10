"""Every rocket launch in history, in plain SQL.

Plain-script version of the notebook. Set SPACE_DB to the path of space_launches.db.
"""


import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DB_PATH = Path(os.environ.get("SPACE_DB", "space_launches.db"))
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

what = q("""
SELECT CASE WHEN orbital = 1 THEN 'Orbital launch' ELSE category END AS kind,
       COUNT(*)                                               AS launches,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)     AS share_pct,
       MIN(year)                                              AS first_year,
       MAX(year)                                              AS last_year
FROM launches
GROUP BY kind
ORDER BY launches DESC
LIMIT 10
""")

fig, ax = plt.subplots(figsize=(8.5, 4.2))
ax.barh(what.kind[::-1], what.launches[::-1], color=[AMBER if k == "Orbital launch" else GREY for k in what.kind[::-1]])
for y, (v, s) in enumerate(zip(what.launches[::-1], what.share_pct[::-1])):
    ax.text(v + 250, y, f"{v:,}  ({s}%)", va="center", fontsize=8)
ax.set_xlim(0, what.launches.max() * 1.2)
ax.set_title("Rocket launches by kind (catalogue categories)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
what.head(4)

yearly = q("""
WITH yearly AS (
    SELECT year,
           COUNT(*)                   AS launches,
           SUM(outcome = 'success')   AS successes,
           SUM(outcome = 'failure')   AS failures
    FROM launches
    WHERE orbital = 1
    GROUP BY year
)
SELECT year, launches, failures,
       ROUND(100.0 * successes / launches, 1)                   AS success_pct,
       SUM(launches) OVER (ORDER BY year)                       AS total_so_far,
       launches - LAG(launches) OVER (ORDER BY year)            AS change
FROM yearly
ORDER BY year
""")

fig, ax = plt.subplots(figsize=(10, 4))
ax.bar(yearly.year, yearly.launches, color=AMBER, label="Orbital launches")
ax.bar(yearly.year, yearly.failures, color=BLUE, label="of which failures")
ax.set_ylabel("Launches per year")
ax.set_title("Orbital launches per year, 1957 to 2026 (2026 is a partial year)")
ax.legend(frameon=False, fontsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
yearly.sort_values("launches", ascending=False).head(5)

states = q("""
SELECT (year / 10) * 10                                                                        AS decade,
       COUNT(*)                                                                                AS launches,
       SUM(state IN ('Soviet Union', 'Russia'))                                                AS soviet_russia,
       SUM(state = 'United States')                                                            AS usa,
       SUM(state = 'China')                                                                    AS china,
       SUM(state IN ('France', 'ESA', 'ELDO', 'United Kingdom', 'Italy', 'Germany'))           AS europe,
       SUM(state IN ('Japan', 'India'))                                                        AS japan_india,
       SUM(NOT (state IN ('Soviet Union', 'Russia', 'United States', 'China', 'France', 'ESA', 'ELDO',
                          'United Kingdom', 'Italy', 'Germany', 'Japan', 'India')) OR state IS NULL) AS other
FROM launches
WHERE orbital = 1
GROUP BY decade
ORDER BY decade
""")

cols = ["soviet_russia", "usa", "china", "europe", "japan_india", "other"]
labels = ["Soviet Union / Russia", "United States", "China", "Europe", "Japan and India", "Other"]
fig, ax = plt.subplots(figsize=(9.5, 4.2))
bottom = np.zeros(len(states))
for col, lab, color in zip(cols, labels, [BLUE, AMBER, "#c0392b", "#68a357", "#b794f4", GREY]):
    ax.bar(states.decade.astype(str) + "s", states[col], bottom=bottom, label=lab, color=color)
    bottom += states[col].values
ax.set_ylabel("Orbital launches in the decade")
ax.set_title("Orbital launches by decade and launching state (2020s so far)")
ax.legend(frameon=False, fontsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
states

families = q("""
WITH f AS (
    SELECT COALESCE(v.family, l.vehicle)  AS family,
           MIN(v.manufacturer)             AS manufacturer,
           COUNT(*)                        AS launches,
           SUM(l.outcome = 'success')      AS successes,
           MIN(l.year)                     AS first_year,
           MAX(l.year)                     AS last_year
    FROM launches l
    LEFT JOIN vehicles v USING (vehicle)
    WHERE l.orbital = 1
    GROUP BY COALESCE(v.family, l.vehicle)
)
SELECT RANK() OVER (ORDER BY launches DESC)  AS rank,
       family, manufacturer, launches, first_year, last_year,
       ROUND(100.0 * successes / launches, 1) AS success_pct
FROM f
ORDER BY launches DESC
LIMIT 15
""")

fig, ax = plt.subplots(figsize=(9, 5))
ax.barh(families.family[::-1], families.launches[::-1], color=BLUE)
for y, (v, s, a, b) in enumerate(zip(families.launches[::-1], families.success_pct[::-1], families.first_year[::-1], families.last_year[::-1])):
    ax.text(v + 15, y, f"{v:,}  ({s:.0f}% ok, {a}-{b})", va="center", fontsize=8)
ax.set_xlim(0, families.launches.max() * 1.35)
ax.set_title("Rocket families with the most orbital launches")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
families

reliability = q("""
WITH n AS (
    SELECT vehicle, outcome,
           ROW_NUMBER() OVER (PARTITION BY vehicle ORDER BY launch_date, launch_tag) AS flight_no,
           COUNT(*)     OVER (PARTITION BY vehicle)                                   AS total
    FROM launches
    WHERE orbital = 1
)
SELECT CASE WHEN flight_no <= 5  THEN '1) flights 1-5'
            WHEN flight_no <= 20 THEN '2) flights 6-20'
            WHEN flight_no <= 50 THEN '3) flights 21-50'
            ELSE                      '4) flights 51+' END         AS flight_numbers,
       COUNT(*)                                                    AS launches,
       COUNT(DISTINCT vehicle)                                     AS vehicles,
       SUM(outcome = 'failure')                                    AS failures,
       ROUND(100.0 * SUM(outcome = 'failure') / COUNT(*), 1)       AS failure_pct
FROM n
WHERE total >= 20
GROUP BY flight_numbers
ORDER BY flight_numbers
""")

fig, ax = plt.subplots(figsize=(8, 3.8))
ax.bar(reliability.flight_numbers.str[3:], reliability.failure_pct, color=AMBER)
for x, (v, n) in enumerate(zip(reliability.failure_pct, reliability.launches)):
    ax.text(x, v + 0.2, f"{v:.1f}%\n({n:,} launches)", ha="center", fontsize=8)
ax.set_ylim(0, reliability.failure_pct.max() * 1.3)
ax.set_ylabel("Failure rate (%)")
ax.set_title("Failure rate by flight number, vehicles with 20+ launches")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
reliability

sites = q("""
SELECT s.short_name                                                       AS site,
       GROUP_CONCAT(DISTINCT s.state_code)                                AS states,
       COUNT(*)                                                           AS launches,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)                 AS share_pct,
       MIN(l.year)                                                        AS first_year,
       MAX(l.year)                                                        AS last_year,
       ROUND(100.0 * SUM(l.outcome = 'success') / COUNT(*), 1)            AS success_pct
FROM launches l
JOIN sites s USING (site_code)
WHERE l.orbital = 1
GROUP BY s.short_name
ORDER BY launches DESC
LIMIT 12
""")

sites

cadence = q("""
WITH g AS (
    SELECT year, launch_date,
           julianday(launch_date) - julianday(LAG(launch_date) OVER (ORDER BY launch_date, launch_tag)) AS gap_days
    FROM launches
    WHERE orbital = 1
)
SELECT year,
       COUNT(*)                   AS launches,
       ROUND(AVG(gap_days), 2)    AS avg_gap_days,
       MAX(gap_days)              AS longest_gap_days,
       SUM(gap_days = 0)          AS same_day_as_previous
FROM g
GROUP BY year
ORDER BY year
""")

fig, ax = plt.subplots(figsize=(10, 4))
ax.plot(cadence.year, cadence.avg_gap_days, color=AMBER, marker="o", ms=3.5, lw=2.2, label="Average gap")
ax.plot(cadence.year, cadence.longest_gap_days, color=GREY, lw=1.6, label="Longest gap")
ax.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
ax.set_yscale("log")
ax.set_ylabel("Days since the previous orbital launch (log scale)")
ax.set_title("From one launch a month to more than one a day")
ax.legend(frameon=False, fontsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
cadence.tail(6)

spacex = q("""
WITH l AS (
    SELECT year, agency_code,
           CASE WHEN vehicle LIKE 'Starship%' THEN NULL ELSE payload_tonnes END AS tonnes
    FROM launches
    WHERE orbital = 1 AND year >= 2006
)
SELECT year,
       COUNT(*)                                                                         AS launches,
       SUM(agency_code = 'SPX')                                                         AS spacex_launches,
       ROUND(100.0 * SUM(agency_code = 'SPX') / COUNT(*), 1)                            AS spacex_launch_share_pct,
       ROUND(SUM(tonnes))                                                               AS world_tonnes,
       ROUND(100.0 * SUM(CASE WHEN agency_code = 'SPX' THEN tonnes END) / SUM(tonnes), 1) AS spacex_tonnes_share_pct
FROM l
GROUP BY year
ORDER BY year
""")

fig, ax = plt.subplots(figsize=(10, 4))
ax.plot(spacex.year, spacex.spacex_launch_share_pct, color=AMBER, marker="o", ms=4, lw=2.4, label="Share of launches")
ax.plot(spacex.year, spacex.spacex_tonnes_share_pct, color=BLUE, marker="s", ms=4, lw=2.4, label="Share of tonnes to orbit")
ax.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
ax.set_ylabel("SpaceX share of the world total (%)")
ax.set_title("SpaceX in the world's orbital launches")
ax.legend(frameon=False, fontsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
spacex.tail(6)

orbits = q("""
SELECT (year / 5) * 5                                                       AS from_year,
       COUNT(*)                                                             AS launches,
       SUM(substr(category, 5, 3) = 'LEO')                                  AS low_earth,
       SUM(substr(category, 5, 3) = 'SSO')                                  AS sun_synchronous,
       SUM(substr(category, 5, 3) IN ('GTO', 'GEO', 'STO', 'MTO'))          AS geostationary_transfer,
       SUM(substr(category, 5, 3) = 'ISS')                                  AS space_station,
       SUM(substr(category, 5, 3) IN ('MOL', 'HEO', 'EEO'))                 AS elliptical,
       SUM(substr(category, 5, 3) = 'MEO')                                  AS medium_earth
FROM launches
WHERE orbital = 1
GROUP BY from_year
ORDER BY from_year
""")

cols = ["low_earth", "sun_synchronous", "geostationary_transfer", "space_station", "elliptical", "medium_earth"]
share = orbits[cols].div(orbits.launches, axis=0) * 100
fig, ax = plt.subplots(figsize=(9.5, 4.2))
bottom = np.zeros(len(orbits))
for col, color in zip(cols, [AMBER, BLUE, "#68a357", "#b794f4", GREY, "#c0392b"]):
    ax.bar(orbits.from_year.astype(str), share[col], bottom=bottom, label=col.replace("_", " "), color=color)
    bottom += share[col].values
ax.set_ylabel("% of orbital launches")
ax.set_title("Where orbital launches were headed, by five-year period")
ax.legend(frameon=False, fontsize=7, ncol=3, loc="lower center", bbox_to_anchor=(0.5, -0.32))
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
orbits.tail(4)

streaks = q("""
WITH o AS (
    SELECT vehicle, launch_date, outcome,
           ROW_NUMBER() OVER (PARTITION BY vehicle ORDER BY launch_date, launch_tag)          AS rn,
           ROW_NUMBER() OVER (PARTITION BY vehicle, outcome ORDER BY launch_date, launch_tag) AS rn_outcome
    FROM launches
    WHERE orbital = 1
),
runs AS (
    SELECT vehicle, rn - rn_outcome AS grp, COUNT(*) AS run_length,
           MIN(launch_date) AS from_date, MAX(launch_date) AS to_date
    FROM o
    WHERE outcome = 'success'
    GROUP BY vehicle, grp
)
SELECT vehicle, run_length, from_date, to_date,
       CASE WHEN to_date = (SELECT MAX(launch_date) FROM launches x WHERE x.vehicle = runs.vehicle AND x.orbital = 1)
            THEN 'run reaches the last flight' ELSE 'ended by a non-success' END AS status
FROM runs
ORDER BY run_length DESC
LIMIT 10
""")

streaks
