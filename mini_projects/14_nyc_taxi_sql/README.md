# A month of New York yellow cabs, in plain SQL

8 business questions answered with SQL only (joins, CTEs, window functions and more), on real data from the NYC Taxi & Limousine Commission trip records (January 2023).
Python only runs the queries and draws the charts.

- [`nyc_taxi_queries.sql`](nyc_taxi_queries.sql): every query, runnable in any SQLite client
- [`nyc_taxi.py`](nyc_taxi.py): the same queries plus the charts, as a script
- [`build_db.py`](build_db.py): downloads the data and builds the SQLite database (about 48 MB download, 3 million trips, builds a 300 MB database; needs pyarrow)
- [Rendered notebook with results and takeaways](https://kagap.github.io/notebooks/nyc_taxi.html)

The data and the database are not stored in this repo; the build script fetches them.

## Run it

```bash
pip install -r ../requirements.txt
python build_db.py --out taxi.db
TAXI_DB=taxi.db python nyc_taxi.py
```
