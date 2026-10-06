# 25 seasons of the Premier League, in plain SQL

8 business questions answered with SQL only (joins, CTEs, window functions and more), on real data from football-data.co.uk (Premier League results, 2000/01 to 2024/25).
Python only runs the queries and draws the charts.

- [`premier_league_queries.sql`](premier_league_queries.sql): every query, runnable in any SQLite client
- [`premier_league.py`](premier_league.py): the same queries plus the charts, as a script
- [`build_db.py`](build_db.py): downloads the data and builds the SQLite database (about 3 MB download in 25 CSV files)
- [Rendered notebook with results and takeaways](https://kagap.github.io/notebooks/premier_league.html)

The data and the database are not stored in this repo; the build script fetches them.

## Run it

```bash
pip install -r ../requirements.txt
python build_db.py --out premier_league.db
PL_DB=premier_league.db python premier_league.py
```
