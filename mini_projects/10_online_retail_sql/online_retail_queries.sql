-- Two years of an online gift shop, in plain SQL (SQLite dialect)
-- Run against retail.db: https://archive.ics.uci.edu/dataset/502/online+retail+ii

-- 1. How do sales and returns develop month by month?
WITH monthly AS (
    SELECT strftime('%Y-%m', invoice_date)                                         AS month,
           COUNT(DISTINCT CASE WHEN invoice NOT LIKE 'C%' THEN invoice END)        AS orders,
           SUM(CASE WHEN invoice NOT LIKE 'C%' THEN quantity * price END)          AS sales,
           -SUM(CASE WHEN invoice LIKE 'C%'    THEN quantity * price END)          AS returned
    FROM invoice_lines
    WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9]*' AND invoice_date < '2011-12-01'
    GROUP BY month
)
SELECT month, orders,
       ROUND(sales)                                    AS sales,
       ROUND(returned)                                 AS returned,
       ROUND(100.0 * returned / sales, 1)              AS returned_pct,
       ROUND(100.0 * (sales - LAG(sales) OVER (ORDER BY month))
                   / LAG(sales) OVER (ORDER BY month), 1) AS growth_pct
FROM monthly
ORDER BY month;


-- 2. Which products bring in the most money?
WITH names AS (
    SELECT stock_code, description
    FROM (SELECT stock_code, description,
                 ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY COUNT(*) DESC) AS rn
          FROM invoice_lines
          WHERE description IS NOT NULL
          GROUP BY stock_code, description)
    WHERE rn = 1
)
SELECT l.stock_code,
       n.description                       AS product,
       SUM(l.quantity)                     AS net_units,
       COUNT(DISTINCT CASE WHEN l.invoice NOT LIKE 'C%' THEN l.invoice END) AS orders,
       ROUND(SUM(l.quantity * l.price))    AS net_revenue
FROM invoice_lines l
JOIN names n USING (stock_code)
WHERE l.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9]*'
GROUP BY l.stock_code
ORDER BY net_revenue DESC
LIMIT 10;


-- 3. How much does the business depend on the UK?
WITH by_country AS (
    SELECT country,
           COUNT(DISTINCT customer_id)  AS customers,
           COUNT(DISTINCT invoice)      AS orders,
           SUM(quantity * price)        AS revenue
    FROM invoice_lines
    WHERE invoice NOT LIKE 'C%' AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9]*'
    GROUP BY country
)
SELECT RANK() OVER (ORDER BY revenue DESC)              AS rank,
       country, customers, orders,
       ROUND(revenue)                                   AS revenue,
       ROUND(100.0 * revenue / SUM(revenue) OVER (), 1) AS share_pct,
       ROUND(revenue / orders)                          AS avg_order_value
FROM by_country
ORDER BY revenue DESC
LIMIT 10;


-- 4. Do a few customers pay the bills?
WITH spend AS (
    SELECT customer_id, SUM(quantity * price) AS lifetime
    FROM invoice_lines
    WHERE customer_id IS NOT NULL AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9]*'      -- returns are netted off here
    GROUP BY customer_id
    HAVING lifetime > 0
),
bucketed AS (
    SELECT *, NTILE(10) OVER (ORDER BY lifetime DESC) AS decile FROM spend
)
SELECT decile,
       COUNT(*)                                                         AS customers,
       ROUND(SUM(lifetime))                                             AS revenue,
       ROUND(100.0 * SUM(lifetime) / SUM(SUM(lifetime)) OVER (), 1)     AS share_pct,
       ROUND(100.0 * SUM(SUM(lifetime)) OVER (ORDER BY decile)
                   / SUM(SUM(lifetime)) OVER (), 1)                     AS cumulative_pct
FROM bucketed
GROUP BY decile
ORDER BY decile;


-- 5. Which customers need a phone call? RFM segmentation
WITH today AS (SELECT date(MAX(invoice_date), '+1 day') AS d FROM invoice_lines),
customers AS (
    SELECT customer_id,
           CAST(julianday((SELECT d FROM today)) - julianday(MAX(invoice_date)) AS INT) AS recency_days,
           COUNT(DISTINCT CASE WHEN invoice NOT LIKE 'C%' THEN invoice END)              AS orders,
           SUM(quantity * price)                                                         AS monetary
    FROM invoice_lines
    WHERE customer_id IS NOT NULL AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9]*'
    GROUP BY customer_id
    HAVING monetary > 0
),
scored AS (
    SELECT *,
           NTILE(4) OVER (ORDER BY recency_days DESC) AS r,        -- 4 = bought most recently
           CASE WHEN orders >= 5 THEN 3 WHEN orders >= 2 THEN 2 ELSE 1 END AS f,
           NTILE(4) OVER (ORDER BY monetary)          AS m
    FROM customers
)
SELECT CASE WHEN r = 4 AND f = 3 THEN 'Champions'
            WHEN f = 3           THEN 'Loyal'
            WHEN r >= 3 AND f = 1 THEN 'New or recent'
            WHEN r <= 2 AND f >= 2 THEN 'At risk'
            WHEN r <= 2          THEN 'Lost'
            ELSE                      'Regular' END                AS segment,
       COUNT(*)                                                    AS customers,
       ROUND(SUM(monetary))                                        AS revenue,
       ROUND(100.0 * SUM(monetary) / SUM(SUM(monetary)) OVER (), 1) AS revenue_share_pct,
       ROUND(AVG(recency_days))                                    AS avg_days_since_last_order,
       ROUND(AVG(orders), 1)                                       AS avg_orders
FROM scored
GROUP BY segment
ORDER BY revenue DESC;


-- 6. Do customers keep coming back? Cohort retention
WITH purchases AS (
    SELECT DISTINCT customer_id, strftime('%Y-%m', invoice_date) AS month
    FROM invoice_lines
    WHERE customer_id IS NOT NULL AND invoice NOT LIKE 'C%' AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9]*'
      AND invoice_date < '2011-12-01'          -- December 2011 is incomplete
),
first_month AS (
    SELECT customer_id, MIN(month) AS cohort FROM purchases GROUP BY customer_id
),
cohort_size AS (
    SELECT cohort, COUNT(*) AS size FROM first_month GROUP BY cohort
)
SELECT f.cohort,
       cs.size                                    AS customers,
       (CAST(substr(p.month, 1, 4) AS INT) - CAST(substr(f.cohort, 1, 4) AS INT)) * 12
         + CAST(substr(p.month, 6, 2) AS INT) - CAST(substr(f.cohort, 6, 2) AS INT) AS months_since,
       COUNT(*)                                   AS active_customers,
       ROUND(100.0 * COUNT(*) / cs.size, 1)       AS retention_pct
FROM first_month f
JOIN purchases   p  USING (customer_id)
JOIN cohort_size cs ON cs.cohort = f.cohort
WHERE f.cohort >= '2010-01'
GROUP BY f.cohort, months_since
ORDER BY f.cohort, months_since;


-- 7. Which products are bought together?
WITH names AS (
    SELECT stock_code, description
    FROM (SELECT stock_code, description,
                 ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY COUNT(*) DESC) AS rn
          FROM invoice_lines
          WHERE description IS NOT NULL
          GROUP BY stock_code, description)
    WHERE rn = 1
),
top_products AS (
    SELECT stock_code
    FROM invoice_lines
    WHERE invoice NOT LIKE 'C%' AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9]*'
    GROUP BY stock_code
    ORDER BY COUNT(DISTINCT invoice) DESC
    LIMIT 60
),
baskets AS (
    SELECT DISTINCT invoice, stock_code
    FROM invoice_lines
    WHERE invoice NOT LIKE 'C%' AND stock_code IN (SELECT stock_code FROM top_products)
),
total AS (SELECT COUNT(DISTINCT invoice) AS n FROM invoice_lines WHERE invoice NOT LIKE 'C%'),
single AS (SELECT stock_code, COUNT(*) AS invoices FROM baskets GROUP BY stock_code),
pairs AS (
    SELECT a.stock_code AS a_code, b.stock_code AS b_code, COUNT(*) AS together
    FROM baskets a
    JOIN baskets b ON a.invoice = b.invoice AND a.stock_code < b.stock_code
    GROUP BY a.stock_code, b.stock_code
)
SELECT na.description                                              AS product_a,
       nb.description                                              AS product_b,
       p.together                                                  AS orders_together,
       ROUND(1.0 * p.together * t.n / (sa.invoices * sb.invoices), 1) AS lift
FROM pairs p
CROSS JOIN total t
JOIN names na ON na.stock_code = p.a_code
JOIN names nb ON nb.stock_code = p.b_code
JOIN single sa ON sa.stock_code = p.a_code
JOIN single sb ON sb.stock_code = p.b_code
WHERE p.together >= 200
ORDER BY lift DESC
LIMIT 10;


-- 8. How long do customers wait between orders?
WITH orders AS (
    SELECT customer_id, invoice, MIN(date(invoice_date)) AS order_date
    FROM invoice_lines
    WHERE customer_id IS NOT NULL AND invoice NOT LIKE 'C%' AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9]*'
    GROUP BY customer_id, invoice
),
gaps AS (
    SELECT julianday(order_date)
             - julianday(LAG(order_date) OVER (PARTITION BY customer_id ORDER BY order_date)) AS gap_days
    FROM orders
)
SELECT CASE WHEN gap_days = 0   THEN '0 same day'
            WHEN gap_days <= 14 THEN '1-14 days'
            WHEN gap_days <= 30 THEN '15-30 days'
            WHEN gap_days <= 60 THEN '31-60 days'
            WHEN gap_days <= 120 THEN '61-120 days'
            ELSE                      '120+ days' END AS gap,
       COUNT(*)                                        AS orders,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS share_pct
FROM gaps
WHERE gap_days IS NOT NULL
GROUP BY gap
ORDER BY MIN(gap_days);
