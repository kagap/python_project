-- The free library: 80,000 books from Project Gutenberg, in plain SQL (SQLite dialect)
-- Run against gutenberg.db: https://www.gutenberg.org/

-- 1. How has the library grown?
WITH yearly AS (
    SELECT CAST(substr(issued, 1, 4) AS INT) AS year,
           COUNT(*)                          AS added,
           SUM(type = 'Text')                AS texts,
           SUM(type = 'Sound')               AS audiobooks
    FROM books
    GROUP BY year
)
SELECT year, added, texts, audiobooks,
       SUM(added) OVER (ORDER BY year)                                            AS total_so_far,
       ROUND(100.0 * (added - LAG(added) OVER (ORDER BY year)) / LAG(added) OVER (ORDER BY year), 0) AS change_pct
FROM yearly
ORDER BY year;


-- 2. Which languages does it hold?
SELECT l.language,
       COUNT(*)                                           AS books,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS share_pct,
       MIN(CAST(substr(b.issued, 1, 4) AS INT))           AS first_added
FROM book_languages l
JOIN books b USING (book_id)
GROUP BY l.language
ORDER BY books DESC
LIMIT 12;


-- 3. Is the library becoming less English?
SELECT (CAST(substr(b.issued, 1, 4) AS INT) / 5) * 5      AS period_from,
       COUNT(*)                                            AS book_languages,
       ROUND(100.0 * AVG(l.language <> 'en'), 1)           AS non_english_pct
FROM books b
JOIN book_languages l USING (book_id)
WHERE b.type = 'Text'
GROUP BY period_from
ORDER BY period_from;


-- 4. Who are the most prolific authors?
SELECT RANK() OVER (ORDER BY COUNT(*) DESC)               AS rank,
       a.name, a.birth_year, a.death_year,
       COUNT(*)                                            AS books,
       MIN(CAST(substr(b.issued, 1, 4) AS INT))            AS first_added,
       MAX(CAST(substr(b.issued, 1, 4) AS INT))            AS last_added
FROM book_authors ba
JOIN authors a USING (author_id)
JOIN books   b USING (book_id)
WHERE a.name NOT IN ('Various', 'Anonymous', 'Unknown') AND b.type = 'Text'
GROUP BY a.author_id
ORDER BY books DESC
LIMIT 15;


-- 5. When were the authors born?
SELECT (a.birth_year / 10) * 10     AS birth_decade,
       COUNT(DISTINCT a.author_id)  AS authors,
       COUNT(*)                     AS books
FROM authors a
JOIN book_authors ba USING (author_id)
WHERE a.birth_year BETWEEN 1500 AND 1960
GROUP BY birth_decade
ORDER BY birth_decade;


-- 6. Did authors live longer over the centuries?
SELECT (birth_year / 50) * 50                                         AS born_from,
       COUNT(*)                                                       AS authors,
       ROUND(AVG(death_year - birth_year), 1)                         AS avg_lifespan,
       ROUND(100.0 * AVG(death_year - birth_year >= 80), 1)           AS lived_to_80_pct
FROM authors
WHERE birth_year BETWEEN 1500 AND 1899
  AND death_year IS NOT NULL
  AND death_year - birth_year BETWEEN 15 AND 110
GROUP BY born_from
ORDER BY born_from;


-- 7. Which subjects go together?
WITH main AS (
    SELECT DISTINCT book_id,
           CASE WHEN instr(subject, ' -- ') > 0 THEN substr(subject, 1, instr(subject, ' -- ') - 1) ELSE subject END AS heading
    FROM book_subjects
),
top AS (SELECT heading FROM main GROUP BY heading ORDER BY COUNT(*) DESC LIMIT 40),
pool AS (SELECT * FROM main WHERE heading IN (SELECT heading FROM top)),
total AS (SELECT COUNT(DISTINCT book_id) AS n FROM main),
single AS (SELECT heading, COUNT(*) AS c FROM pool GROUP BY heading),
pairs AS (
    SELECT x.heading AS a, y.heading AS b, COUNT(*) AS together
    FROM pool x
    JOIN pool y ON x.book_id = y.book_id AND x.heading < y.heading
    GROUP BY x.heading, y.heading
)
SELECT p.a AS heading_a, p.b AS heading_b, p.together AS books_together,
       ROUND(1.0 * p.together * t.n / (sa.c * sb.c), 1) AS lift
FROM pairs p
CROSS JOIN total t
JOIN single sa ON sa.heading = p.a
JOIN single sb ON sb.heading = p.b
WHERE p.together >= 40
ORDER BY lift DESC
LIMIT 12;


-- 8. What do the titles say? A recursive split
WITH RECURSIVE words(book_id, word, rest) AS (
    SELECT b.book_id, '',
           lower(replace(replace(replace(replace(replace(replace(replace(replace(replace(b.title, ',', ' '), ';', ' '), ':', ' '), '.', ' '), '-', ' '), char(10), ' '), '(', ' '), ')', ' '), '"', ' ')) || ' '
    FROM books b
    JOIN book_languages l USING (book_id)
    WHERE l.language = 'en' AND b.type = 'Text'
    UNION ALL
    SELECT book_id,
           substr(rest, 1, instr(rest, ' ') - 1),
           ltrim(substr(rest, instr(rest, ' ') + 1))
    FROM words
    WHERE rest <> ''
)
SELECT word, COUNT(DISTINCT book_id) AS titles
FROM words
WHERE length(word) > 2
  AND word NOT IN ('the','and','for','with','from','his','her','their','its','are','was','not','but','you','who','all','how','new','one','volume','vol','part','being','which','there','their','them','book','other','into','out','than','that','this','these','those','has','had','have','been','will','our','your')
GROUP BY word
ORDER BY titles DESC
LIMIT 20;
