-- Inside New York's restaurant inspections, in plain SQL (SQLite dialect)
-- Run against restaurants.db: https://data.cityofnewyork.us/Health/DOHMH-New-York-City-Restaurant-Inspection-Results/43nn-pn8j

-- 1. How do the boroughs compare on grades?
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
ORDER BY a_pct DESC;


-- 2. What do inspectors find most often?
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
LIMIT 10;


-- 3. Which cuisines score best and worst?
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
ORDER BY median_score DESC, avg_score DESC;


-- 4. Is the picture changing month by month?
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
ORDER BY month;


-- 5. Which chains have the cleanest and the dirtiest records?
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
ORDER BY avg_score;


-- 6. Which cuisines get closed most often?
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
LIMIT 10;


-- 7. What happens at the next inspection after a bad one?
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
ORDER BY first_score;
