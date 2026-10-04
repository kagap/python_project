"""What New Yorkers complain about, in plain SQL.

Plain-script version of the notebook. Set NYC311_DB to the path of nyc311.db.
"""


import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DB_PATH = Path(os.environ.get("NYC311_DB", "nyc311.db"))
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

top = q("""
SELECT complaint_type,
       COUNT(*)                                           AS requests,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS share_pct
FROM requests
GROUP BY complaint_type
ORDER BY requests DESC
LIMIT 12
""")

fig, ax = plt.subplots(figsize=(9, 4.8))
ax.barh(top.complaint_type[::-1], top.requests[::-1], color=AMBER)
for y, (v, s) in enumerate(zip(top.requests[::-1], top.share_pct[::-1])):
    ax.text(v + 300, y, f"{v:,}  ({s}%)", va="center", fontsize=8)
ax.set_xlim(0, top.requests.max() * 1.22)
ax.set_title("Most common complaint types (24 sampled days of 2023)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

boroughs = q("""
WITH borough_type AS (
    SELECT borough, complaint_type, COUNT(*) AS n
    FROM requests
    WHERE borough <> 'Unspecified'
    GROUP BY borough, complaint_type
),
ranked AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY borough ORDER BY n DESC) AS rn,
           SUM(n)       OVER (PARTITION BY borough)                 AS total,
           SUM(n)       OVER ()                                     AS grand_total
    FROM borough_type
)
SELECT borough,
       total                                   AS requests,
       ROUND(100.0 * total / grand_total, 1)   AS share_of_city_pct,
       complaint_type                          AS most_common_complaint,
       ROUND(100.0 * n / total, 1)             AS its_share_pct
FROM ranked
WHERE rn = 1
ORDER BY requests DESC
""")

boroughs

response = q("""
WITH durations AS (
    SELECT agency,
           (julianday(closed_date) - julianday(created_date)) * 24 AS hours
    FROM requests
    WHERE closed_date IS NOT NULL AND closed_date >= created_date
),
ranked AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY agency ORDER BY hours) AS rn,
           COUNT(*)     OVER (PARTITION BY agency)                AS n
    FROM durations
)
SELECT agency,
       MAX(n)                                                              AS closed_requests,
       ROUND(MAX(CASE WHEN rn = CAST(n * 0.5 AS INT) + 1 THEN hours END), 1) AS median_hours,
       ROUND(MAX(CASE WHEN rn = CAST(n * 0.9 AS INT) + 1 THEN hours END), 1) AS p90_hours
FROM ranked
GROUP BY agency
HAVING closed_requests >= 1000
ORDER BY median_hours
""")

plot_df = response.sort_values("median_hours")
fig, ax = plt.subplots(figsize=(9, 4.6))
y = np.arange(len(plot_df))
ax.barh(y + 0.2, plot_df.median_hours, height=0.4, color=AMBER, label="Median")
ax.barh(y - 0.2, plot_df.p90_hours, height=0.4, color=BLUE, label="90th percentile")
ax.set_yticks(y, plot_df.agency)
ax.invert_yaxis()
ax.set_xscale("log")
ax.set_xlabel("Hours from request to closure (log scale)")
ax.set_title("Response time by agency")
ax.legend(frameon=False)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
response

slow = q("""
WITH durations AS (
    SELECT complaint_type,
           julianday(closed_date) - julianday(created_date) AS days
    FROM requests
    WHERE closed_date IS NOT NULL AND closed_date >= created_date
),
ranked AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY complaint_type ORDER BY days) AS rn,
           COUNT(*)     OVER (PARTITION BY complaint_type)               AS n
    FROM durations
)
SELECT complaint_type,
       MAX(n)                                                            AS closed_requests,
       ROUND(MAX(CASE WHEN rn = CAST(n * 0.5 AS INT) + 1 THEN days END), 1) AS median_days,
       ROUND(MAX(CASE WHEN rn = CAST(n * 0.9 AS INT) + 1 THEN days END), 1) AS p90_days
FROM ranked
GROUP BY complaint_type
HAVING closed_requests >= 400
ORDER BY median_days DESC
LIMIT 10
""")

slow

hourly = q("""
WITH top_types AS (
    SELECT complaint_type FROM requests GROUP BY complaint_type ORDER BY COUNT(*) DESC LIMIT 4
),
hourly AS (
    SELECT complaint_type,
           CAST(strftime('%H', created_date) AS INT) AS hour,
           COUNT(*)                                  AS n
    FROM requests
    WHERE complaint_type IN (SELECT complaint_type FROM top_types)
      AND strftime('%H:%M:%S', created_date) <> '00:00:00'
    GROUP BY complaint_type, hour
)
SELECT complaint_type, hour, n,
       ROUND(100.0 * n / SUM(n) OVER (PARTITION BY complaint_type), 2) AS share_of_day_pct
FROM hourly
ORDER BY complaint_type, hour
""")

wide = hourly.pivot(index="hour", columns="complaint_type", values="share_of_day_pct")
fig, ax = plt.subplots(figsize=(9.5, 4.2))
for col, color in zip(wide.columns, [AMBER, BLUE, "#38a169", "#c05621"]):
    ax.plot(wide.index, wide[col], marker="o", ms=3, label=col, color=color)
ax.set_xticks(range(0, 24, 2))
ax.set_xlabel("Hour of day")
ax.set_ylabel("% of the type's requests")
ax.set_title("When each complaint is reported")
ax.legend(frameon=False, fontsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

season = q("""
SELECT strftime('%m', created_date)                                          AS month,
       ROUND(SUM(complaint_type = 'HEAT/HOT WATER')        / 2.0)           AS heat_hot_water,
       ROUND(SUM(complaint_type = 'Noise - Residential')   / 2.0)           AS noise_residential,
       ROUND(SUM(complaint_type = 'Illegal Parking')       / 2.0)           AS illegal_parking,
       ROUND(SUM(complaint_type LIKE 'Water%'
              OR complaint_type = 'Plumbing')              / 2.0)           AS water_plumbing,
       ROUND(COUNT(*) / 2.0)                                                AS all_requests
FROM requests
GROUP BY month
ORDER BY month
""")

fig, ax = plt.subplots(figsize=(9.5, 4.2))
months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
for col, color, label in [("heat_hot_water", AMBER, "Heat / hot water"),
                          ("noise_residential", BLUE, "Noise - residential"),
                          ("illegal_parking", "#38a169", "Illegal parking")]:
    ax.plot(months, season[col], marker="o", ms=4, label=label, color=color)
ax.set_ylabel("Requests per sampled day")
ax.set_title("Complaints by month (average of the 1st and 15th)")
ax.legend(frameon=False)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
season

quotient = q("""
WITH borough_type AS (
    SELECT borough, complaint_type, COUNT(*) AS n
    FROM requests
    WHERE borough <> 'Unspecified'
    GROUP BY borough, complaint_type
),
shares AS (
    SELECT *,
           1.0 * n / SUM(n) OVER (PARTITION BY borough)         AS borough_share,
           1.0 * SUM(n) OVER (PARTITION BY complaint_type)
               / SUM(n) OVER ()                                 AS city_share
    FROM borough_type
),
ranked AS (
    SELECT *, borough_share / city_share AS quotient,
           ROW_NUMBER() OVER (PARTITION BY borough ORDER BY borough_share / city_share DESC) AS rn
    FROM shares
    WHERE n >= 150
)
SELECT borough, complaint_type, n AS requests,
       ROUND(100.0 * borough_share, 1) AS borough_share_pct,
       ROUND(100.0 * city_share, 1)    AS city_share_pct,
       ROUND(quotient, 1)              AS location_quotient
FROM ranked
WHERE rn <= 2
ORDER BY borough, location_quotient DESC
""")

quotient
