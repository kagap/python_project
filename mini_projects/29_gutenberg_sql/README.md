# 80,000 Free Books

The Project Gutenberg catalogue (79,533 books, 36,609 authors) in plain SQL, with SQL only (joins, CTEs, window functions, a recursive CTE and more), on the public-domain catalogue file.
Python only runs the queries and draws the charts.

- [`gutenberg_books_queries.sql`](gutenberg_books_queries.sql): every query, runnable in any SQLite client
- [`gutenberg_books.py`](gutenberg_books.py): the same queries plus the charts, as a script
- [`build_db.py`](build_db.py): downloads the data and builds the SQLite database (about 21 MB download)
- [Rendered notebook with results and takeaways](https://kagap.github.io/notebooks/gutenberg_books.html)

The data and the database are not stored in this repo; the build script fetches them.

## Run it

```bash
pip install -r ../requirements.txt
python build_db.py --out gutenberg.db
GUTENBERG_DB=gutenberg.db python gutenberg_books.py
```
