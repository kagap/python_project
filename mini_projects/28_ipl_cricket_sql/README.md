# Every Ball of the IPL

Every ball of the Indian Premier League (2008 to 2026) in plain SQL, with SQL only (joins, CTEs, window functions and more), on real ball-by-ball data from Cricsheet (ODC-By licence).
Python only runs the queries and draws the charts.

- [`ipl_cricket_queries.sql`](ipl_cricket_queries.sql): every query, runnable in any SQLite client
- [`ipl_cricket.py`](ipl_cricket.py): the same queries plus the charts, as a script
- [`build_db.py`](build_db.py): downloads the data and builds the SQLite database (about 7 MB download)
- [Rendered notebook with results and takeaways](https://kagap.github.io/notebooks/ipl_cricket.html)

The data and the database are not stored in this repo; the build script fetches them.

## Run it

```bash
pip install -r ../requirements.txt
python build_db.py --out ipl.db
IPL_DB=ipl.db python ipl_cricket.py
```
