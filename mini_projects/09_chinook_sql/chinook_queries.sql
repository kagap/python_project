-- A digital music store in plain SQL (SQLite dialect)
-- Run against Chinook_Sqlite.sqlite from https://github.com/lerocha/chinook-database/releases

-- 1. Revenue per year with year-on-year growth (CTE + LAG)
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
ORDER BY year;


-- 2. Top artists by revenue (four-table JOIN)
SELECT ar.Name                                   AS artist,
       ROUND(SUM(il.UnitPrice * il.Quantity), 2) AS revenue,
       COUNT(DISTINCT t.TrackId)                 AS tracks_sold
FROM InvoiceLine il
JOIN Track  t  USING (TrackId)
JOIN Album  al USING (AlbumId)
JOIN Artist ar USING (ArtistId)
GROUP BY ar.ArtistId
ORDER BY revenue DESC
LIMIT 10;


-- 3. Genre share and cumulative share (SUM OVER)
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
LIMIT 10;


-- 4. Countries ranked by revenue (RANK)
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
LIMIT 10;


-- 5. Monthly revenue, 3-month moving average, year-to-date (window frame)
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
ORDER BY month;


-- 6. Sales team hierarchy and revenue roll-up (recursive CTE)
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
ORDER BY o.path;


-- 7. Customer cohort retention (CTEs)
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
ORDER BY f.cohort, years_since_first_purchase;


-- 8. RFM customer segmentation (NTILE + CASE)
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
ORDER BY revenue DESC;


-- 9. Share of the catalogue that never sold (anti-join)
SELECT ge.Name                                              AS genre,
       COUNT(*)                                             AS tracks,
       SUM(sold.TrackId IS NULL)                            AS never_sold,
       ROUND(100.0 * SUM(sold.TrackId IS NULL) / COUNT(*), 1) AS never_sold_pct
FROM Track t
JOIN Genre ge USING (GenreId)
LEFT JOIN (SELECT DISTINCT TrackId FROM InvoiceLine) sold USING (TrackId)   -- anti-join
GROUP BY ge.GenreId
HAVING tracks >= 20
ORDER BY never_sold_pct DESC;


-- 10. Genres bought together, with lift (self-join)
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
LIMIT 10;
