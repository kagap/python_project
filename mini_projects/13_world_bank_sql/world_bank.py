"""60 years of countries getting richer and healthier, in plain SQL.

Plain-script version of the notebook. Set WORLDBANK_DB to the path of worldbank.db.
"""


import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DB_PATH = Path(os.environ.get("WORLDBANK_DB", "worldbank.db"))
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

index = q("""
SELECT c.name AS country, i.year,
       ROUND(i.gdp_per_capita)                                       AS gdp_per_capita,
       ROUND(100.0 * i.gdp_per_capita
             / FIRST_VALUE(i.gdp_per_capita) OVER (PARTITION BY i.country_code ORDER BY i.year), 0) AS index_1995
FROM indicators i
JOIN countries c ON c.code = i.country_code
WHERE i.country_code IN ('POL', 'DEU', 'CZE', 'HUN', 'SVK', 'ROU')
  AND i.year >= 1995 AND i.gdp_per_capita IS NOT NULL
ORDER BY c.name, i.year
""")

fig, ax = plt.subplots(figsize=(9.5, 4.4))
for country, g in index.groupby("country"):
    ax.plot(g.year, g.index_1995, lw=3 if country == "Poland" else 1.5, label=country,
            color=AMBER if country == "Poland" else None)
ax.set_ylabel("GDP per capita, 1995 = 100")
ax.set_title("GDP per capita in current US$, indexed to 1995")
ax.legend(frameon=False, ncol=3, fontsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
index[index.year == 2023].sort_values("index_1995", ascending=False)

growth = q("""
WITH start AS (SELECT country_code, gdp_per_capita AS gdp FROM indicators WHERE year = 1995),
finish AS (SELECT country_code, gdp_per_capita AS gdp, population FROM indicators WHERE year = 2022)
SELECT RANK() OVER (ORDER BY f.gdp / s.gdp DESC)      AS rank,
       c.name                                         AS country,
       c.region,
       ROUND(s.gdp)                                   AS gdp_1995,
       ROUND(f.gdp)                                   AS gdp_2022,
       ROUND(f.gdp / s.gdp, 1)                        AS times_richer,
       ROUND(100 * (power(f.gdp / s.gdp, 1.0 / 27) - 1), 1) AS avg_yearly_growth_pct
FROM start s
JOIN finish    f USING (country_code)
JOIN countries c ON c.code = s.country_code
WHERE f.population >= 1000000 AND s.gdp > 0
ORDER BY times_richer DESC
LIMIT 12
""")

fig, ax = plt.subplots(figsize=(8.5, 4.6))
ax.barh(growth.country[::-1], growth.times_richer[::-1], color=AMBER)
for y, v in enumerate(growth.times_richer[::-1]):
    ax.text(v + 0.1, y, f"{v:.1f}x", va="center", fontsize=8)
ax.set_xlim(0, growth.times_richer.max() * 1.12)
ax.set_title("GDP per capita in 2022 compared with 1995 (current US$, countries with 1m+ people)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
growth

covid = q("""
WITH yearly AS (
    SELECT country_code, year, life_expectancy,
           LAG(life_expectancy, 2) OVER (PARTITION BY country_code ORDER BY year) AS two_years_before
    FROM indicators
    WHERE year BETWEEN 2018 AND 2021 AND life_expectancy IS NOT NULL
)
SELECT c.name AS country, c.region,
       ROUND(two_years_before, 1)                    AS le_2019,
       ROUND(life_expectancy, 1)                     AS le_2021,
       ROUND(life_expectancy - two_years_before, 1)  AS change_years
FROM yearly y
JOIN countries c ON c.code = y.country_code
WHERE y.year = 2021 AND two_years_before IS NOT NULL
ORDER BY change_years
LIMIT 12
""")

fig, ax = plt.subplots(figsize=(8.5, 4.6))
ax.barh(covid.country[::-1], covid.change_years[::-1], color=BLUE)
for y, v in enumerate(covid.change_years[::-1]):
    ax.text(v - 0.05, y, f"{v:+.1f}", va="center", ha="right", fontsize=8)
ax.set_xlim(covid.change_years.min() * 1.15, 0)
ax.set_title("Change in life expectancy between 2019 and 2021 (years)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
covid

preston = q("""
WITH data AS (
    SELECT year, ln(gdp_per_capita) AS x, life_expectancy AS y
    FROM indicators
    WHERE year IN (1970, 1980, 1990, 2000, 2010, 2020)
      AND gdp_per_capita > 0 AND life_expectancy IS NOT NULL
),
sums AS (
    SELECT year, COUNT(*) AS n, SUM(x) AS sx, SUM(y) AS sy, SUM(x * y) AS sxy,
           SUM(x * x) AS sxx, SUM(y * y) AS syy
    FROM data
    GROUP BY year
)
SELECT year,
       n AS countries,
       ROUND((n * sxy - sx * sy) / (sqrt(n * sxx - sx * sx) * sqrt(n * syy - sy * sy)), 3) AS correlation
FROM sums
ORDER BY year
""")

scatter = q("""
SELECT c.name AS country, c.region, i.gdp_per_capita, i.life_expectancy, i.population
FROM indicators i JOIN countries c ON c.code = i.country_code
WHERE i.year = 2019 AND i.gdp_per_capita > 0 AND i.life_expectancy IS NOT NULL
""")
fig, ax = plt.subplots(figsize=(9, 4.8))
ax.scatter(scatter.gdp_per_capita, scatter.life_expectancy, s=scatter.population / 4e6 + 6, alpha=0.55, color=AMBER,
           edgecolor="white", lw=0.5)
ax.set_xscale("log")
ax.set_xlabel("GDP per capita, current US$ (log scale)")
ax.set_ylabel("Life expectancy (years)")
ax.set_title("Richer countries live longer (2019, bubble size = population)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
preston

regions = q("""
SELECT c.region, i.year,
       ROUND(SUM(i.life_expectancy * i.population) / SUM(i.population), 1) AS life_expectancy,
       ROUND(SUM(i.under5_mortality * i.population) / SUM(i.population), 0) AS under5_mortality
FROM indicators i
JOIN countries c ON c.code = i.country_code
WHERE i.year IN (1960, 1970, 1980, 1990, 2000, 2010, 2019)
  AND i.life_expectancy IS NOT NULL AND i.population IS NOT NULL AND i.under5_mortality IS NOT NULL
GROUP BY c.region, i.year
ORDER BY c.region, i.year
""")

fig, ax = plt.subplots(figsize=(9.5, 4.4))
for region, g in regions.groupby("region"):
    ax.plot(g.year, g.life_expectancy, marker="o", ms=4, label=region.strip())
ax.set_ylabel("Life expectancy (years, population-weighted)")
ax.set_title("Life expectancy by region")
ax.legend(frameon=False, fontsize=7, loc="lower right")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
regions[regions.year.isin([1960, 2019])].pivot(index="region", columns="year", values=["life_expectancy", "under5_mortality"])

climbers = q("""
WITH r2000 AS (
    SELECT country_code, gdp_per_capita AS gdp,
           RANK() OVER (ORDER BY gdp_per_capita DESC) AS rnk
    FROM indicators
    WHERE year = 2000 AND gdp_per_capita IS NOT NULL AND population >= 1000000
),
r2022 AS (
    SELECT country_code, gdp_per_capita AS gdp,
           RANK() OVER (ORDER BY gdp_per_capita DESC) AS rnk
    FROM indicators
    WHERE year = 2022 AND gdp_per_capita IS NOT NULL AND population >= 1000000
)
SELECT c.name AS country,
       a.rnk  AS rank_2000,
       b.rnk  AS rank_2022,
       a.rnk - b.rnk AS places_gained,
       ROUND(a.gdp) AS gdp_2000,
       ROUND(b.gdp) AS gdp_2022
FROM r2000 a
JOIN r2022 b USING (country_code)
JOIN countries c ON c.code = a.country_code
ORDER BY places_gained DESC
""")

top_up = climbers.head(8)
top_down = climbers.tail(8)
both = pd.concat([top_up, top_down])
fig, ax = plt.subplots(figsize=(8.5, 5.2))
ax.barh(both.country[::-1], both.places_gained[::-1], color=[BLUE if v < 0 else AMBER for v in both.places_gained[::-1]])
ax.axvline(0, color="#4a5568", lw=0.8)
ax.set_title("Biggest movers in the GDP-per-capita ranking, 2000 to 2022 (places)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
climbers.head(10)

decoupling = q("""
WITH wide AS (
    SELECT country_code,
           MAX(CASE WHEN year = 2005 THEN gdp_per_capita END) AS gdp_2005,
           MAX(CASE WHEN year = 2019 THEN gdp_per_capita END) AS gdp_2019,
           MAX(CASE WHEN year = 2005 THEN co2_per_capita END) AS co2_2005,
           MAX(CASE WHEN year = 2019 THEN co2_per_capita END) AS co2_2019,
           MAX(CASE WHEN year = 2019 THEN population END)     AS pop_2019
    FROM indicators
    WHERE year IN (2005, 2019)
    GROUP BY country_code
)
SELECT c.name AS country,
       ROUND(100.0 * (gdp_2019 / gdp_2005 - 1)) AS gdp_change_pct,
       ROUND(100.0 * (co2_2019 / co2_2005 - 1)) AS co2_change_pct,
       ROUND(co2_2005, 1) AS co2_2005,
       ROUND(co2_2019, 1) AS co2_2019
FROM wide w
JOIN countries c ON c.code = w.country_code
WHERE gdp_2005 > 0 AND co2_2005 > 0 AND pop_2019 >= 5000000
  AND gdp_2019 / gdp_2005 > 1.2 AND co2_2019 < co2_2005
ORDER BY co2_change_pct
LIMIT 15
""")

fig, ax = plt.subplots(figsize=(8.5, 5))
ax.barh(decoupling.country[::-1], decoupling.co2_change_pct[::-1], color=BLUE)
for y, (c, g) in enumerate(zip(decoupling.co2_change_pct[::-1], decoupling.gdp_change_pct[::-1])):
    ax.text(c - 0.5, y, f"{c:.0f}%  (income {g:+.0f}%)", va="center", ha="right", fontsize=8)
ax.set_xlim(decoupling.co2_change_pct.min() * 1.7, 0)
ax.set_title("CO2 per person, 2005 to 2019, in countries whose income per person rose 20%+ (5m+ people)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
decoupling
