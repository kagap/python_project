# Jobs and prices across Europe, in plain SQL

8 business questions answered with SQL only (joins, CTEs, window functions and more), on real data from Eurostat (monthly unemployment and HICP inflation series).
Python only runs the queries and draws the charts.

- [`eurostat_europe_queries.sql`](eurostat_europe_queries.sql): every query, runnable in any SQLite client
- [`eurostat_europe.py`](eurostat_europe.py): the same queries plus the charts, as a script
- [`build_db.py`](build_db.py): downloads the data and builds the SQLite database (about 3 MB download)
- [Rendered notebook with results and takeaways](https://kagap.github.io/notebooks/eurostat_europe.html)

The data and the database are not stored in this repo; the build script fetches them.

## Run it

```bash
pip install -r ../requirements.txt
python build_db.py --out eurostat.db
EUROSTAT_DB=eurostat.db python eurostat_europe.py
```
