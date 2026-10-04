# A year of crime in Chicago, in plain SQL

9 business questions answered with SQL only (joins, CTEs, window functions and more), on real data from the City of Chicago open data portal, crimes dataset for 2023.
Python only runs the queries and draws the charts.

- [`chicago_crime_queries.sql`](chicago_crime_queries.sql): every query, runnable in any SQLite client
- [`chicago_crime.py`](chicago_crime.py): the same queries plus the charts, as a script
- [`build_db.py`](build_db.py): downloads the data and builds the SQLite database (about 30 MB download, 264,000 rows)
- [Rendered notebook with results and takeaways](https://kagap.github.io/notebooks/chicago_crime.html)

The data and the database are not stored in this repo; the build script fetches them.

## Run it

```bash
pip install -r ../requirements.txt
python build_db.py --out chicago.db
CHICAGO_DB=chicago.db python chicago_crime.py
```
