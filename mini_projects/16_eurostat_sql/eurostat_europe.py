"""Jobs and prices across Europe, in plain SQL.

Plain-script version of the notebook. Set EUROSTAT_DB to the path of eurostat.db.
"""


import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DB_PATH = Path(os.environ.get("EUROSTAT_DB", "eurostat.db"))
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

latest = q("""
WITH series AS (
    SELECT country_code, month, rate_total, rate_youth,
           LAG(rate_total, 12) OVER (PARTITION BY country_code ORDER BY month)  AS year_ago,
           ROW_NUMBER()        OVER (PARTITION BY country_code ORDER BY month DESC) AS rn
    FROM unemployment
)
SELECT RANK() OVER (ORDER BY s.rate_total DESC)  AS rank,
       c.country,
       s.month,
       s.rate_total                              AS unemployment_pct,
       ROUND(s.rate_total - s.year_ago, 1)       AS change_vs_year_ago,
       s.rate_youth                              AS under25_pct
FROM series s
JOIN countries c USING (country_code)
WHERE s.rn = 1 AND c.is_aggregate = 0 AND s.month >= '2026-01'
ORDER BY s.rate_total DESC
""")

fig, ax = plt.subplots(figsize=(8.5, 7))
colors = [BLUE if v < 0 else AMBER for v in latest.change_vs_year_ago.fillna(0)]
ax.barh(latest.country[::-1], latest.unemployment_pct[::-1], color=colors[::-1])
for y, v in enumerate(latest.unemployment_pct[::-1]):
    ax.text(v + 0.1, y, f"{v:.1f}%", va="center", fontsize=7)
ax.set_title("Unemployment rate, latest month (amber = higher than a year earlier, blue = lower)")
ax.tick_params(axis="y", labelsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
latest.head(8)

youth = q("""
WITH series AS (
    SELECT country_code, month, rate_total, rate_youth,
           ROW_NUMBER() OVER (PARTITION BY country_code ORDER BY month DESC) AS rn
    FROM unemployment
    WHERE rate_total IS NOT NULL AND rate_youth IS NOT NULL
)
SELECT c.country, s.month,
       s.rate_total                          AS overall_pct,
       s.rate_youth                          AS under25_pct,
       ROUND(s.rate_youth / s.rate_total, 1) AS youth_to_overall_ratio
FROM series s
JOIN countries c USING (country_code)
WHERE s.rn = 1 AND c.is_aggregate = 0 AND s.month >= '2026-01'
ORDER BY under25_pct DESC
LIMIT 12
""")

youth

peaks = q("""
WITH ranked AS (
    SELECT country_code, month, rate_total,
           ROW_NUMBER() OVER (PARTITION BY country_code ORDER BY rate_total DESC, month DESC) AS peak_rn,
           ROW_NUMBER() OVER (PARTITION BY country_code ORDER BY month DESC)                  AS last_rn
    FROM unemployment
    WHERE rate_total IS NOT NULL
)
SELECT c.country,
       MAX(CASE WHEN peak_rn = 1 THEN rate_total END)              AS peak_pct,
       MAX(CASE WHEN peak_rn = 1 THEN month END)                   AS peak_month,
       MAX(CASE WHEN last_rn = 1 THEN rate_total END)              AS latest_pct,
       MAX(CASE WHEN last_rn = 1 THEN month END)                   AS latest_month,
       ROUND(MAX(CASE WHEN peak_rn = 1 THEN rate_total END)
           - MAX(CASE WHEN last_rn = 1 THEN rate_total END), 1)    AS fall_since_peak
FROM ranked r
JOIN countries c USING (country_code)
WHERE c.is_aggregate = 0
GROUP BY r.country_code
HAVING latest_month >= '2026-01'
ORDER BY peak_pct DESC
LIMIT 12
""")

fig, ax = plt.subplots(figsize=(9, 5))
y = np.arange(len(peaks))
ax.barh(y + 0.2, peaks.peak_pct, height=0.4, color=AMBER, label="Peak")
ax.barh(y - 0.2, peaks.latest_pct, height=0.4, color=BLUE, label="Latest")
ax.set_yticks(y, [f"{c} ({m[:4]})" for c, m in zip(peaks.country, peaks.peak_month)])
ax.invert_yaxis()
ax.set_xlabel("Unemployment rate (%)")
ax.set_title("Highest unemployment since 2005 (year of the peak) against today")
ax.legend(frameon=False)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
peaks

poland = q("""
SELECT month,
       MAX(CASE WHEN country_code = 'PL'        THEN rate_total END) AS poland,
       MAX(CASE WHEN country_code = 'EU27_2020' THEN rate_total END) AS eu27,
       ROUND(MAX(CASE WHEN country_code = 'PL'        THEN rate_total END)
           - MAX(CASE WHEN country_code = 'EU27_2020' THEN rate_total END), 1) AS gap
FROM unemployment
WHERE country_code IN ('PL', 'EU27_2020')
GROUP BY month
HAVING poland IS NOT NULL AND eu27 IS NOT NULL
ORDER BY month
""")

x = pd.to_datetime(poland.month)
fig, ax = plt.subplots(figsize=(9.5, 4))
ax.plot(x, poland.poland, color=AMBER, lw=2.2, label="Poland")
ax.plot(x, poland.eu27, color=BLUE, lw=2, label="EU-27")
ax.set_ylabel("Unemployment rate (%)")
ax.set_title("Unemployment: Poland and the EU average")
ax.legend(frameon=False)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
poland.iloc[[0, len(poland) // 2, -1]]

inflation_peak = q("""
WITH ranked AS (
    SELECT country_code, month, annual_rate,
           ROW_NUMBER() OVER (PARTITION BY country_code ORDER BY annual_rate DESC) AS rn
    FROM inflation
    WHERE coicop = 'CP00'
)
SELECT RANK() OVER (ORDER BY r.annual_rate DESC) AS rank,
       c.country,
       r.month        AS peak_month,
       r.annual_rate  AS peak_inflation_pct
FROM ranked r
JOIN countries c USING (country_code)
WHERE r.rn = 1 AND c.is_aggregate = 0
ORDER BY r.annual_rate DESC
LIMIT 12
""")

fig, ax = plt.subplots(figsize=(8.5, 4.6))
ax.barh(inflation_peak.country[::-1], inflation_peak.peak_inflation_pct[::-1], color=AMBER)
for y, (v, m) in enumerate(zip(inflation_peak.peak_inflation_pct[::-1], inflation_peak.peak_month[::-1])):
    ax.text(v + 0.3, y, f"{v:.1f}%  ({m})", va="center", fontsize=8)
ax.set_xlim(0, inflation_peak.peak_inflation_pct.max() * 1.2)
ax.set_title("Peak annual inflation since 2015 (all items)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
inflation_peak

categories = q("""
SELECT month,
       MAX(CASE WHEN coicop = 'CP00' THEN annual_rate END) AS all_items,
       MAX(CASE WHEN coicop = 'CP01' THEN annual_rate END) AS food,
       MAX(CASE WHEN coicop = 'CP04' THEN annual_rate END) AS housing_and_energy,
       MAX(CASE WHEN coicop = 'CP07' THEN annual_rate END) AS transport
FROM inflation
WHERE country_code = 'EU27_2020' AND month >= '2019-01'
GROUP BY month
ORDER BY month
""")

x = pd.to_datetime(categories.month)
fig, ax = plt.subplots(figsize=(10, 4.2))
ax.plot(x, categories.housing_and_energy, color=AMBER, lw=2, label="Housing, water, electricity, gas")
ax.plot(x, categories.food, color="#38a169", lw=2, label="Food")
ax.plot(x, categories.transport, color=BLUE, lw=2, label="Transport")
ax.plot(x, categories.all_items, color="#4a5568", lw=2.5, ls="--", label="All items")
ax.axhline(0, color=GREY, lw=0.8)
ax.set_ylabel("Annual inflation (%)")
ax.set_title("Inflation in the EU by category")
ax.legend(frameon=False, fontsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
categories.loc[[categories.housing_and_energy.idxmax(), categories.food.idxmax(), categories.transport.idxmax()]]

phillips = q("""
WITH u AS (
    SELECT country_code, substr(month, 1, 4) AS year, AVG(rate_total) AS unemployment
    FROM unemployment WHERE rate_total IS NOT NULL
    GROUP BY country_code, year
),
i AS (
    SELECT country_code, substr(month, 1, 4) AS year, AVG(annual_rate) AS inflation
    FROM inflation WHERE coicop = 'CP00'
    GROUP BY country_code, year
),
joined AS (
    SELECT u.year, u.unemployment AS x, i.inflation AS y
    FROM u JOIN i USING (country_code, year)
    JOIN countries c USING (country_code)
    WHERE c.is_aggregate = 0 AND u.year BETWEEN '2015' AND '2025'
),
sums AS (
    SELECT year, COUNT(*) AS n, SUM(x) AS sx, SUM(y) AS sy, SUM(x * y) AS sxy, SUM(x * x) AS sxx, SUM(y * y) AS syy
    FROM joined GROUP BY year
)
SELECT year, n AS countries,
       ROUND((n * sxy - sx * sy) / (sqrt(n * sxx - sx * sx) * sqrt(n * syy - sy * sy)), 2) AS correlation
FROM sums
ORDER BY year
""")

fig, ax = plt.subplots(figsize=(9, 3.8))
ax.bar(phillips.year, phillips.correlation, color=[BLUE if v < 0 else AMBER for v in phillips.correlation])
for x, v in enumerate(phillips.correlation):
    ax.text(x, v + (0.02 if v >= 0 else -0.06), f"{v:.2f}", ha="center", fontsize=8)
ax.axhline(0, color="#4a5568", lw=0.8)
ax.set_ylim(-1, 1)
ax.set_title("Correlation between unemployment and inflation across countries, by year")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
phillips

streaks = q("""
WITH changes AS (
    SELECT country_code, month, rate_total,
           rate_total - LAG(rate_total) OVER (PARTITION BY country_code ORDER BY month) AS delta
    FROM unemployment
    WHERE rate_total IS NOT NULL
),
flagged AS (
    SELECT *, delta <= 0 AS not_rising,
           ROW_NUMBER() OVER (PARTITION BY country_code ORDER BY month) AS month_no
    FROM changes
    WHERE delta IS NOT NULL
),
numbered AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY country_code, not_rising ORDER BY month) AS flag_no
    FROM flagged
)
SELECT c.country,
       COUNT(*)              AS months_in_a_row,
       MIN(n.month)          AS from_month,
       MAX(n.month)          AS to_month,
       ROUND(SUM(n.delta), 1) AS change_pp
FROM numbered n
JOIN countries c USING (country_code)
WHERE n.not_rising = 1 AND c.is_aggregate = 0
GROUP BY n.country_code, n.month_no - n.flag_no
ORDER BY months_in_a_row DESC, from_month
LIMIT 8
""")

streaks
