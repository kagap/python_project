"""A year of road collisions in Britain, in plain SQL.

Plain-script version of the notebook. Set ROADS_DB to the path of roads.db.
"""


import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DB_PATH = Path(os.environ.get("ROADS_DB", "roads.db"))
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

severity = q("""
SELECT s.severity_name                                   AS severity,
       COUNT(*)                                          AS collisions,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS share_pct,
       SUM(c.number_of_casualties)                       AS casualties,
       ROUND(AVG(c.number_of_vehicles), 2)               AS avg_vehicles,
       ROUND(AVG(c.number_of_casualties), 2)             AS avg_casualties
FROM collisions c
JOIN severities s ON s.severity = c.collision_severity
GROUP BY c.collision_severity
ORDER BY c.collision_severity
""")

severity

when = q("""
SELECT c.day_of_week,
       w.weekday_name                                 AS weekday,
       CAST(substr(c.time, 1, 2) AS INT)              AS hour,
       COUNT(*)                                       AS collisions
FROM collisions c
JOIN weekdays w USING (day_of_week)
GROUP BY c.day_of_week, hour
ORDER BY c.day_of_week, hour
""")

grid = when.pivot(index="day_of_week", columns="hour", values="collisions").fillna(0)
fig, ax = plt.subplots(figsize=(10, 3.6))
im = ax.imshow(grid.values, cmap="Oranges", aspect="auto")
ax.set_yticks(range(7), ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"])
ax.set_xticks(range(0, 24, 2), range(0, 24, 2))
ax.set_xlabel("Hour of day")
ax.set_title("Injury collisions by weekday and hour, 2023")
ax.grid(False)
fig.colorbar(im, ax=ax, label="Collisions", pad=0.01)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

hourly = q("""
SELECT CAST(substr(time, 1, 2) AS INT)                       AS hour,
       COUNT(*)                                              AS collisions,
       SUM(collision_severity <= 2)                          AS fatal_or_serious,
       ROUND(100.0 * AVG(collision_severity <= 2), 1)        AS fatal_or_serious_pct,
       ROUND(100.0 * AVG(collision_severity = 1), 2)         AS fatal_pct
FROM collisions
GROUP BY hour
ORDER BY hour
""")

fig, ax = plt.subplots(figsize=(9.5, 4))
ax.bar(hourly.hour, hourly.collisions / 1000, color=GREY)
ax.set_ylabel("Collisions (thousand)")
ax.set_xlabel("Hour of day")
ax.set_xticks(range(0, 24, 2))
ax2 = ax.twinx()
ax2.plot(hourly.hour, hourly.fatal_or_serious_pct, color=AMBER, marker="o", lw=2)
ax2.set_ylabel("Fatal or serious (% of collisions)")
ax2.set_ylim(0, hourly.fatal_or_serious_pct.max() * 1.2)
ax2.grid(False)
ax2.spines["right"].set_visible(True)
ax.set_title("Number of collisions (bars) and how many were fatal or serious (line)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
hourly.sort_values("fatal_or_serious_pct").iloc[[0, -1]]

speed = q("""
SELECT speed_limit,
       COUNT(*)                                       AS collisions,
       SUM(collision_severity = 1)                    AS fatal,
       ROUND(100.0 * AVG(collision_severity <= 2), 1) AS fatal_or_serious_pct,
       ROUND(100.0 * AVG(collision_severity = 1), 2)  AS fatal_pct,
       ROUND(AVG(number_of_casualties), 2)            AS avg_casualties
FROM collisions
GROUP BY speed_limit
ORDER BY speed_limit
""")

fig, ax = plt.subplots(figsize=(8.5, 4))
ax.bar(speed.speed_limit.astype(str) + " mph", speed.fatal_or_serious_pct, color=BLUE)
for x, (v, f) in enumerate(zip(speed.fatal_or_serious_pct, speed.fatal_pct)):
    ax.text(x, v + 0.4, f"{v:.0f}%\n({f:.1f}% fatal)", ha="center", fontsize=8)
ax.set_ylim(0, speed.fatal_or_serious_pct.max() * 1.25)
ax.set_ylabel("Fatal or serious (% of collisions)")
ax.set_title("Severity of collisions by speed limit")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
speed

light = q("""
SELECT l.light_name                                     AS light,
       a.area_name                                      AS area,
       COUNT(*)                                         AS collisions,
       ROUND(100.0 * AVG(c.collision_severity <= 2), 1) AS fatal_or_serious_pct
FROM collisions c
JOIN light l USING (light_conditions)
JOIN areas a USING (urban_or_rural_area)
WHERE a.area_name IN ('Urban', 'Rural')
GROUP BY l.light_name, a.area_name
HAVING collisions >= 300
ORDER BY a.area_name, fatal_or_serious_pct DESC
""")

wide = light.pivot(index="light", columns="area", values="fatal_or_serious_pct").loc[
    light.groupby("light").collisions.sum().sort_values(ascending=False).index]
fig, ax = plt.subplots(figsize=(9, 4.2))
y = np.arange(len(wide))
ax.barh(y + 0.2, wide["Rural"], height=0.4, color=AMBER, label="Rural")
ax.barh(y - 0.2, wide["Urban"], height=0.4, color=BLUE, label="Urban")
ax.set_yticks(y, wide.index)
ax.invert_yaxis()
ax.set_xlabel("Fatal or serious (% of collisions)")
ax.set_title("Severity by light conditions, urban and rural roads")
ax.legend(frameon=False)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
light

vehicles = q("""
SELECT COALESCE(vt.vehicle_group, 'Unclassified')           AS vehicle_group,
       COUNT(*)                                             AS vehicles_involved,
       ROUND(100.0 * AVG(c.collision_severity <= 2), 1)     AS fatal_or_serious_pct,
       ROUND(100.0 * AVG(c.collision_severity = 1), 2)      AS fatal_pct
FROM vehicles v
JOIN collisions c USING (collision_index)
LEFT JOIN vehicle_types vt ON vt.vehicle_type = v.vehicle_type
GROUP BY vehicle_group
HAVING vehicles_involved >= 300
ORDER BY fatal_or_serious_pct DESC
""")

fig, ax = plt.subplots(figsize=(8.5, 4.2))
ax.barh(vehicles.vehicle_group[::-1], vehicles.fatal_or_serious_pct[::-1], color=AMBER)
for y, (v, n) in enumerate(zip(vehicles.fatal_or_serious_pct[::-1], vehicles.vehicles_involved[::-1])):
    ax.text(v + 0.4, y, f"{v:.0f}%  ({n:,} vehicles)", va="center", fontsize=8)
ax.set_xlim(0, vehicles.fatal_or_serious_pct.max() * 1.3)
ax.set_title("Fatal or serious share of the collisions each vehicle type was in")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
vehicles

age = q("""
WITH banded AS (
    SELECT CASE WHEN k.age_of_casualty < 16 THEN '0-15'
                WHEN k.age_of_casualty < 25 THEN '16-24'
                WHEN k.age_of_casualty < 35 THEN '25-34'
                WHEN k.age_of_casualty < 45 THEN '35-44'
                WHEN k.age_of_casualty < 55 THEN '45-54'
                WHEN k.age_of_casualty < 65 THEN '55-64'
                WHEN k.age_of_casualty < 75 THEN '65-74'
                ELSE '75+' END                      AS age_band,
           k.casualty_severity, k.casualty_class
    FROM casualties k
    WHERE k.age_of_casualty >= 0
)
SELECT age_band,
       COUNT(*)                                             AS casualties,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)   AS share_pct,
       ROUND(100.0 * AVG(casualty_severity <= 2), 1)        AS fatal_or_serious_pct,
       ROUND(100.0 * AVG(casualty_class = 3), 1)            AS pedestrian_pct
FROM banded
GROUP BY age_band
ORDER BY age_band
""")

fig, axes = plt.subplots(1, 2, figsize=(10, 3.9))
axes[0].bar(age.age_band, age.casualties / 1000, color=GREY)
axes[0].set_title("Casualties by age band (thousand)")
axes[1].bar(age.age_band, age.fatal_or_serious_pct, color=AMBER, label="Fatal or serious")
axes[1].plot(age.age_band, age.pedestrian_pct, color=BLUE, marker="o", label="Pedestrians")
axes[1].set_title("Fatal or serious share and pedestrian share (%)")
axes[1].legend(frameon=False, fontsize=8)
for ax in axes:
    ax.tick_params(axis="x", rotation=45)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
age

involved = q("""
SELECT CASE WHEN number_of_vehicles >= 3 THEN '3 or more' ELSE CAST(number_of_vehicles AS TEXT) END AS vehicles,
       COUNT(*)                                          AS collisions,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS share_pct,
       ROUND(100.0 * AVG(collision_severity <= 2), 1)    AS fatal_or_serious_pct,
       ROUND(100.0 * AVG(light_conditions <> 1), 1)      AS in_darkness_pct,
       ROUND(AVG(number_of_casualties), 2)               AS avg_casualties
FROM collisions
GROUP BY vehicles
ORDER BY vehicles
""")

involved
