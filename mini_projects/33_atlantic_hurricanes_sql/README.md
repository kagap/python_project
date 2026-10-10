# 175 Years of Atlantic Hurricanes

175 years of Atlantic hurricanes (1,988 storms, 55,524 observations, 1851 to 2025) in plain SQL, with SQL only (moving-average frames, a self-join on time, gaps and islands and more), on NOAA's HURDAT2 best-track database.
Python only runs the queries and draws the charts.

- [`atlantic_hurricanes_queries.sql`](atlantic_hurricanes_queries.sql): every query, runnable in any SQLite client
- [`atlantic_hurricanes.py`](atlantic_hurricanes.py): the same queries plus the charts, as a script
- [`build_db.py`](build_db.py): downloads the data and builds the SQLite database (about 7 MB text file from the National Hurricane Center)
- [Rendered notebook with results and takeaways](https://kagap.github.io/notebooks/atlantic_hurricanes.html)

The data and the database are not stored in this repo; the build script fetches them.

## Run it

```bash
pip install -r ../requirements.txt
python build_db.py --out hurricanes.db
HURRICANE_DB=hurricanes.db python atlantic_hurricanes.py
```
