"""A month of New York yellow cabs, in plain SQL.

Plain-script version of the notebook. Set TAXI_DB to the path of taxi.db.
"""


import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DB_PATH = Path(os.environ.get("TAXI_DB", "taxi.db"))
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
    SELECT substr(pickup_time, 1, 10) AS day,
           COUNT(*)                   AS trips,
           ROUND(SUM(total_amount))   AS revenue
    FROM trips
    WHERE pickup_time >= '2023-01-01' AND pickup_time < '2023-02-01'
    GROUP BY day
)
SELECT day, trips, revenue,
       ROUND(AVG(trips) OVER (ORDER BY day ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)) AS moving_avg_7d,
       ROUND(100.0 * (trips - LAG(trips, 7) OVER (ORDER BY day))
                   / LAG(trips, 7) OVER (ORDER BY day), 1)                            AS vs_same_day_last_week_pct
FROM daily
ORDER BY day
""")

x = pd.to_datetime(daily.day)
weekend = x.dt.dayofweek >= 5
fig, ax = plt.subplots(figsize=(10, 4))
ax.bar(x, daily.trips / 1000, color=[BLUE if w else AMBER for w in weekend])
ax.plot(x, daily.moving_avg_7d / 1000, color="#4a5568", lw=2, label="7-day moving average")
ax.set_ylabel("Trips (thousand)")
ax.set_title("Yellow cab trips per day, January 2023 (blue = weekend)")
ax.set_ylim(0, daily.trips.max() / 1000 * 1.22)
ax.legend(frameon=False, loc="upper left")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
daily.sort_values("trips").iloc[[0, 1, -2, -1]]

hours = q("""
SELECT CAST(strftime('%w', pickup_time) AS INT) AS weekday,    -- 0 = Sunday
       CAST(strftime('%H', pickup_time) AS INT) AS hour,
       COUNT(*)                                 AS trips
FROM trips
WHERE pickup_time >= '2023-01-01' AND pickup_time < '2023-02-01'
GROUP BY weekday, hour
ORDER BY weekday, hour
""")

grid = hours.pivot(index="weekday", columns="hour", values="trips").fillna(0)
fig, ax = plt.subplots(figsize=(10, 3.6))
im = ax.imshow(grid.values / 1000, cmap="Oranges", aspect="auto")
ax.set_yticks(range(7), ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"])
ax.set_xticks(range(0, 24, 2), range(0, 24, 2))
ax.set_xlabel("Hour of day")
ax.set_title("Pickups by weekday and hour, January 2023")
ax.grid(False)
fig.colorbar(im, ax=ax, label="Thousand trips", pad=0.01)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

zones = q("""
SELECT RANK() OVER (ORDER BY COUNT(*) DESC)               AS rank,
       z.zone,
       z.borough,
       COUNT(*)                                           AS trips,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS share_pct,
       ROUND(AVG(t.fare_amount), 2)                       AS avg_fare
FROM trips t
JOIN zones z ON z.zone_id = t.pickup_zone_id
WHERE pickup_time >= '2023-01-01' AND pickup_time < '2023-02-01'
GROUP BY z.zone_id
ORDER BY trips DESC
LIMIT 10
""")

fig, ax = plt.subplots(figsize=(9, 4.4))
ax.barh(zones.zone[::-1], zones.trips[::-1] / 1000, color=AMBER)
for y, (v, f) in enumerate(zip(zones.trips[::-1] / 1000, zones.avg_fare[::-1])):
    ax.text(v + 1, y, f"{v:,.0f}k  (avg fare ${f:.2f})", va="center", fontsize=8)
ax.set_xlim(0, zones.trips.max() / 1000 * 1.3)
ax.set_title("Top 10 pickup zones")
ax.set_xlabel("Trips (thousand)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

routes = q("""
WITH valid AS (
    SELECT pickup_zone_id, dropoff_zone_id, fare_amount, trip_distance,
           (julianday(dropoff_time) - julianday(pickup_time)) * 1440 AS minutes
    FROM trips
    WHERE pickup_time >= '2023-01-01' AND pickup_time < '2023-02-01' AND trip_distance > 0 AND fare_amount > 0
      AND dropoff_time > pickup_time
)
SELECT zp.zone                                AS pickup,
       zd.zone                                AS dropoff,
       COUNT(*)                               AS trips,
       ROUND(AVG(v.trip_distance), 1)         AS avg_miles,
       ROUND(AVG(v.minutes), 1)               AS avg_minutes,
       ROUND(AVG(v.fare_amount), 2)           AS avg_fare
FROM valid v
JOIN zones zp ON zp.zone_id = v.pickup_zone_id
JOIN zones zd ON zd.zone_id = v.dropoff_zone_id
WHERE zp.borough <> 'Unknown' AND zd.borough <> 'Unknown'      -- zones 264 and 265 are not real places
GROUP BY v.pickup_zone_id, v.dropoff_zone_id
ORDER BY trips DESC
LIMIT 10
""")

routes

airports = q("""
WITH valid AS (
    SELECT zp.zone AS pickup, zd.zone AS dropoff, t.fare_amount, t.trip_distance, t.tip_amount, t.payment_type,
           (julianday(t.dropoff_time) - julianday(t.pickup_time)) * 1440 AS minutes
    FROM trips t
    JOIN zones zp ON zp.zone_id = t.pickup_zone_id
    JOIN zones zd ON zd.zone_id = t.dropoff_zone_id
    WHERE t.pickup_time >= '2023-01-01' AND t.pickup_time < '2023-02-01' AND t.trip_distance > 0 AND t.fare_amount > 0
      AND t.dropoff_time > t.pickup_time
)
SELECT CASE WHEN dropoff LIKE '%Airport' THEN 'To ' || dropoff ELSE 'From ' || pickup END AS direction,
       COUNT(*)                                       AS trips,
       ROUND(AVG(fare_amount), 1)                     AS avg_fare,
       ROUND(AVG(trip_distance), 1)                   AS avg_miles,
       ROUND(AVG(minutes))                            AS avg_minutes,
       ROUND(100.0 * AVG(CASE WHEN payment_type = 1 AND fare_amount >= 3 AND tip_amount / fare_amount < 1.5
                           THEN tip_amount / fare_amount END), 1) AS avg_tip_pct
FROM valid
WHERE dropoff LIKE '%Airport' OR pickup LIKE '%Airport'
GROUP BY direction
HAVING trips >= 500
ORDER BY trips DESC
""")

fig, ax = plt.subplots(figsize=(8.5, 3.8))
ax.barh(airports.direction[::-1], airports.avg_fare[::-1], color=BLUE)
for y, (v, n) in enumerate(zip(airports.avg_fare[::-1], airports.trips[::-1])):
    ax.text(v + 0.6, y, f"${v:.0f}  ({n:,} trips)", va="center", fontsize=8)
ax.set_xlim(0, airports.avg_fare.max() * 1.3)
ax.set_title("Average metered fare of airport trips")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
airports

tips = q("""
SELECT CAST(strftime('%H', pickup_time) AS INT)          AS hour,
       COUNT(*)                                          AS trips,
       ROUND(100.0 * AVG(tip_amount / fare_amount), 1)   AS avg_tip_pct,
       ROUND(100.0 * AVG(tip_amount = 0), 1)             AS no_tip_pct
FROM trips
WHERE pickup_time >= '2023-01-01' AND pickup_time < '2023-02-01' AND payment_type = 1 AND fare_amount >= 3
  AND tip_amount / fare_amount < 1.5                    -- drop obvious data-entry errors
GROUP BY hour
ORDER BY hour
""")

fig, ax = plt.subplots(figsize=(9.5, 4))
ax.plot(tips.hour, tips.avg_tip_pct, marker="o", color=AMBER, label="Average tip (% of fare)")
ax.set_xlabel("Hour of day")
ax.set_ylabel("Average tip (% of fare)")
ax.set_xticks(range(0, 24, 2))
ax2 = ax.twinx()
ax2.plot(tips.hour, tips.no_tip_pct, marker="s", ms=4, color=BLUE, label="Trips with no tip (%)")
ax2.set_ylabel("Trips with no tip (%)")
ax2.grid(False)
ax2.spines["right"].set_visible(True)
ax.set_title("Tipping on credit-card trips by pickup hour")
lines = ax.get_lines() + ax2.get_lines()
ax.legend(lines, [l.get_label() for l in lines], frameon=False, loc="lower left")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
tips

speed = q("""
WITH valid AS (
    SELECT CAST(strftime('%H', pickup_time) AS INT)                          AS hour,
           CASE WHEN strftime('%w', pickup_time) IN ('0', '6') THEN 'Weekend' ELSE 'Weekday' END AS day_type,
           trip_distance,
           (julianday(dropoff_time) - julianday(pickup_time)) * 24           AS hours
    FROM trips
    WHERE pickup_time >= '2023-01-01' AND pickup_time < '2023-02-01' AND trip_distance > 0.2
)
SELECT hour, day_type,
       COUNT(*)                           AS trips,
       ROUND(SUM(trip_distance) / SUM(hours), 1) AS avg_mph
FROM valid
WHERE hours BETWEEN 1.0 / 60 AND 3 AND trip_distance / hours < 80
GROUP BY hour, day_type
ORDER BY day_type, hour
""")

fig, ax = plt.subplots(figsize=(9.5, 4))
for day_type, color in [("Weekday", AMBER), ("Weekend", BLUE)]:
    g = speed[speed.day_type == day_type]
    ax.plot(g.hour, g.avg_mph, marker="o", ms=4, label=day_type, color=color)
ax.set_xticks(range(0, 24, 2))
ax.set_xlabel("Pickup hour")
ax.set_ylabel("Average speed (mph)")
ax.set_title("Average taxi speed by hour")
ax.legend(frameon=False)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
speed.sort_values("avg_mph").iloc[[0, -1]]

distance = q("""
WITH valid AS (
    SELECT trip_distance, fare_amount,
           CASE WHEN trip_distance < 1  THEN '1  under 1 mile'
                WHEN trip_distance < 2  THEN '2  1-2 miles'
                WHEN trip_distance < 5  THEN '3  2-5 miles'
                WHEN trip_distance < 10 THEN '4  5-10 miles'
                ELSE                         '5  over 10 miles' END AS bucket
    FROM trips
    WHERE pickup_time >= '2023-01-01' AND pickup_time < '2023-02-01' AND trip_distance > 0 AND fare_amount > 0
)
SELECT substr(bucket, 4)                                   AS distance,
       COUNT(*)                                            AS trips,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)  AS share_pct,
       ROUND(AVG(fare_amount), 2)                          AS avg_fare,
       ROUND(SUM(fare_amount) / SUM(trip_distance), 2)     AS fare_per_mile
FROM valid
GROUP BY bucket
ORDER BY bucket
""")

fig, ax = plt.subplots(figsize=(8.5, 3.9))
ax.bar(distance.distance, distance.share_pct, color=AMBER)
for x, v in enumerate(distance.share_pct):
    ax.text(x, v + 0.8, f"{v:.0f}%", ha="center", fontsize=9)
ax.set_ylabel("Share of trips (%)")
ax.set_ylim(0, distance.share_pct.max() * 1.18)
ax2 = ax.twinx()
ax2.plot(distance.distance, distance.fare_per_mile, color=BLUE, marker="o")
ax2.set_ylabel("Fare per mile ($)")
ax2.set_ylim(0, distance.fare_per_mile.max() * 1.2)
ax2.grid(False)
ax2.spines["right"].set_visible(True)
ax.set_title("Trips by distance (bars) and fare per mile (line)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
distance
