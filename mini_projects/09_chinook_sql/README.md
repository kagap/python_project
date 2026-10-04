# A music store in plain SQL

Ten business questions about the Chinook sample database (a digital music store), answered
with SQL only: joins, CTEs, a recursive CTE, window functions (`RANK`, `LAG`, `NTILE`,
running totals and moving averages), a self-join and an anti-join. Python just runs the
queries and draws the charts.

- [`chinook_queries.sql`](chinook_queries.sql): every query, runnable in any SQLite client
- [`chinook_sql.py`](chinook_sql.py): the same queries plus the charts, as a script
- [Rendered notebook](https://kagap.github.io/notebooks/chinook_sql.html)

Chinook contains generated sample data, so the project demonstrates SQL technique rather
than real findings about a business.

## Run it

1. Download `Chinook_Sqlite.sqlite` from the
   [Chinook releases page](https://github.com/lerocha/chinook-database/releases)
   (the database is not copied into this repo).
2. Point the script at it and run:

```bash
pip install -r ../requirements.txt
CHINOOK_DB=path/to/Chinook_Sqlite.sqlite python chinook_sql.py
```
