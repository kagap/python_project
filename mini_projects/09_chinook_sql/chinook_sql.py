"""A digital music store, queried in plain SQL.

Plain-script version of the notebook. Set CHINOOK_DB to the path of Chinook_Sqlite.sqlite.
"""


import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DB_PATH = Path(os.environ.get("CHINOOK_DB", "Chinook_Sqlite.sqlite"))
AMBER, BLUE, GREY = "#e8a33d", "#2b6cb0", "#a0aec0"
plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
                     "grid.alpha": 0.25, "figure.dpi": 110})
pd.set_option("display.width", 120)

con = sqlite3.connect(DB_PATH)


def q(sql):
    """Run a query and return the result as a DataFrame."""
    return pd.read_sql_query(sql, con)

tables = q("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").name
pd.DataFrame({"table": tables,
              "rows": [con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables]})

yearly = q("""
WITH yearly AS (
    SELECT CAST(strftime('%Y', InvoiceDate) AS INT) AS year,
           COUNT(*)                                 AS invoices,
           ROUND(SUM(Total), 2)                     AS revenue
    FROM Invoice
    GROUP BY year
)
SELECT year, invoices, revenue,
       ROUND(100.0 * (revenue - LAG(revenue) OVER (ORDER BY year))
                   / LAG(revenue) OVER (ORDER BY year), 1) AS growth_pct
FROM yearly
ORDER BY year
""")

fig, ax = plt.subplots(figsize=(8, 4))
ax.bar(yearly.year.astype(str), yearly.revenue, color=AMBER)
for x, (rev, g) in enumerate(zip(yearly.revenue, yearly.growth_pct)):
    label = f"${rev:,.0f}" + ("" if pd.isna(g) else f"\n{g:+.1f}%")
    ax.text(x, rev + 6, label, ha="center", fontsize=9)
ax.set_title("Revenue per year (and change vs previous year)")
ax.set_ylabel("USD")
ax.set_ylim(0, yearly.revenue.max() * 1.18)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

artists = q("""
SELECT ar.Name                                   AS artist,
       ROUND(SUM(il.UnitPrice * il.Quantity), 2) AS revenue,
       COUNT(DISTINCT t.TrackId)                 AS tracks_sold
FROM InvoiceLine il
JOIN Track  t  USING (TrackId)
JOIN Album  al USING (AlbumId)
JOIN Artist ar USING (ArtistId)
GROUP BY ar.ArtistId
ORDER BY revenue DESC
LIMIT 10
""")

fig, ax = plt.subplots(figsize=(8, 4.2))
ax.barh(artists.artist[::-1], artists.revenue[::-1], color=BLUE)
for y, v in enumerate(artists.revenue[::-1]):
    ax.text(v + 1.5, y, f"${v:,.0f}", va="center", fontsize=9)
ax.set_title("Top 10 artists by revenue")
ax.set_xlabel("USD")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

genres = q("""
WITH genre_revenue AS (
    SELECT ge.Name AS genre, SUM(il.UnitPrice * il.Quantity) AS revenue
    FROM InvoiceLine il
    JOIN Track t  USING (TrackId)
    JOIN Genre ge USING (GenreId)
    GROUP BY ge.GenreId
)
SELECT genre,
       ROUND(revenue, 2)                                              AS revenue,
       ROUND(100.0 * revenue / SUM(revenue) OVER (), 1)               AS share_pct,
       ROUND(100.0 * SUM(revenue) OVER (ORDER BY revenue DESC)
                   / SUM(revenue) OVER (), 1)                         AS cumulative_pct
FROM genre_revenue
ORDER BY revenue DESC
LIMIT 10
""")

fig, ax = plt.subplots(figsize=(8.5, 4.2))
ax.bar(genres.genre, genres.share_pct, color=AMBER)
ax.set_ylabel("Share of revenue (%)")
ax.tick_params(axis="x", rotation=40)
ax2 = ax.twinx()
ax2.plot(genres.genre, genres.cumulative_pct, color=BLUE, marker="o")
ax2.set_ylabel("Cumulative share (%)")
ax2.set_ylim(0, 105)
ax2.grid(False)
ax2.spines["right"].set_visible(True)
ax.set_title("Genres by share of revenue, with cumulative line")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

countries = q("""
WITH by_country AS (
    SELECT BillingCountry               AS country,
           COUNT(DISTINCT CustomerId)   AS customers,
           SUM(Total)                   AS revenue
    FROM Invoice
    GROUP BY BillingCountry
)
SELECT RANK() OVER (ORDER BY revenue DESC) AS rank,
       country, customers,
       ROUND(revenue, 2)                   AS revenue,
       ROUND(revenue / customers, 2)       AS revenue_per_customer
FROM by_country
ORDER BY rank
LIMIT 10
""")

countries

moving_avg = q("""
WITH monthly AS (
    SELECT strftime('%Y-%m', InvoiceDate) AS month, SUM(Total) AS revenue
    FROM Invoice
    GROUP BY month
)
SELECT month,
       ROUND(revenue, 2) AS revenue,
       ROUND(AVG(revenue) OVER (ORDER BY month
                                ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2) AS moving_avg_3m,
       ROUND(SUM(revenue) OVER (PARTITION BY substr(month, 1, 4)
                                ORDER BY month), 2)                           AS year_to_date
FROM monthly
ORDER BY month
""")

fig, ax = plt.subplots(figsize=(9, 3.8))
x = pd.to_datetime(moving_avg.month)
ax.plot(x, moving_avg.revenue, color=GREY, marker="o", ms=3, lw=1, label="Monthly revenue")
ax.plot(x, moving_avg.moving_avg_3m, color=AMBER, lw=2.2, label="3-month moving average")
ax.set_ylim(0, moving_avg.revenue.max() * 1.25)
ax.set_ylabel("USD")
ax.set_title("Monthly revenue")
ax.legend(frameon=False, loc="lower right")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

team = q("""
WITH RECURSIVE org AS (                      -- walk the reporting line from the top down
    SELECT EmployeeId, FirstName || ' ' || LastName AS name, Title,
           0 AS depth,
           FirstName || ' ' || LastName            AS path
    FROM Employee
    WHERE ReportsTo IS NULL
    UNION ALL
    SELECT e.EmployeeId, e.FirstName || ' ' || e.LastName, e.Title,
           o.depth + 1,
           o.path || ' > ' || e.FirstName || ' ' || e.LastName
    FROM Employee e
    JOIN org o ON e.ReportsTo = o.EmployeeId
),
rep_revenue AS (
    SELECT c.SupportRepId AS EmployeeId, SUM(i.Total) AS revenue
    FROM Customer c
    JOIN Invoice  i USING (CustomerId)
    GROUP BY c.SupportRepId
)
SELECT substr('— — — ', 1, o.depth * 2) || o.name AS employee,
       o.Title,
       ROUND(COALESCE(rr.revenue, 0), 2)            AS own_revenue,
       ROUND((SELECT SUM(COALESCE(r2.revenue, 0))   -- everyone at or below this person
              FROM org o2
              LEFT JOIN rep_revenue r2 USING (EmployeeId)
              WHERE o2.path LIKE o.path || '%'), 2) AS team_revenue
FROM org o
LEFT JOIN rep_revenue rr USING (EmployeeId)
ORDER BY o.path
""")

team

cohorts = q("""
WITH first_year AS (
    SELECT CustomerId, MIN(CAST(strftime('%Y', InvoiceDate) AS INT)) AS cohort
    FROM Invoice
    GROUP BY CustomerId
),
activity AS (
    SELECT DISTINCT CustomerId, CAST(strftime('%Y', InvoiceDate) AS INT) AS year
    FROM Invoice
),
cohort_size AS (
    SELECT cohort, COUNT(*) AS size FROM first_year GROUP BY cohort
)
SELECT f.cohort,
       cs.size                                   AS customers,
       a.year - f.cohort                         AS years_since_first_purchase,
       COUNT(*)                                  AS active_customers,
       ROUND(100.0 * COUNT(*) / cs.size, 0)      AS retention_pct
FROM first_year f
JOIN activity    a  USING (CustomerId)
JOIN cohort_size cs ON cs.cohort = f.cohort
GROUP BY f.cohort, years_since_first_purchase
ORDER BY f.cohort, years_since_first_purchase
""")

matrix = cohorts.pivot(index="cohort", columns="years_since_first_purchase", values="retention_pct")
sizes = cohorts.drop_duplicates("cohort").set_index("cohort").customers
fig, ax = plt.subplots(figsize=(7.5, 3.6))
ax.imshow(matrix.values, cmap="Oranges", vmin=0, vmax=130, aspect="auto")
ax.set_xticks(range(matrix.shape[1]), [f"Year {c}" for c in matrix.columns])
ax.set_yticks(range(matrix.shape[0]), [f"{c} (n={sizes[c]})" for c in matrix.index])
for i in range(matrix.shape[0]):
    for j in range(matrix.shape[1]):
        if not np.isnan(matrix.values[i, j]):
            ax.text(j, i, f"{matrix.values[i, j]:.0f}%", ha="center", va="center", fontsize=9)
ax.grid(False)
ax.set_title("Share of each first-purchase cohort still buying")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

rfm = q("""
WITH today AS (SELECT MAX(InvoiceDate) AS d FROM Invoice),
base AS (
    SELECT CustomerId,
           CAST(julianday((SELECT d FROM today)) - julianday(MAX(InvoiceDate)) AS INT) AS recency_days,
           COUNT(*)   AS frequency,
           SUM(Total) AS monetary
    FROM Invoice
    GROUP BY CustomerId
),
scored AS (                                  -- 3 = best, 1 = worst on each dimension
    SELECT *,
           NTILE(3) OVER (ORDER BY recency_days DESC) AS r,
           NTILE(3) OVER (ORDER BY frequency)         AS f,
           NTILE(3) OVER (ORDER BY monetary)          AS m
    FROM base
)
SELECT CASE WHEN r = 3 AND m = 3 THEN 'Champions'
            WHEN r = 3             THEN 'Active'
            WHEN r = 1 AND m >= 2  THEN 'At risk (high value)'
            WHEN r = 1             THEN 'Lapsed'
            ELSE                        'Regular' END       AS segment,
       COUNT(*)                          AS customers,
       ROUND(SUM(monetary), 2)           AS revenue,
       ROUND(AVG(recency_days))          AS avg_days_since_last_order
FROM scored
GROUP BY segment
ORDER BY revenue DESC
""")

fig, ax = plt.subplots(figsize=(8, 3.6))
ax.barh(rfm.segment[::-1], rfm.revenue[::-1], color=BLUE)
for y, (rev, n) in enumerate(zip(rfm.revenue[::-1], rfm.customers[::-1])):
    ax.text(rev + 8, y, f"${rev:,.0f}  ({n} customers)", va="center", fontsize=9)
ax.set_xlim(0, rfm.revenue.max() * 1.35)
ax.set_title("Lifetime revenue by RFM segment")
ax.set_xlabel("USD")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
rfm

never_sold = q("""
SELECT ge.Name                                              AS genre,
       COUNT(*)                                             AS tracks,
       SUM(sold.TrackId IS NULL)                            AS never_sold,
       ROUND(100.0 * SUM(sold.TrackId IS NULL) / COUNT(*), 1) AS never_sold_pct
FROM Track t
JOIN Genre ge USING (GenreId)
LEFT JOIN (SELECT DISTINCT TrackId FROM InvoiceLine) sold USING (TrackId)   -- anti-join
GROUP BY ge.GenreId
HAVING tracks >= 20
ORDER BY never_sold_pct DESC
""")

fig, ax = plt.subplots(figsize=(8, 4.8))
ax.barh(never_sold.genre[::-1], never_sold.never_sold_pct[::-1], color=AMBER)
ax.set_xlabel("% of the genre's tracks never sold (genres with 20+ tracks)")
ax.set_title("Unsold catalogue by genre")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

basket = q("""
WITH invoice_genres AS (
    SELECT DISTINCT il.InvoiceId, t.GenreId
    FROM InvoiceLine il
    JOIN Track t USING (TrackId)
),
total AS (SELECT COUNT(DISTINCT InvoiceId) AS n FROM invoice_genres),
single AS (SELECT GenreId, COUNT(*) AS invoices FROM invoice_genres GROUP BY GenreId),
pairs AS (                                   -- self-join: two genres on the same invoice
    SELECT a.GenreId AS g1, b.GenreId AS g2, COUNT(*) AS together
    FROM invoice_genres a
    JOIN invoice_genres b ON a.InvoiceId = b.InvoiceId AND a.GenreId < b.GenreId
    GROUP BY a.GenreId, b.GenreId
)
SELECT ga.Name AS genre_a, gb.Name AS genre_b, p.together,
       ROUND(1.0 * p.together * t.n / (sa.invoices * sb.invoices), 2) AS lift
FROM pairs p
CROSS JOIN total t
JOIN single sa ON sa.GenreId = p.g1
JOIN single sb ON sb.GenreId = p.g2
JOIN Genre  ga ON ga.GenreId = p.g1
JOIN Genre  gb ON gb.GenreId = p.g2
WHERE p.together >= 20
ORDER BY p.together DESC
LIMIT 10
""")

basket
