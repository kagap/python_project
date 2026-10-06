# 100,000 movie ratings, in plain SQL

7 business questions answered with SQL only (joins, CTEs, window functions and more), on real data from GroupLens Research (MovieLens latest small, research use, not redistributed).
Python only runs the queries and draws the charts.

- [`movielens_queries.sql`](movielens_queries.sql): every query, runnable in any SQLite client
- [`movielens.py`](movielens.py): the same queries plus the charts, as a script
- [`build_db.py`](build_db.py): downloads the data and builds the SQLite database (about 1 MB download)
- [Rendered notebook with results and takeaways](https://kagap.github.io/notebooks/movielens.html)

The data and the database are not stored in this repo; the build script fetches them.

## Run it

```bash
pip install -r ../requirements.txt
python build_db.py --out movielens.db
MOVIELENS_DB=movielens.db python movielens.py
```
