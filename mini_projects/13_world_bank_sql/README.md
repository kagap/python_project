# 60 years of countries getting richer and healthier, in plain SQL

7 business questions answered with SQL only (joins, CTEs, window functions and more), on real data from the World Bank API (World Development Indicators, CC BY 4.0).
Python only runs the queries and draws the charts.

- [`world_bank_queries.sql`](world_bank_queries.sql): every query, runnable in any SQLite client
- [`world_bank.py`](world_bank.py): the same queries plus the charts, as a script
- [`build_db.py`](build_db.py): downloads the data and builds the SQLite database (about 20 MB of JSON, built into a 1 MB database)
- [Rendered notebook with results and takeaways](https://kagap.github.io/notebooks/world_bank.html)

The data and the database are not stored in this repo; the build script fetches them.

## Run it

```bash
pip install -r ../requirements.txt
python build_db.py --out worldbank.db
WORLDBANK_DB=worldbank.db python world_bank.py
```
