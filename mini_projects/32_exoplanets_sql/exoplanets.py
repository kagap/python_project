"""6,445 worlds beyond the Solar System, in plain SQL.

Plain-script version of the notebook. Set EXO_DB to the path of exoplanets.db.
"""


import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DB_PATH = Path(os.environ.get("EXO_DB", "exoplanets.db"))
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

methods = q("""
WITH r AS (
    SELECT method, mass_earth,
           ROW_NUMBER() OVER (PARTITION BY method ORDER BY mass_earth) AS rn,
           COUNT(*)     OVER (PARTITION BY method)                     AS n
    FROM planets
    WHERE mass_source IN ('measured', 'minimum mass (Msini)') AND mass_earth > 0
),
med AS (
    SELECT method, AVG(mass_earth) AS median_mass
    FROM r
    WHERE rn IN ((n + 1) / 2, (n + 2) / 2)
    GROUP BY method
)
SELECT p.method,
       COUNT(*)                                            AS planets,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)  AS share_pct,
       MIN(p.disc_year)                                    AS first_year,
       ROUND(m.median_mass)                                AS median_observed_mass_earth,
       ROUND(AVG(p.distance_pc))                           AS avg_distance_pc
FROM planets p
LEFT JOIN med m USING (method)
GROUP BY p.method
ORDER BY planets DESC
""")

fig, ax = plt.subplots(figsize=(9, 4.2))
ax.barh(methods.method[::-1], methods.planets[::-1], color=BLUE)
for y, (v, s, m) in enumerate(zip(methods.planets[::-1], methods.share_pct[::-1], methods.median_observed_mass_earth[::-1])):
    ax.text(v + 40, y, f"{v:,}  ({s}%, median observed mass {m:,.0f} Earths)" if m == m else f"{v:,}  ({s}%)", va="center", fontsize=8)
ax.set_xlim(0, methods.planets.max() * 1.6)
ax.set_title("Confirmed exoplanets by discovery method")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
methods.head(4)

by_year = q("""
WITH y AS (
    SELECT disc_year                                   AS year,
           COUNT(*)                                    AS planets,
           SUM(method = 'Transit')                     AS transit,
           SUM(method = 'Radial Velocity')             AS radial_velocity,
           SUM(method = 'Microlensing')                AS microlensing,
           SUM(method = 'Imaging')                     AS imaging,
           SUM(method NOT IN ('Transit', 'Radial Velocity', 'Microlensing', 'Imaging')) AS other
    FROM planets
    GROUP BY disc_year
)
SELECT year, planets, transit, radial_velocity, microlensing, imaging, other,
       SUM(planets) OVER (ORDER BY year) AS total_so_far
FROM y
ORDER BY year
""")

cols = ["radial_velocity", "transit", "microlensing", "imaging", "other"]
fig, ax = plt.subplots(figsize=(10, 4.2))
bottom = np.zeros(len(by_year))
for col, color in zip(cols, [BLUE, AMBER, "#68a357", "#b794f4", GREY]):
    ax.bar(by_year.year, by_year[col], bottom=bottom, label=col.replace("_", " "), color=color)
    bottom += by_year[col].values
ax.set_ylabel("Planets announced in the year")
ax.set_title("Exoplanet discoveries per year (2014 and 2016 are Kepler batches, 2026 is partial)")
ax.legend(frameon=False, fontsize=8, ncol=2)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
by_year.sort_values("planets", ascending=False).head(5)

facilities = q("""
SELECT RANK() OVER (ORDER BY COUNT(*) DESC)               AS rank,
       facility,
       MIN(locale)                                        AS locale,
       COUNT(*)                                           AS planets,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS share_pct,
       MIN(disc_year)                                     AS first_year,
       MAX(disc_year)                                     AS last_year
FROM planets
GROUP BY facility
ORDER BY planets DESC
LIMIT 12
""")

facilities

sizes = q("""
SELECT CASE WHEN radius_earth < 1.25 THEN '1) Earth-size (< 1.25)'
            WHEN radius_earth < 2    THEN '2) Super-Earth (1.25-2)'
            WHEN radius_earth < 4    THEN '3) Sub-Neptune (2-4)'
            WHEN radius_earth < 6    THEN '4) Neptune-size (4-6)'
            WHEN radius_earth < 15   THEN '5) Jupiter-size (6-15)'
            ELSE                          '6) Larger (15+)' END AS size_class,
       COUNT(*)                                                       AS planets,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)             AS share_pct,
       ROUND(100.0 * AVG(CASE WHEN period_days IS NOT NULL THEN period_days < 10 END), 1) AS orbit_under_10_days_pct,
       ROUND(AVG(eq_temp_k))                                          AS avg_eq_temp_k,
       ROUND(AVG(distance_pc))                                        AS avg_distance_pc
FROM planets
WHERE radius_source = 'measured (transit)' AND radius_earth IS NOT NULL
GROUP BY size_class
ORDER BY size_class
""")

fig, ax = plt.subplots(figsize=(9, 4))
labels = sizes.size_class.str[3:]
ax.bar(labels, sizes.planets, color=AMBER)
for x, (n, p) in enumerate(zip(sizes.planets, sizes.orbit_under_10_days_pct)):
    ax.text(x, n + 25, f"{n:,}\n{p:.0f}% under 10 days", ha="center", fontsize=8)
ax.set_ylim(0, sizes.planets.max() * 1.18)
ax.set_ylabel("Transiting planets")
ax.set_title("Transiting planets by size class")
ax.tick_params(axis="x", labelsize=7.5)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
sizes

valley = q("""
SELECT ROUND(radius_earth * 10) / 10  AS radius_bin,
       COUNT(*)                       AS planets
FROM planets
WHERE radius_source = 'measured (transit)' AND radius_earth BETWEEN 1 AND 4 AND period_days < 100
GROUP BY radius_bin
ORDER BY radius_bin
""")

fig, ax = plt.subplots(figsize=(9.5, 3.9))
ax.bar(valley.radius_bin, valley.planets, width=0.085, color=BLUE)
ax.set_xlabel("Planet radius (Earth radii)")
ax.set_ylabel("Transiting planets with orbital period under 100 days")
ax.set_title("How many planets of each size? (bins of 0.1 Earth radii)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
valley.sort_values("planets", ascending=False).head(5)

hot_jupiters = q("""
SELECT disc_year                                                          AS year,
       COUNT(*)                                                           AS planets,
       SUM(radius_earth > 8 AND period_days < 10)                         AS hot_jupiters,
       ROUND(100.0 * SUM(radius_earth > 8 AND period_days < 10) / COUNT(*), 1) AS hot_jupiter_pct,
       SUM(radius_earth < 2)                                              AS smaller_than_2_earths
FROM planets
WHERE radius_source = 'measured (transit)' AND radius_earth IS NOT NULL AND period_days IS NOT NULL
GROUP BY disc_year
ORDER BY disc_year
""")

fig, ax = plt.subplots(figsize=(10, 4))
ax.plot(hot_jupiters.year, hot_jupiters.hot_jupiter_pct, color=AMBER, marker="o", ms=4, lw=2.4)
ax.set_ylabel("Hot Jupiters, % of the year's new transiting planets")
ax.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
ax.set_title("The share of hot Jupiters among new transiting planets")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
hot_jupiters.tail(8)

resonance = q("""
WITH s AS (
    SELECT host, period_days,
           LAG(period_days) OVER (PARTITION BY host ORDER BY period_days) AS inner_period
    FROM planets
    WHERE period_days IS NOT NULL
)
SELECT ROUND(period_days / inner_period, 1)  AS period_ratio,
       COUNT(*)                              AS neighbouring_pairs
FROM s
WHERE inner_period IS NOT NULL AND period_days / inner_period < 4
GROUP BY period_ratio
ORDER BY period_ratio
""")

fig, ax = plt.subplots(figsize=(9.5, 3.9))
ax.bar(resonance.period_ratio, resonance.neighbouring_pairs, width=0.085, color=BLUE)
for x, label in [(1.5, "3:2"), (2.0, "2:1"), (3.0, "3:1")]:
    ax.axvline(x, color=AMBER, lw=1.2, ls="--")
    ax.text(x, resonance.neighbouring_pairs.max() * 1.02, label, ha="center", fontsize=8, color="#b7791f")
ax.set_xlabel("Period of the outer planet divided by that of its inner neighbour")
ax.set_ylabel("Neighbouring pairs")
ax.set_title("Spacing of neighbouring planets in the same system")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
resonance.sort_values("neighbouring_pairs", ascending=False).head(5)

stars = q("""
SELECT CASE WHEN star_teff_k < 3900 THEN '1) M dwarf (< 3900 K)'
            WHEN star_teff_k < 5300 THEN '2) K dwarf (3900-5300 K)'
            WHEN star_teff_k < 6000 THEN '3) G, Sun-like (5300-6000 K)'
            WHEN star_teff_k < 7300 THEN '4) F (6000-7300 K)'
            ELSE                         '5) A or hotter (> 7300 K)' END    AS star_class,
       COUNT(*)                                                              AS planets,
       COUNT(DISTINCT host)                                                  AS stars,
       ROUND(1.0 * COUNT(*) / COUNT(DISTINCT host), 2)                       AS planets_per_star,
       ROUND(100.0 * AVG(CASE WHEN radius_source = 'measured (transit)' THEN radius_earth < 2 END), 1) AS transiting_under_2_earths_pct,
       ROUND(AVG(distance_pc))                                               AS avg_distance_pc
FROM planets
WHERE star_teff_k IS NOT NULL
GROUP BY star_class
ORDER BY star_class
""")

fig, ax = plt.subplots(figsize=(9, 3.9))
ax.bar(stars.star_class.str[3:], stars.stars, color=AMBER)
for x, (n, p) in enumerate(zip(stars.stars, stars.planets_per_star)):
    ax.text(x, n + 40, f"{n:,} stars\n{p} planets each", ha="center", fontsize=8)
ax.set_ylim(0, stars.stars.max() * 1.2)
ax.set_ylabel("Stars with known planets")
ax.set_title("Planet-hosting stars by temperature class")
ax.tick_params(axis="x", labelsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
stars

density = q("""
WITH d AS (
    SELECT ROUND(radius_earth * 2) / 2                       AS radius_bin,
           5.51 * mass_earth / POWER(radius_earth, 3)        AS density
    FROM planets
    WHERE mass_source = 'measured' AND radius_source = 'measured (transit)'
      AND radius_earth BETWEEN 0.5 AND 4 AND mass_earth > 0
),
ok AS (SELECT * FROM d WHERE density <= 30),
r AS (
    SELECT radius_bin, density,
           ROW_NUMBER() OVER (PARTITION BY radius_bin ORDER BY density) AS rn,
           COUNT(*)     OVER (PARTITION BY radius_bin)                  AS n
    FROM ok
)
SELECT radius_bin,
       n                                   AS planets,
       ROUND(AVG(density), 2)              AS median_density_g_cm3,
       (SELECT COUNT(*) FROM d WHERE d.radius_bin = r.radius_bin AND d.density > 30) AS impossible_excluded
FROM r
WHERE rn IN ((n + 1) / 2, (n + 2) / 2)
GROUP BY radius_bin
ORDER BY radius_bin
""")

fig, ax = plt.subplots(figsize=(8.5, 4))
ax.bar(density.radius_bin, density.median_density_g_cm3, width=0.42, color=BLUE)
for x, v, n in zip(density.radius_bin, density.median_density_g_cm3, density.planets):
    ax.text(x, v + 0.15, f"{v:.1f}\n(n={n})", ha="center", fontsize=7.5)
ax.axhline(5.51, color=AMBER, lw=1.4, ls="--")
ax.text(density.radius_bin.max() + 0.2, 5.51, "Earth", color="#b7791f", fontsize=8, va="center")
ax.set_xlabel("Planet radius (Earth radii)")
ax.set_ylabel("Median bulk density (g/cm3)")
ax.set_title("Transiting planets with a measured mass: density against size")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
density

earthlike = q("""
SELECT planet, host,
       ROUND(radius_earth, 2)                                            AS radius_earth,
       ROUND(insolation_earth, 2)                                        AS insolation_earth,
       ROUND(distance_pc * 3.2616)                                       AS distance_light_years,
       mass_source,
       ROUND(ABS(LN(radius_earth)) + ABS(LN(insolation_earth)), 2)       AS distance_from_earth,
       disc_year
FROM planets
WHERE radius_source = 'measured (transit)' AND radius_earth BETWEEN 0.5 AND 1.6 AND insolation_earth BETWEEN 0.3 AND 1.5
ORDER BY distance_from_earth
LIMIT 12
""")

earthlike

nearest = q("""
SELECT host,
       ROUND(MIN(distance_pc) * 3.2616, 1)                                  AS distance_light_years,
       COUNT(*)                                                             AS planets,
       ROUND(MIN(star_teff_k))                                              AS star_temperature_k,
       GROUP_CONCAT(planet, ', ')                                           AS planet_names,
       COUNT(*) OVER (ORDER BY MIN(distance_pc))                            AS systems_up_to_here
FROM planets
WHERE distance_pc IS NOT NULL
GROUP BY host
ORDER BY MIN(distance_pc)
LIMIT 12
""")

nearest
