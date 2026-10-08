# Twenty Years of Polish Counties

Twenty years of Polish counties (380 powiats, 2005 to 2025) in plain SQL, with SQL only (views, window functions, regression from sums and more), on public statistics from the GUS Local Data Bank API.
Python only runs the queries and draws the charts.

- [`poland_counties_queries.sql`](poland_counties_queries.sql): every query, runnable in any SQLite client
- [`poland_counties.py`](poland_counties.py): the same queries plus the charts, as a script
- [`build_db.py`](build_db.py): downloads the data and builds the SQLite database (about 45 API requests, 2 MB, cached; the anonymous API has a daily quota, so the script waits when asked to)
- [Rendered notebook with results and takeaways](https://kagap.github.io/notebooks/poland_counties.html)

The data and the database are not stored in this repo; the build script fetches them.

## Run it

```bash
pip install -r ../requirements.txt
python build_db.py --out poland_counties.db
COUNTIES_DB=poland_counties.db python poland_counties.py
```
