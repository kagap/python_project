# What New Yorkers complain about, in plain SQL

7 business questions answered with SQL only (joins, CTEs, window functions and more), on real data from NYC Open Data, 311 service requests (sampled on the 1st and 15th of each month of 2023).
Python only runs the queries and draws the charts.

- [`nyc311_queries.sql`](nyc311_queries.sql): every query, runnable in any SQLite client
- [`nyc311.py`](nyc311.py): the same queries plus the charts, as a script
- [`build_db.py`](build_db.py): downloads the data and builds the SQLite database (about 30 MB download, 214,000 rows)
- [Rendered notebook with results and takeaways](https://kagap.github.io/notebooks/nyc311.html)

The data and the database are not stored in this repo; the build script fetches them.

## Run it

```bash
pip install -r ../requirements.txt
python build_db.py --out nyc311.db
NYC311_DB=nyc311.db python nyc311.py
```
