"""Two years of an online gift shop, in plain SQL.

Plain-script version of the notebook. Set RETAIL_DB to the path of retail.db.
"""


import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DB_PATH = Path(os.environ.get("RETAIL_DB", "retail.db"))
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

monthly = q("""
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
ORDER BY month
""")

fig, ax = plt.subplots(figsize=(10, 4))
ax.bar(monthly.month, monthly.sales / 1000, color=AMBER)
ax.set_ylabel("Sales (GBP thousand)")
ax.tick_params(axis="x", rotation=60)
ax2 = ax.twinx()
ax2.plot(monthly.month, monthly.returned_pct, color=BLUE, marker="o", ms=3)
ax2.set_ylabel("Returned value (% of sales)")
ax2.set_ylim(0, max(10, monthly.returned_pct.max() * 1.2))
ax2.grid(False)
ax2.spines["right"].set_visible(True)
ax.set_title("Monthly sales (bars) and share returned (line)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
monthly.tail(8)

products = q("""
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
LIMIT 10
""")

fig, ax = plt.subplots(figsize=(9, 4.4))
ax.barh(products["product"][::-1], products.net_revenue[::-1] / 1000, color=BLUE)
for y, v in enumerate(products.net_revenue[::-1] / 1000):
    ax.text(v + 1, y, f"{v:,.0f}k", va="center", fontsize=8)
ax.set_xlim(0, products.net_revenue.max() / 1000 * 1.15)
ax.set_title("Top 10 products by net revenue (GBP thousand)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
products

countries = q("""
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
LIMIT 10
""")

countries

deciles = q("""
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
ORDER BY decile
""")

fig, ax = plt.subplots(figsize=(8.5, 4))
ax.bar(deciles.decile.astype(str), deciles.share_pct, color=AMBER)
ax.set_xlabel("Customer decile (1 = biggest spenders)")
ax.set_ylabel("Share of revenue (%)")
ax2 = ax.twinx()
ax2.plot(deciles.decile.astype(str), deciles.cumulative_pct, color=BLUE, marker="o")
ax2.set_ylim(0, 105)
ax2.set_ylabel("Cumulative share (%)")
ax2.grid(False)
ax2.spines["right"].set_visible(True)
ax.set_title("Revenue by customer decile")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
deciles

rfm = q("""
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
ORDER BY revenue DESC
""")

fig, ax = plt.subplots(figsize=(8.5, 3.8))
ax.barh(rfm.segment[::-1], rfm.revenue[::-1] / 1000, color=BLUE)
for y, (v, n) in enumerate(zip(rfm.revenue[::-1] / 1000, rfm.customers[::-1])):
    ax.text(v + 20, y, f"{v:,.0f}k  ({n:,} customers)", va="center", fontsize=8)
ax.set_xlim(0, rfm.revenue.max() / 1000 * 1.35)
ax.set_title("Lifetime revenue by RFM segment (GBP thousand)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
rfm

cohorts = q("""
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
ORDER BY f.cohort, months_since
""")

grid = cohorts[cohorts.months_since <= 12].pivot(index="cohort", columns="months_since", values="retention_pct")
sizes = cohorts.drop_duplicates("cohort").set_index("cohort").customers
fig, ax = plt.subplots(figsize=(10, 6.2))
ax.imshow(grid.values, cmap="Oranges", vmin=0, vmax=60, aspect="auto")
ax.set_xticks(range(grid.shape[1]), [f"M{c}" for c in grid.columns])
ax.set_yticks(range(grid.shape[0]), [f"{c} (n={sizes[c]})" for c in grid.index], fontsize=8)
for i in range(grid.shape[0]):
    for j in range(grid.shape[1]):
        v = grid.values[i, j]
        if not np.isnan(v):
            ax.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=7, color="white" if v >= 90 else "black")
ax.grid(False)
ax.set_title("Share of each first-purchase cohort that buys again, by months since first purchase (%)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

basket = q("""
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
LIMIT 10
""")

basket

gaps = q("""
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
ORDER BY MIN(gap_days)
""")

fig, ax = plt.subplots(figsize=(8, 3.8))
ax.bar(gaps.gap, gaps.share_pct, color=AMBER)
for x, v in enumerate(gaps.share_pct):
    ax.text(x, v + 0.5, f"{v:.0f}%", ha="center", fontsize=9)
ax.set_ylim(0, gaps.share_pct.max() * 1.18)
ax.set_title("Time since the same customer's previous order")
ax.set_ylabel("% of repeat orders")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
gaps
