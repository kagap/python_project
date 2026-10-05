# A year of road collisions in Britain, in plain SQL

8 business questions answered with SQL only (joins, CTEs, window functions and more), on real data from the UK Department for Transport road safety data (STATS19, 2023, Open Government Licence).
Python only runs the queries and draws the charts.

- [`uk_road_safety_queries.sql`](uk_road_safety_queries.sql): every query, runnable in any SQLite client
- [`uk_road_safety.py`](uk_road_safety.py): the same queries plus the charts, as a script
- [`build_db.py`](build_db.py): downloads the data and builds the SQLite database (about 52 MB download in three CSV files)
- [Rendered notebook with results and takeaways](https://kagap.github.io/notebooks/uk_road_safety.html)

The data and the database are not stored in this repo; the build script fetches them.

## Run it

```bash
pip install -r ../requirements.txt
python build_db.py --out roads.db
ROADS_DB=roads.db python uk_road_safety.py
```
