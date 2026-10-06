# Inside New York's restaurant inspections, in plain SQL

7 business questions answered with SQL only (joins, CTEs, window functions and more), on real data from NYC Open Data (DOHMH restaurant inspection results, January 2025 onwards).
Python only runs the queries and draws the charts.

- [`nyc_restaurants_queries.sql`](nyc_restaurants_queries.sql): every query, runnable in any SQLite client
- [`nyc_restaurants.py`](nyc_restaurants.py): the same queries plus the charts, as a script
- [`build_db.py`](build_db.py): downloads the data and builds the SQLite database (about 33 MB download, 163,000 rows)
- [Rendered notebook with results and takeaways](https://kagap.github.io/notebooks/nyc_restaurants.html)

The data and the database are not stored in this repo; the build script fetches them.

## Run it

```bash
pip install -r ../requirements.txt
python build_db.py --out restaurants.db
RESTAURANTS_DB=restaurants.db python nyc_restaurants.py
```
