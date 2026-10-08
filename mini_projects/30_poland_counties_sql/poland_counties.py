"""Twenty years of Polish counties, in plain SQL.

Plain-script version of the notebook. Set COUNTIES_DB to the path of poland_counties.db.
"""


import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DB_PATH = Path(os.environ.get("COUNTIES_DB", "poland_counties.db"))
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

national = q("""
SELECT year,
       COUNT(*)                                                           AS counties,
       ROUND(SUM(unemployment * population) / SUM(population), 1)         AS unemployment_pct,
       ROUND(SUM(wage * population) / SUM(population))                    AS avg_wage_pln,
       ROUND(MIN(unemployment), 1)                                        AS lowest_county,
       ROUND(MAX(unemployment), 1)                                        AS highest_county,
       ROUND(SUM(unemployment * population) / SUM(population)
             - LAG(SUM(unemployment * population) / SUM(population)) OVER (ORDER BY year), 1) AS change_pp
FROM panel
WHERE unemployment IS NOT NULL AND population IS NOT NULL AND wage IS NOT NULL
GROUP BY year
ORDER BY year
""")

fig, ax = plt.subplots(figsize=(10, 4))
ax.fill_between(national.year, national.lowest_county, national.highest_county, color=GREY, alpha=0.35, label="Range across counties")
ax.plot(national.year, national.unemployment_pct, color=AMBER, marker="o", ms=4, lw=2.4, label="Population-weighted average")
ax.set_ylabel("Registered unemployment rate (%)")
ax.set_title("Unemployment fell sharply, and the range across counties narrowed")
ax.legend(frameon=False, fontsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
national[["year", "unemployment_pct", "avg_wage_pln"]].iloc[[0, 4, 9, 14, 19, 20]]

breaks = q("""
WITH yearly AS (
    SELECT p.name, p.voivodship, f.year, f.population,
           LAG(f.population) OVER (PARTITION BY f.powiat_id ORDER BY f.year) AS previous
    FROM panel f
    JOIN powiats p USING (powiat_id)
)
SELECT name, voivodship, year,
       ROUND(previous, 1)                           AS thousand_before,
       ROUND(population, 1)                         AS thousand_after,
       ROUND(100.0 * (population / previous - 1), 1) AS change_pct
FROM yearly
WHERE ABS(population / previous - 1) > 0.08
ORDER BY year, name
""")

breaks

spread = q("""
WITH ranked AS (
    SELECT year, unemployment,
           ROW_NUMBER() OVER (PARTITION BY year ORDER BY unemployment) AS rn,
           COUNT(*)     OVER (PARTITION BY year)                       AS n
    FROM panel
    WHERE unemployment IS NOT NULL
),
pct AS (
    SELECT year,
           MIN(CASE WHEN rn >= 0.10 * n THEN unemployment END) AS p10,
           MIN(CASE WHEN rn >= 0.50 * n THEN unemployment END) AS median,
           MIN(CASE WHEN rn >= 0.90 * n THEN unemployment END) AS p90
    FROM ranked
    GROUP BY year
)
SELECT year, p10, median, p90,
       ROUND(p90 - p10, 1)  AS gap_pp,
       ROUND(p90 / p10, 1)  AS ratio
FROM pct
ORDER BY year
""")

fig, ax = plt.subplots(figsize=(10, 4))
ax.fill_between(spread.year, spread.p10, spread.p90, color=GREY, alpha=0.35, label="10th to 90th percentile")
ax.plot(spread.year, spread["median"], color=BLUE, marker="o", ms=4, lw=2.2, label="Median county")
ax.set_ylabel("Registered unemployment rate (%)")
ax.set_title("The typical county and the spread around it")
ax.legend(frameon=False, fontsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
spread.iloc[[0, 5, 10, 14, 19, 20]]

wages_2024 = q("""
WITH w AS (
    SELECT p.name, p.voivodship, p.is_city, ROUND(f.wage) AS wage_pln,
           RANK() OVER (ORDER BY f.wage DESC)          AS rank_high,
           RANK() OVER (ORDER BY f.wage)               AS rank_low,
           ROUND(100.0 * f.wage / AVG(f.wage) OVER ()) AS index_vs_average
    FROM panel f
    JOIN powiats p USING (powiat_id)
    WHERE f.year = 2024
)
SELECT CASE WHEN rank_high <= 10 THEN rank_high ELSE -rank_low END AS position,
       name, voivodship, is_city, wage_pln, index_vs_average
FROM w
WHERE rank_high <= 10 OR rank_low <= 10
ORDER BY wage_pln DESC
""")

fig, ax = plt.subplots(figsize=(9, 6))
colors = [AMBER if c else BLUE for c in wages_2024.is_city]
ax.barh(wages_2024.name[::-1], wages_2024.wage_pln[::-1], color=colors[::-1])
for y, v in enumerate(wages_2024.wage_pln[::-1]):
    ax.text(v + 120, y, f"{v:,.0f}", va="center", fontsize=8)
ax.set_xlim(0, wages_2024.wage_pln.max() * 1.12)
ax.set_title("Average gross monthly wage in 2024 (PLN): ten highest and ten lowest counties\n(orange = city with powiat status)", fontsize=10)
ax.tick_params(axis="y", labelsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
wages_2024.head(3)

voivodships = q("""
SELECT p.voivodship,
       COUNT(DISTINCT p.powiat_id)                                             AS counties,
       ROUND(SUM(CASE WHEN f.year = 2024 THEN f.population END) / 1000, 2)    AS population_millions,
       ROUND(SUM(CASE WHEN f.year = 2024 THEN f.wage * f.population END)
             / SUM(CASE WHEN f.year = 2024 THEN f.population END))            AS wage_2024,
       ROUND(100.0 * ((SUM(CASE WHEN f.year = 2024 THEN f.wage * f.population END)
                       / SUM(CASE WHEN f.year = 2024 THEN f.population END))
                    / (SUM(CASE WHEN f.year = 2010 THEN f.wage * f.population END)
                       / SUM(CASE WHEN f.year = 2010 THEN f.population END)) - 1)) AS wage_growth_pct,
       ROUND(SUM(CASE WHEN f.year = 2010 THEN f.unemployment * f.population END)
             / SUM(CASE WHEN f.year = 2010 THEN f.population END), 1)         AS unemployment_2010,
       ROUND(SUM(CASE WHEN f.year = 2024 THEN f.unemployment * f.population END)
             / SUM(CASE WHEN f.year = 2024 THEN f.population END), 1)         AS unemployment_2024,
       ROUND(100.0 * (SUM(CASE WHEN f.year = 2024 THEN f.population END)
                      / SUM(CASE WHEN f.year = 2010 THEN f.population END) - 1), 1) AS population_change_pct
FROM panel f
JOIN powiats p USING (powiat_id)
WHERE f.year IN (2010, 2024)
GROUP BY p.voivodship
ORDER BY wage_2024 DESC
""")

fig, ax = plt.subplots(figsize=(9, 5.2))
ax.scatter(voivodships.wage_2024, voivodships.unemployment_2024, s=voivodships.population_millions * 90, color=AMBER, alpha=0.8)
for _, r in voivodships.iterrows():
    ax.annotate(r.voivodship, (r.wage_2024, r.unemployment_2024), textcoords="offset points", xytext=(6, 5), fontsize=7.5)
ax.set_xlabel("Average gross monthly wage in 2024 (PLN)")
ax.set_ylabel("Registered unemployment in 2024 (%)")
ax.set_title("Voivodships in 2024 (bubble size = population)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
voivodships[["voivodship", "wage_growth_pct", "population_change_pct"]]

climbers = q("""
WITH r AS (
    SELECT powiat_id, year, wage,
           RANK() OVER (PARTITION BY year ORDER BY wage DESC) AS pay_rank
    FROM panel
    WHERE year IN (2010, 2024) AND wage IS NOT NULL
      AND powiat_id NOT IN (SELECT powiat_id FROM series_breaks)
),
moves AS (
    SELECT p.name, p.voivodship, p.is_city,
           a.pay_rank AS rank_2010, b.pay_rank AS rank_2024,
           a.pay_rank - b.pay_rank AS places_gained,
           ROUND(a.wage) AS wage_2010, ROUND(b.wage) AS wage_2024,
           ROUND(100.0 * (b.wage / a.wage - 1)) AS growth_pct
    FROM r a
    JOIN r b ON a.powiat_id = b.powiat_id AND a.year = 2010 AND b.year = 2024
    JOIN powiats p ON p.powiat_id = a.powiat_id
),
numbered AS (
    SELECT *, ROW_NUMBER() OVER (ORDER BY places_gained DESC) AS best,
              ROW_NUMBER() OVER (ORDER BY places_gained)      AS worst
    FROM moves
)
SELECT name, voivodship, rank_2010, rank_2024, places_gained, wage_2010, wage_2024, growth_pct
FROM numbered
WHERE best <= 8 OR worst <= 8
ORDER BY places_gained DESC
""")

climbers

convergence = q("""
WITH starts(y0) AS (VALUES (2005), (2010), (2015), (2019)),
pairs AS (
    SELECT s.y0, LN(a.wage) AS x, LN(b.wage / a.wage) AS y
    FROM starts s
    JOIN panel a ON a.year = s.y0
    JOIN panel b ON b.powiat_id = a.powiat_id AND b.year = 2024
    WHERE a.wage > 0 AND b.wage > 0 AND a.powiat_id NOT IN (SELECT powiat_id FROM series_breaks)
),
sums AS (
    SELECT y0, COUNT(*) AS n, SUM(x) AS sx, SUM(y) AS sy, SUM(x * y) AS sxy, SUM(x * x) AS sxx, SUM(y * y) AS syy
    FROM pairs GROUP BY y0
)
SELECT y0                                                                              AS start_year,
       n                                                                               AS counties,
       ROUND((n * sxy - sx * sy) / (n * sxx - sx * sx), 3)                             AS slope,
       ROUND((n * sxy - sx * sy) / SQRT((n * sxx - sx * sx) * (n * syy - sy * sy)), 2) AS correlation
FROM sums
ORDER BY y0
""")

pairs = q("""
SELECT p.name, a.wage AS wage_2010, 100 * (b.wage / a.wage - 1) AS growth_pct
FROM panel a JOIN panel b ON a.powiat_id = b.powiat_id AND a.year = 2010 AND b.year = 2024
JOIN powiats p ON p.powiat_id = a.powiat_id
WHERE a.wage > 0 AND b.wage > 0 AND a.powiat_id NOT IN (SELECT powiat_id FROM series_breaks)
""")
fig, ax = plt.subplots(figsize=(8.5, 4.8))
ax.scatter(pairs.wage_2010, pairs.growth_pct, s=14, color=BLUE, alpha=0.6)
b1, b0 = np.polyfit(np.log(pairs.wage_2010), np.log1p(pairs.growth_pct / 100), 1)
xs = np.linspace(pairs.wage_2010.min(), pairs.wage_2010.max(), 100)
ax.plot(xs, 100 * (np.exp(b0 + b1 * np.log(xs)) - 1), color=AMBER, lw=2.4)
ax.set_xlabel("Average gross monthly wage in 2010 (PLN)")
ax.set_ylabel("Wage growth 2010 to 2024 (%)")
ax.set_title("Starting wage and later growth, counties without border changes")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
convergence

jobs_pay = q("""
WITH s AS (
    SELECT year, COUNT(*) AS n,
           SUM(unemployment) AS sx, SUM(LN(wage)) AS sy, SUM(unemployment * LN(wage)) AS sxy,
           SUM(unemployment * unemployment) AS sxx, SUM(LN(wage) * LN(wage)) AS syy
    FROM panel
    WHERE unemployment IS NOT NULL AND wage > 0
    GROUP BY year
),
u AS (
    SELECT year, COUNT(*) AS n,
           SUM(urbanization) AS sx, SUM(unemployment) AS sy, SUM(urbanization * unemployment) AS sxy,
           SUM(urbanization * urbanization) AS sxx, SUM(unemployment * unemployment) AS syy
    FROM panel
    WHERE unemployment IS NOT NULL AND urbanization IS NOT NULL
    GROUP BY year
)
SELECT s.year,
       ROUND((s.n * s.sxy - s.sx * s.sy) / SQRT((s.n * s.sxx - s.sx * s.sx) * (s.n * s.syy - s.sy * s.sy)), 2) AS corr_unemployment_wage,
       ROUND((u.n * u.sxy - u.sx * u.sy) / SQRT((u.n * u.sxx - u.sx * u.sx) * (u.n * u.syy - u.sy * u.sy)), 2) AS corr_urbanization_unemployment
FROM s
LEFT JOIN u USING (year)
ORDER BY s.year
""")

fig, ax = plt.subplots(figsize=(9.5, 3.9))
ax.plot(jobs_pay.year, jobs_pay.corr_unemployment_wage, color=AMBER, marker="o", ms=4, lw=2.2, label="Unemployment vs log wage")
ax.plot(jobs_pay.year, jobs_pay.corr_urbanization_unemployment, color=BLUE, marker="s", ms=4, lw=2.2, label="Urbanization vs unemployment")
ax.axhline(0, color="#4a5568", lw=0.8)
ax.set_ylabel("Correlation across counties")
ax.set_title("How tightly pay, jobs and urbanization are linked")
ax.legend(frameon=False, fontsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
jobs_pay.iloc[[0, 5, 10, 15, 20]]

population = q("""
WITH change AS (
    SELECT p.name, p.voivodship, p.is_city,
           ROUND(a.population, 1)                               AS thousand_2010,
           ROUND(b.population, 1)                               AS thousand_2024,
           ROUND(100.0 * (b.population / a.population - 1), 1) AS change_pct,
           ROUND(b.median_age, 1)                               AS median_age_2024
    FROM panel a
    JOIN panel b ON a.powiat_id = b.powiat_id AND a.year = 2010 AND b.year = 2024
    JOIN powiats p ON p.powiat_id = a.powiat_id
    WHERE a.powiat_id NOT IN (SELECT powiat_id FROM series_breaks)
),
numbered AS (
    SELECT *, ROW_NUMBER() OVER (ORDER BY change_pct DESC) AS up,
              ROW_NUMBER() OVER (ORDER BY change_pct)      AS down,
              SUM(change_pct > 0) OVER ()                  AS growing,
              COUNT(*) OVER ()                             AS total
    FROM change
)
SELECT name, voivodship, is_city, thousand_2010, thousand_2024, change_pct, median_age_2024, growing, total
FROM numbered
WHERE up <= 8 OR down <= 8
ORDER BY change_pct DESC
""")

fig, ax = plt.subplots(figsize=(9, 5.2))
colors = [AMBER if v > 0 else BLUE for v in population.change_pct]
ax.barh(population.name[::-1], population.change_pct[::-1], color=colors[::-1])
ax.axvline(0, color="#4a5568", lw=0.8)
for y, v in enumerate(population.change_pct[::-1]):
    ax.text(v + (0.5 if v > 0 else -0.5), y, f"{v:+.1f}%", va="center", ha="left" if v > 0 else "right", fontsize=8)
ax.set_xlim(population.change_pct.min() - 6, population.change_pct.max() + 7)
ax.set_title("Population change 2010 to 2024: eight fastest growing and shrinking counties")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
population[["growing", "total"]].iloc[0]

ageing = q("""
SELECT f.year,
       CASE WHEN p.is_city = 1 THEN 'City with powiat status' ELSE 'Land county' END AS type,
       COUNT(*)                                  AS counties,
       ROUND(AVG(f.median_age), 1)               AS avg_median_age,
       ROUND(MIN(f.median_age), 1)               AS youngest,
       ROUND(MAX(f.median_age), 1)               AS oldest
FROM panel f
JOIN powiats p USING (powiat_id)
WHERE f.median_age IS NOT NULL
GROUP BY f.year, p.is_city
ORDER BY type, f.year
""")

fig, ax = plt.subplots(figsize=(8.5, 3.9))
for t, g in ageing.groupby("type"):
    ax.plot(g.year, g.avg_median_age, marker="o", lw=2.2, label=t)
ax.set_ylabel("Average median age (years)")
ax.set_title("Median age of the population, 2018 to 2025")
ax.legend(frameon=False, fontsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
ageing[ageing.year.isin([2018, 2025])]

economy = q("""
WITH c AS (
    SELECT f.*, MAX(emp_agriculture, emp_industry, emp_construction, emp_services, emp_public) AS top_share
    FROM panel f
    WHERE f.year = 2024 AND f.emp_services IS NOT NULL
),
labelled AS (
    SELECT *, CASE WHEN emp_services = top_share THEN 'Market services'
                   WHEN emp_industry = top_share THEN 'Industry'
                   WHEN emp_agriculture = top_share THEN 'Agriculture'
                   WHEN emp_public = top_share THEN 'Public services'
                   ELSE 'Construction' END AS dominant
    FROM c
)
SELECT dominant                              AS dominant_sector,
       COUNT(*)                              AS counties,
       ROUND(AVG(top_share), 1)              AS avg_top_share_pct,
       ROUND(AVG(wage))                      AS avg_wage_pln,
       ROUND(AVG(unemployment), 1)           AS avg_unemployment_pct,
       ROUND(AVG(median_age), 1)             AS avg_median_age
FROM labelled
GROUP BY dominant
ORDER BY counties DESC
""")

fig, ax = plt.subplots(figsize=(8.5, 3.9))
ax.bar(economy.dominant_sector, economy.avg_wage_pln, color=[AMBER, BLUE, GREY, "#68a357", "#b794f4"][:len(economy)])
for x, (w, n) in enumerate(zip(economy.avg_wage_pln, economy.counties)):
    ax.text(x, w + 60, f"{w:,.0f}\n({n} counties)", ha="center", fontsize=8)
ax.set_ylim(0, economy.avg_wage_pln.max() * 1.22)
ax.set_ylabel("Average gross monthly wage, 2024 (PLN)")
ax.set_title("Wages by the biggest sector of employment in the county")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
economy

persistent = q("""
WITH q AS (
    SELECT powiat_id, year, unemployment,
           NTILE(4) OVER (PARTITION BY year ORDER BY unemployment DESC) AS quartile
    FROM panel
    WHERE unemployment IS NOT NULL
)
SELECT p.name, p.voivodship,
       COUNT(*)                         AS years,
       SUM(q.quartile = 1)              AS years_in_worst_quartile,
       ROUND(AVG(q.unemployment), 1)    AS avg_unemployment,
       COUNT(*) OVER ()                 AS persistent_total
FROM q
JOIN powiats p USING (powiat_id)
GROUP BY q.powiat_id
HAVING years = 21 AND years_in_worst_quartile = 21
ORDER BY avg_unemployment DESC
LIMIT 12
""")

persistent
