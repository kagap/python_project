"""Inside New York's restaurant inspections, in plain SQL.

Plain-script version of the notebook. Set RESTAURANTS_DB to the path of restaurants.db.
"""


import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DB_PATH = Path(os.environ.get("RESTAURANTS_DB", "restaurants.db"))
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

grades = q("""
WITH insp AS (
    SELECT camis, inspection_date,
           MAX(dba)                          AS name,
           MAX(boro)                         AS boro,
           MAX(cuisine_description)          AS cuisine,
           MAX(inspection_type)              AS inspection_type,
           MAX(score)                        AS score,
           MAX(grade)                        AS grade,
           MAX(action)                       AS action,
           COUNT(violation_code)             AS violations,
           SUM(critical_flag = 'Critical')   AS critical_violations
    FROM inspections
    GROUP BY camis, inspection_date
)
SELECT boro                                              AS borough,
       COUNT(*)                                          AS graded_inspections,
       ROUND(100.0 * AVG(grade = 'A'), 1)                AS a_pct,
       ROUND(100.0 * AVG(grade = 'B'), 1)                AS b_pct,
       ROUND(100.0 * AVG(grade = 'C'), 1)                AS c_pct,
       ROUND(AVG(score), 1)                              AS avg_score
FROM insp
WHERE grade IN ('A', 'B', 'C') AND boro <> '0'
GROUP BY boro
ORDER BY a_pct DESC
""")

fig, ax = plt.subplots(figsize=(8.5, 3.9))
ax.barh(grades.borough[::-1], grades.a_pct[::-1], color="#38a169", label="A")
ax.barh(grades.borough[::-1], grades.b_pct[::-1], left=grades.a_pct[::-1], color=AMBER, label="B")
ax.barh(grades.borough[::-1], grades.c_pct[::-1], left=(grades.a_pct + grades.b_pct)[::-1], color="#c0392b", label="C")
for y, a in enumerate(grades.a_pct[::-1]):
    ax.text(a / 2, y, f"{a:.0f}%", ha="center", va="center", color="white", fontsize=9)
ax.set_xlim(0, 100)
ax.set_xlabel("% of graded inspections")
ax.set_title("Grades by borough")
ax.legend(frameon=False, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.18))
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
grades

violations = q("""
SELECT v.violation_code                                          AS code,
       substr(c.violation_description, 1, 80)                    AS description,
       MAX(v.critical_flag)                                      AS type,
       COUNT(DISTINCT v.camis || v.inspection_date)              AS inspections,
       ROUND(100.0 * COUNT(DISTINCT v.camis || v.inspection_date)
             / (SELECT COUNT(DISTINCT camis || inspection_date) FROM inspections), 1) AS pct_of_inspections
FROM inspections v
JOIN violation_codes c USING (violation_code)
GROUP BY v.violation_code
ORDER BY inspections DESC
LIMIT 10
""")

fig, ax = plt.subplots(figsize=(10, 4.8))
labels = violations.code + "  " + violations.description.str.slice(0, 55)
ax.barh(labels[::-1], violations.pct_of_inspections[::-1],
        color=[AMBER if t == "Critical" else GREY for t in violations.type[::-1]])
for y, v in enumerate(violations.pct_of_inspections[::-1]):
    ax.text(v + 0.3, y, f"{v:.0f}%", va="center", fontsize=8)
ax.set_xlim(0, violations.pct_of_inspections.max() * 1.12)
ax.set_title("Most cited violations (% of inspections; amber = critical)")
ax.tick_params(axis="y", labelsize=7)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
violations[["code", "type", "inspections"]]

cuisines = q("""
WITH insp AS (
    SELECT camis, inspection_date,
           MAX(dba)                          AS name,
           MAX(boro)                         AS boro,
           MAX(cuisine_description)          AS cuisine,
           MAX(inspection_type)              AS inspection_type,
           MAX(score)                        AS score,
           MAX(grade)                        AS grade,
           MAX(action)                       AS action,
           COUNT(violation_code)             AS violations,
           SUM(critical_flag = 'Critical')   AS critical_violations
    FROM inspections
    GROUP BY camis, inspection_date
),
scored AS (
    SELECT cuisine, score, critical_violations
    FROM insp
    WHERE score IS NOT NULL AND cuisine IS NOT NULL
),
ranked AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY cuisine ORDER BY score) AS rn,
           COUNT(*)     OVER (PARTITION BY cuisine)                AS n
    FROM scored
)
SELECT cuisine,
       MAX(n)                                                        AS inspections,
       MAX(CASE WHEN rn = (n + 1) / 2 THEN score END)                AS median_score,
       ROUND(AVG(score), 1)                                          AS avg_score,
       ROUND(AVG(critical_violations), 1)                            AS avg_critical_violations
FROM ranked
GROUP BY cuisine
HAVING inspections >= 400
ORDER BY median_score DESC, avg_score DESC
""")

d = pd.concat([cuisines.head(10), cuisines.tail(10)])
fig, ax = plt.subplots(figsize=(9, 6))
colors = [AMBER] * 10 + [BLUE] * 10
ax.barh(d.cuisine[::-1], d.median_score[::-1], color=colors[::-1])
for y, v in enumerate(d.median_score[::-1]):
    ax.text(v + 0.3, y, f"{v:g}", va="center", fontsize=8)
ax.set_xlabel("Median inspection score (lower is better)")
ax.set_title("Cuisines with the highest and lowest median scores (400+ inspections)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
cuisines.shape

monthly = q("""
WITH insp AS (
    SELECT camis, inspection_date,
           MAX(dba)                          AS name,
           MAX(boro)                         AS boro,
           MAX(cuisine_description)          AS cuisine,
           MAX(inspection_type)              AS inspection_type,
           MAX(score)                        AS score,
           MAX(grade)                        AS grade,
           MAX(action)                       AS action,
           COUNT(violation_code)             AS violations,
           SUM(critical_flag = 'Critical')   AS critical_violations
    FROM inspections
    GROUP BY camis, inspection_date
),
monthly AS (
    SELECT substr(inspection_date, 1, 7)           AS month,
           COUNT(*)                                AS inspections,
           ROUND(AVG(score), 1)                    AS avg_score,
           ROUND(100.0 * AVG(grade = 'A'), 1)      AS a_grade_pct
    FROM insp
    WHERE inspection_type LIKE 'Cycle Inspection%' AND score IS NOT NULL
    GROUP BY month
)
SELECT month, inspections, avg_score, a_grade_pct,
       ROUND(avg_score - LAG(avg_score) OVER (ORDER BY month), 1) AS change_vs_prev_month
FROM monthly
ORDER BY month
""")

fig, ax = plt.subplots(figsize=(10, 4))
ax.bar(monthly.month, monthly.inspections / 1000, color=GREY)
ax.set_ylabel("Cycle inspections (thousand)")
ax.tick_params(axis="x", rotation=60)
ax2 = ax.twinx()
ax2.plot(monthly.month, monthly.avg_score, color=AMBER, marker="o", lw=2)
ax2.set_ylabel("Average score (lower is better)")
ax2.set_ylim(0, monthly.avg_score.max() * 1.3)
ax2.grid(False)
ax2.spines["right"].set_visible(True)
ax.set_title("Inspections (bars) and average score (line) per month")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
monthly.tail(6)

chains = q("""
WITH insp AS (
    SELECT camis, inspection_date,
           MAX(dba)                          AS name,
           MAX(boro)                         AS boro,
           MAX(cuisine_description)          AS cuisine,
           MAX(inspection_type)              AS inspection_type,
           MAX(score)                        AS score,
           MAX(grade)                        AS grade,
           MAX(action)                       AS action,
           COUNT(violation_code)             AS violations,
           SUM(critical_flag = 'Critical')   AS critical_violations
    FROM inspections
    GROUP BY camis, inspection_date
),
chains AS (
    SELECT name,
           COUNT(DISTINCT camis)                   AS locations,
           COUNT(*)                                AS inspections,
           ROUND(AVG(score), 1)                    AS avg_score,
           ROUND(100.0 * AVG(grade = 'A'), 1)      AS a_grade_pct
    FROM insp
    WHERE score IS NOT NULL
    GROUP BY name
    HAVING locations >= 15
),
ranked AS (
    SELECT *,
           ROW_NUMBER() OVER (ORDER BY avg_score)      AS best_rank,
           ROW_NUMBER() OVER (ORDER BY avg_score DESC) AS worst_rank
    FROM chains
)
SELECT CASE WHEN best_rank <= 8 THEN 'Best' ELSE 'Worst' END AS group_,
       name, locations, inspections, avg_score, a_grade_pct
FROM ranked
WHERE best_rank <= 8 OR worst_rank <= 8
ORDER BY avg_score
""")

fig, ax = plt.subplots(figsize=(9, 6))
ax.barh(chains.name[::-1], chains.avg_score[::-1], color=[AMBER if g == "Worst" else BLUE for g in chains.group_[::-1]])
for y, (v, n) in enumerate(zip(chains.avg_score[::-1], chains.locations[::-1])):
    ax.text(v + 0.3, y, f"{v:.1f}  ({n} locations)", va="center", fontsize=8)
ax.set_xlim(0, chains.avg_score.max() * 1.3)
ax.set_title("Chains with the best (blue) and worst (amber) average inspection score")
ax.tick_params(axis="y", labelsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
chains

closures = q("""
WITH insp AS (
    SELECT camis, inspection_date,
           MAX(dba)                          AS name,
           MAX(boro)                         AS boro,
           MAX(cuisine_description)          AS cuisine,
           MAX(inspection_type)              AS inspection_type,
           MAX(score)                        AS score,
           MAX(grade)                        AS grade,
           MAX(action)                       AS action,
           COUNT(violation_code)             AS violations,
           SUM(critical_flag = 'Critical')   AS critical_violations
    FROM inspections
    GROUP BY camis, inspection_date
)
SELECT cuisine,
       COUNT(*)                                                           AS inspections,
       SUM(action LIKE 'Establishment Closed%')                           AS closures,
       ROUND(100.0 * AVG(action LIKE 'Establishment Closed%'), 1)         AS closure_pct
FROM insp
WHERE cuisine IS NOT NULL
GROUP BY cuisine
HAVING inspections >= 600
ORDER BY closure_pct DESC
LIMIT 10
""")

fig, ax = plt.subplots(figsize=(8.5, 4.4))
ax.barh(closures.cuisine[::-1], closures.closure_pct[::-1], color=AMBER)
for y, (v, n) in enumerate(zip(closures.closure_pct[::-1], closures.closures[::-1])):
    ax.text(v + 0.1, y, f"{v:.1f}%  ({n} closures)", va="center", fontsize=8)
ax.set_xlim(0, closures.closure_pct.max() * 1.3)
ax.set_title("Share of inspections that ended in a closure, by cuisine")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
closures

recovery = q("""
WITH insp AS (
    SELECT camis, inspection_date,
           MAX(dba)                          AS name,
           MAX(boro)                         AS boro,
           MAX(cuisine_description)          AS cuisine,
           MAX(inspection_type)              AS inspection_type,
           MAX(score)                        AS score,
           MAX(grade)                        AS grade,
           MAX(action)                       AS action,
           COUNT(violation_code)             AS violations,
           SUM(critical_flag = 'Critical')   AS critical_violations
    FROM inspections
    GROUP BY camis, inspection_date
),
sequence AS (
    SELECT camis, inspection_date, score,
           LEAD(score)           OVER (PARTITION BY camis ORDER BY inspection_date) AS next_score,
           LEAD(inspection_date) OVER (PARTITION BY camis ORDER BY inspection_date) AS next_date
    FROM insp
    WHERE score IS NOT NULL
)
SELECT CASE WHEN score >= 60 THEN '3  60 or more'
            WHEN score >= 40 THEN '2  40-59'
            ELSE                  '1  28-39' END                       AS first_score,
       COUNT(*)                                                       AS cases,
       ROUND(AVG(julianday(next_date) - julianday(inspection_date))) AS avg_days_to_next,
       ROUND(AVG(next_score), 1)                                      AS avg_next_score,
       ROUND(100.0 * AVG(next_score <= 13), 1)                        AS next_is_a_pct,
       ROUND(100.0 * AVG(next_score >= 28), 1)                        AS next_still_c_pct
FROM sequence
WHERE score >= 28 AND next_score IS NOT NULL
GROUP BY first_score
ORDER BY first_score
""")

fig, ax = plt.subplots(figsize=(8, 3.9))
x = np.arange(len(recovery))
ax.bar(x - 0.2, recovery.next_is_a_pct, width=0.4, color="#38a169", label="Next inspection is an A")
ax.bar(x + 0.2, recovery.next_still_c_pct, width=0.4, color="#c0392b", label="Still a C")
ax.set_xticks(x, recovery.first_score.str.slice(3))
ax.set_xlabel("Score at the failing inspection")
ax.set_ylabel("% of cases")
ax.set_title("Outcome of the next inspection after a score of 28 or more")
ax.legend(frameon=False)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
recovery
