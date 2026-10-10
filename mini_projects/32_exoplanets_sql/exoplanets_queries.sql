-- 6,445 worlds beyond the Solar System, in plain SQL (SQLite dialect)
-- Run against exoplanets.db: https://exoplanetarchive.ipac.caltech.edu/

-- 1. How do we find planets?
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
ORDER BY planets DESC;


-- 2. When were they found?
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
ORDER BY year;


-- 3. Which telescopes did the work?
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
LIMIT 12;


-- 4. What sizes of planet do we see?
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
ORDER BY size_class;


-- 5. Is there a gap in planet sizes?
SELECT ROUND(radius_earth * 10) / 10  AS radius_bin,
       COUNT(*)                       AS planets
FROM planets
WHERE radius_source = 'measured (transit)' AND radius_earth BETWEEN 1 AND 4 AND period_days < 100
GROUP BY radius_bin
ORDER BY radius_bin;


-- 6. Are hot Jupiters as common as they seem?
SELECT disc_year                                                          AS year,
       COUNT(*)                                                           AS planets,
       SUM(radius_earth > 8 AND period_days < 10)                         AS hot_jupiters,
       ROUND(100.0 * SUM(radius_earth > 8 AND period_days < 10) / COUNT(*), 1) AS hot_jupiter_pct,
       SUM(radius_earth < 2)                                              AS smaller_than_2_earths
FROM planets
WHERE radius_source = 'measured (transit)' AND radius_earth IS NOT NULL AND period_days IS NOT NULL
GROUP BY disc_year
ORDER BY disc_year;


-- 7. How are planets in a system spaced?
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
ORDER BY period_ratio;


-- 8. What stars do they orbit?
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
ORDER BY star_class;


-- 9. When does a planet turn from rock to gas?
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
ORDER BY radius_bin;


-- 10. Which planets look most like Earth?
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
LIMIT 12;


-- 11. Which planetary systems are the closest?
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
LIMIT 12;
