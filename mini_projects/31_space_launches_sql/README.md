# Every Rocket Launch in History

Every rocket launch in history (71,541 launches since 1942, 7,190 of them orbital) in plain SQL, with SQL only (window functions, gaps and islands, conditional aggregation and more), on Jonathan McDowell's catalogue of space launches (GCAT).
Python only runs the queries and draws the charts.

- [`space_launches_queries.sql`](space_launches_queries.sql): every query, runnable in any SQLite client
- [`space_launches.py`](space_launches.py): the same queries plus the charts, as a script
- [`build_db.py`](build_db.py): downloads the data and builds the SQLite database (about 15 MB download)
- [Rendered notebook with results and takeaways](https://kagap.github.io/notebooks/space_launches.html)

The data and the database are not stored in this repo; the build script fetches them.

## Run it

```bash
pip install -r ../requirements.txt
python build_db.py --out space_launches.db
SPACE_DB=space_launches.db python space_launches.py
```
