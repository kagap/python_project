"""The world's airline network, in plain SQL.

Plain-script version of the notebook. Set OPENFLIGHTS_DB to the path of openflights.db.
"""

import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DB_PATH = Path(os.environ.get("OPENFLIGHTS_DB", "openflights.db"))
BLUE, AMBER, GREY, RED = "#2b6cb0", "#e8a33d", "#a0aec0", "#c0392b"
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

hubs = q("""
WITH pairs AS (
    SELECT DISTINCT src, dst FROM routes
)
SELECT RANK() OVER (ORDER BY COUNT(*) DESC) AS airport_rank,
       p.src AS iata, a.city, a.country, COUNT(*) AS destinations
FROM pairs p
JOIN airports a ON a.iata = p.src
GROUP BY p.src, a.city, a.country
ORDER BY destinations DESC, iata
LIMIT 15
""")

top = hubs.iloc[::-1]
fig, ax = plt.subplots(figsize=(10, 4.8))
ax.barh([f"{i}  {c}" for i, c in zip(top.iata, top.city)], top.destinations, color=BLUE)
for y, v in enumerate(top.destinations):
    ax.text(v + 2, y, v, va="center", fontsize=8)
ax.set_xlabel("Airports reachable non-stop")
ax.set_title("The fifteen best-connected airports")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

hubs

concentration = q("""
WITH d AS (
    SELECT src AS iata, COUNT(DISTINCT dst) AS destinations FROM routes GROUP BY src
),
r AS (
    SELECT iata, destinations,
           ROW_NUMBER() OVER (ORDER BY destinations DESC, iata)                                          AS airport_rank,
           COUNT(*) OVER ()                                                                              AS airports,
           SUM(destinations) OVER (ORDER BY destinations DESC, iata ROWS UNBOUNDED PRECEDING)           AS running,
           SUM(destinations) OVER ()                                                                     AS total_pairs
    FROM d
)
SELECT airport_rank, iata, destinations,
       ROUND(100.0 * airport_rank / airports, 2) AS airport_pct,
       ROUND(100.0 * running / total_pairs, 1)   AS route_share_pct
FROM r
ORDER BY airport_rank
""")

fig, ax = plt.subplots(figsize=(8.5, 4.6))
ax.plot(concentration.airport_pct, concentration.route_share_pct, color=BLUE, lw=2.6)
ax.plot([0, 100], [0, 100], color=GREY, ls="--", lw=1)
for p in (1, 5, 10, 50):
    r = concentration[concentration.airport_pct >= p].iloc[0]
    ax.scatter([r.airport_pct], [r.route_share_pct], color=AMBER, zorder=3)
    ax.annotate(f"top {p}% of airports: {r.route_share_pct:.0f}% of pairs", (r.airport_pct, r.route_share_pct), xytext=(10, -14), textcoords="offset points", fontsize=8)
ax.set_xlabel("Share of airports, busiest first (%)")
ax.set_ylabel("Share of all airport pairs (%)")
ax.set_title("A few airports hold most of the connections")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

concentration[concentration.airport_rank.isin([34, 170, 341, 852, 1704])]

sizes = q("""
WITH d AS (
    SELECT src AS iata, COUNT(DISTINCT dst) AS destinations FROM routes GROUP BY src
)
SELECT COUNT(*)                         AS airports_with_departures,
       SUM(destinations = 1)            AS one_destination,
       SUM(destinations <= 3)           AS three_or_fewer,
       SUM(destinations >= 100)         AS hundred_or_more,
       ROUND(AVG(destinations), 1)      AS average,
       MAX(destinations)                AS most
FROM d
""")

sizes

domestic = q("""
WITH l AS (
    SELECT DISTINCT r.src, r.dst, a.country AS from_country, b.country AS to_country
    FROM routes r
    JOIN airports a ON a.iata = r.src
    JOIN airports b ON b.iata = r.dst
)
SELECT from_country AS country,
       COUNT(*)                                                       AS airport_pairs,
       ROUND(100.0 * SUM(from_country = to_country) / COUNT(*), 1)    AS domestic_pct
FROM l
GROUP BY from_country
HAVING COUNT(*) >= 500
ORDER BY domestic_pct DESC
""")

d = domestic.iloc[::-1]
fig, ax = plt.subplots(figsize=(9.5, 4.8))
ax.barh(d.country, d.domestic_pct, color=[AMBER if v >= 50 else BLUE for v in d.domestic_pct])
for y, v in enumerate(d.domestic_pct):
    ax.text(v + 1, y, f"{v:.0f}%", va="center", fontsize=8)
ax.axvline(50, color="#4a5568", ls="--", lw=1)
ax.set_xlim(0, 100)
ax.set_xlabel("Share of airport pairs that stay inside the country (%)")
ax.set_title("Domestic versus international routes")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

domestic

bands = q("""
WITH l AS (
    SELECT DISTINCT r.src, r.dst, 6371 * acos(MIN(1.0, MAX(-1.0, sin(radians(a.lat)) * sin(radians(b.lat)) + cos(radians(a.lat)) * cos(radians(b.lat)) * cos(radians(b.lon - a.lon))))) AS km
    FROM routes r
    JOIN airports a ON a.iata = r.src
    JOIN airports b ON b.iata = r.dst
)
SELECT CAST(km / 500 AS INT) * 500                                                AS from_km,
       COUNT(*)                                                                    AS pairs,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)                          AS pct,
       ROUND(100.0 * SUM(COUNT(*)) OVER (ORDER BY CAST(km / 500 AS INT)) / SUM(COUNT(*)) OVER (), 1) AS cumulative_pct
FROM l
GROUP BY from_km
ORDER BY from_km
""")

fig, ax = plt.subplots(figsize=(10, 4))
ax.bar(bands.from_km + 250, bands.pairs, width=440, color=[BLUE if k < 4000 else AMBER for k in bands.from_km])
ax.set_xlabel("Distance (km, bands of 500)")
ax.set_ylabel("Airport pairs, one way")
ax.set_title("Most routes are short; the long-haul tail is thin")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

bands.assign(from_km=bands.from_km.astype(str) + '+')

longest = q("""
WITH pairs AS (
    SELECT DISTINCT MIN(src, dst) AS src, MAX(src, dst) AS dst FROM routes
)
SELECT r.src, r.dst, a.city AS from_city, b.city AS to_city, ROUND(6371 * acos(MIN(1.0, MAX(-1.0, sin(radians(a.lat)) * sin(radians(b.lat)) + cos(radians(a.lat)) * cos(radians(b.lat)) * cos(radians(b.lon - a.lon)))))) AS km
FROM pairs r
JOIN airports a ON a.iata = r.src
JOIN airports b ON b.iata = r.dst
ORDER BY km DESC
LIMIT 8
""")

longest

airlines = q("""
WITH names AS (
    SELECT iata, MIN(name) AS name
    FROM airlines
    WHERE active = 'Y' AND iata IS NOT NULL AND iata <> ''
    GROUP BY iata
    HAVING COUNT(*) = 1
),
leg AS (
    SELECT r.airline_code, r.src, 6371 * acos(MIN(1.0, MAX(-1.0, sin(radians(a.lat)) * sin(radians(b.lat)) + cos(radians(a.lat)) * cos(radians(b.lat)) * cos(radians(b.lon - a.lon))))) AS km
    FROM routes r
    JOIN airports a ON a.iata = r.src
    JOIN airports b ON b.iata = r.dst
    WHERE r.codeshare IS NULL AND r.airline_code IN (SELECT iata FROM names)
),
per_airport AS (
    SELECT airline_code, src, COUNT(*) AS n FROM leg GROUP BY airline_code, src
),
hub AS (
    SELECT airline_code, SUM(n) AS routes, COUNT(*) AS airports_served, MAX(n) AS top_n
    FROM per_airport GROUP BY airline_code
),
top AS (
    SELECT airline_code, src AS top_airport
    FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY airline_code ORDER BY n DESC, src) AS rk FROM per_airport)
    WHERE rk = 1
),
dist AS (
    SELECT airline_code, ROUND(AVG(km)) AS avg_km FROM leg GROUP BY airline_code
)
SELECT n.name AS airline, h.airline_code AS code, h.routes, h.airports_served, t.top_airport,
       ROUND(100.0 * h.top_n / h.routes, 1) AS top_airport_share_pct, d.avg_km
FROM hub h
JOIN names n ON n.iata = h.airline_code
JOIN top   t USING (airline_code)
JOIN dist  d USING (airline_code)
WHERE h.routes >= 300
ORDER BY h.routes DESC
""")

fig, ax = plt.subplots(figsize=(10, 4.8))
ax.scatter(airlines.airports_served, airlines.top_airport_share_pct, s=airlines.routes / 5, color=BLUE, alpha=0.55, edgecolor="none")
offsets = {"Southwest Airlines": (6, -10), "Wizz Air": (-40, 4), "Ryanair": (8, -3), "easyJet": (8, -3), "American Airlines": (-8, 9), "US Airways": (-12, 9)}
for _, r in airlines.iterrows():
    if r.top_airport_share_pct >= 25 or r.airline in offsets or r.airline in ("Delta Air Lines", "Air China", "Azul"):
        ax.annotate(r.airline.replace(" Airlines", "").replace(" Airways", "").replace(" Air Lines", "").replace(" Air Shuttle", ""),
                    (r.airports_served, r.top_airport_share_pct), xytext=offsets.get(r.airline, (5, 3)), textcoords="offset points", fontsize=7.5)
ax.set_xlabel("Airports served")
ax.set_ylabel("Share of routes from the busiest airport (%)")
ax.set_title("Hub-and-spoke or point-to-point? (dot size = routes)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

airlines.head(12)

reach = q("""
WITH RECURSIVE starts(a) AS (
    VALUES ('WAW'), ('ATL'), ('LHR'), ('SIN'), ('JFK')
),
hops(start, airport, n) AS (
    SELECT a, a, 0 FROM starts
    UNION
    SELECT h.start, r.dst, h.n + 1
    FROM hops h
    JOIN routes r ON r.src = h.airport
    WHERE h.n < 6
),
best AS (
    SELECT start, airport, MIN(n) AS n FROM hops GROUP BY start, airport
),
network AS (
    SELECT COUNT(*) AS airports FROM (SELECT src FROM routes UNION SELECT dst FROM routes)
)
SELECT start, n AS flights, COUNT(*) AS new_airports,
       ROUND(100.0 * SUM(COUNT(*)) OVER (PARTITION BY start ORDER BY n) / (SELECT airports FROM network), 1) AS reached_pct
FROM best
GROUP BY start, n
ORDER BY start, n
""")

fig, ax = plt.subplots(figsize=(9.5, 4.2))
colors = {"ATL": GREY, "JFK": AMBER, "LHR": BLUE, "SIN": "#68a357", "WAW": RED}
for start, d in reach.groupby("start"):
    ax.plot(d.flights, d.reached_pct, marker="o", lw=2.4, color=colors[start], label=start)
ax.set_xlim(0.8, 6.2)
ax.set_xlabel("Number of flights")
ax.set_ylabel("Share of the network reached (%)")
ax.legend(frameon=False, fontsize=9, title="starting airport")
ax.set_title("Flights needed to reach the rest of the world")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

reach[reach.flights.between(1, 4)].pivot(index='flights', columns='start', values='reached_pct')

itineraries = q("""
WITH RECURSIVE reach(airport, n) AS (
    SELECT 'WAW', 0
    UNION
    SELECT r.dst, h.n + 1 FROM reach h JOIN routes r ON r.src = h.airport WHERE h.n < 6
),
best AS (
    SELECT airport, MIN(n) AS n FROM reach GROUP BY airport
),
pred AS (
    SELECT r.dst AS airport, b.n + 1 AS n, MIN(r.src) AS src
    FROM routes r JOIN best b ON b.airport = r.src
    GROUP BY r.dst, b.n
),
target(airport) AS (
    VALUES ('IPC'), ('USH'), ('LYR'), ('PPT'), ('LXA'), ('ULN'), ('MLE'), ('KTM'), ('GUM'), ('HNL')
),
back(airport, n, flights, path) AS (
    SELECT b.airport, b.n, b.n, b.airport FROM best b JOIN target t USING (airport)
    UNION ALL
    SELECT p.src, k.n - 1, k.flights, p.src || ' > ' || k.path
    FROM back k
    JOIN pred p ON p.airport = k.airport AND p.n = k.n
    WHERE k.n > 0
)
SELECT SUBSTR(path, -3) AS destination, a.city, a.country, flights, path AS itinerary
FROM back
JOIN airports a ON a.iata = SUBSTR(path, -3)
WHERE n = 0
ORDER BY flights DESC, destination
""")

itineraries

highest = q("""
WITH served AS (
    SELECT src AS iata FROM routes UNION SELECT dst FROM routes
)
SELECT RANK() OVER (ORDER BY altitude_ft DESC) AS altitude_rank, name, city, country, altitude_ft, ROUND(altitude_ft * 0.3048) AS altitude_m
FROM airports
WHERE iata IN (SELECT iata FROM served)
ORDER BY altitude_ft DESC
LIMIT 8
""")

highest

audit = q("""
WITH p AS (
    SELECT DISTINCT src, dst FROM routes
),
used AS (
    SELECT src AS iata FROM routes UNION SELECT dst FROM routes
)
SELECT (SELECT COUNT(*) FROM p)                                                                              AS airport_pairs,
       (SELECT COUNT(*) FROM p a WHERE NOT EXISTS (SELECT 1 FROM p b WHERE b.src = a.dst AND b.dst = a.src)) AS pairs_without_return,
       (SELECT COUNT(*) FROM airports WHERE iata IS NOT NULL)                                               AS airports_with_code,
       (SELECT COUNT(*) FROM airports WHERE iata IS NOT NULL AND iata NOT IN (SELECT iata FROM used))       AS airports_without_routes,
       (SELECT COUNT(*) FROM routes WHERE airline_id IS NULL)                                               AS routes_without_airline_id,
       (SELECT COUNT(*) FROM routes r
         WHERE NOT EXISTS (SELECT 1 FROM airports a WHERE a.iata = r.src)
            OR NOT EXISTS (SELECT 1 FROM airports a WHERE a.iata = r.dst))                                  AS routes_with_unknown_airport
""")

audit.T.rename(columns={0: 'value'})
