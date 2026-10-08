"""The free library: 80,000 books from Project Gutenberg, in plain SQL.

Plain-script version of the notebook. Set GUTENBERG_DB to the path of gutenberg.db.
"""


import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DB_PATH = Path(os.environ.get("GUTENBERG_DB", "gutenberg.db"))
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

growth = q("""
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
ORDER BY year
""")

fig, ax = plt.subplots(figsize=(10, 4))
ax.bar(growth.year, growth.added / 1000, color=AMBER)
ax.set_ylabel("Books added in the year (thousand)")
ax2 = ax.twinx()
ax2.plot(growth.year, growth.total_so_far / 1000, color=BLUE, lw=2.2)
ax2.set_ylabel("Library size (thousand)")
ax2.grid(False)
ax2.spines["right"].set_visible(True)
ax.set_title("Additions per year (bars) and the size of the library (line)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
growth.sort_values("added", ascending=False).head(5)

languages = q("""
SELECT l.language,
       COUNT(*)                                           AS books,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS share_pct,
       MIN(CAST(substr(b.issued, 1, 4) AS INT))           AS first_added
FROM book_languages l
JOIN books b USING (book_id)
GROUP BY l.language
ORDER BY books DESC
LIMIT 12
""")

fig, ax = plt.subplots(figsize=(8.5, 4.4))
ax.barh(languages.language[::-1], languages.books[::-1], color=BLUE)
for y, (v, s) in enumerate(zip(languages.books[::-1], languages.share_pct[::-1])):
    ax.text(v + 400, y, f"{v:,}  ({s}%)", va="center", fontsize=8)
ax.set_xlim(0, languages.books.max() * 1.25)
ax.set_title("Books by language (language codes)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
languages.head(6)

mix = q("""
SELECT (CAST(substr(b.issued, 1, 4) AS INT) / 5) * 5      AS period_from,
       COUNT(*)                                            AS book_languages,
       ROUND(100.0 * AVG(l.language <> 'en'), 1)           AS non_english_pct
FROM books b
JOIN book_languages l USING (book_id)
WHERE b.type = 'Text'
GROUP BY period_from
ORDER BY period_from
""")

fig, ax = plt.subplots(figsize=(9, 3.9))
ax.bar(mix.period_from.astype(str), mix.non_english_pct, color=AMBER)
for x, v in enumerate(mix.non_english_pct):
    ax.text(x, v + 0.6, f"{v:.0f}%", ha="center", fontsize=8)
ax.set_ylabel("% of text books not in English")
ax.set_xlabel("Five-year period of addition")
ax.set_title("The share of non-English books over time")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
mix

authors = q("""
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
LIMIT 15
""")

fig, ax = plt.subplots(figsize=(9, 5))
ax.barh(authors.name[::-1], authors.books[::-1], color=AMBER)
for y, v in enumerate(authors.books[::-1]):
    ax.text(v + 3, y, str(v), va="center", fontsize=8)
ax.set_xlim(0, authors.books.max() * 1.1)
ax.set_title("Authors with the most books in the library")
ax.tick_params(axis="y", labelsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
authors[["name", "books", "birth_year", "death_year"]].head(6)

births = q("""
SELECT (a.birth_year / 10) * 10     AS birth_decade,
       COUNT(DISTINCT a.author_id)  AS authors,
       COUNT(*)                     AS books
FROM authors a
JOIN book_authors ba USING (author_id)
WHERE a.birth_year BETWEEN 1500 AND 1960
GROUP BY birth_decade
ORDER BY birth_decade
""")

fig, ax = plt.subplots(figsize=(10.5, 4))
ax.bar(births.birth_decade, births.books, width=8, color=AMBER, label="Books")
ax.set_ylabel("Books")
ax.set_xlabel("Decade of the author's birth")
ax2 = ax.twinx()
ax2.plot(births.birth_decade, births.authors, color=BLUE, lw=2, label="Authors")
ax2.set_ylabel("Authors")
ax2.set_ylim(0, None)
ax2.grid(False)
ax2.spines["right"].set_visible(True)
ax.set_title("Most of the library was written by authors born between about 1800 and 1890")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
births.sort_values("books", ascending=False).head(5)

lifespan = q("""
SELECT (birth_year / 50) * 50                                         AS born_from,
       COUNT(*)                                                       AS authors,
       ROUND(AVG(death_year - birth_year), 1)                         AS avg_lifespan,
       ROUND(100.0 * AVG(death_year - birth_year >= 80), 1)           AS lived_to_80_pct
FROM authors
WHERE birth_year BETWEEN 1500 AND 1899
  AND death_year IS NOT NULL
  AND death_year - birth_year BETWEEN 15 AND 110
GROUP BY born_from
ORDER BY born_from
""")

fig, ax = plt.subplots(figsize=(8.5, 3.9))
ax.bar(lifespan.born_from.astype(str), lifespan.avg_lifespan, color=BLUE)
for x, (v, n) in enumerate(zip(lifespan.avg_lifespan, lifespan.authors)):
    ax.text(x, v + 0.5, f"{v:.0f}\n(n={n:,})", ha="center", fontsize=8)
ax.set_ylim(40, lifespan.avg_lifespan.max() + 9)
ax.set_xlabel("Born in the half-century from")
ax.set_ylabel("Average lifespan (years)")
ax.set_title("Average lifespan of authors by birth period")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
lifespan

subjects = q("""
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
LIMIT 12
""")

subjects

title_words = q("""
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
LIMIT 20
""")

fig, ax = plt.subplots(figsize=(8.5, 5.2))
ax.barh(title_words.word[::-1], title_words.titles[::-1], color=AMBER)
for y, v in enumerate(title_words.titles[::-1]):
    ax.text(v + 20, y, f"{v:,}", va="center", fontsize=8)
ax.set_xlim(0, title_words.titles.max() * 1.12)
ax.set_title("The most common words in English book titles")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
