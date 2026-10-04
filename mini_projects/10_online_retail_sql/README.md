# Two years of an online gift shop, in plain SQL

8 business questions answered with SQL only (joins, CTEs, window functions and more), on real data from Online Retail II from the UCI Machine Learning Repository (real transactions, CC BY 4.0).
Python only runs the queries and draws the charts.

- [`online_retail_queries.sql`](online_retail_queries.sql): every query, runnable in any SQLite client
- [`online_retail.py`](online_retail.py): the same queries plus the charts, as a script
- [`build_db.py`](build_db.py): downloads the data and builds the SQLite database (about 46 MB download, a million rows; reading the Excel file takes a few minutes and about 1 GB of memory)
- [Rendered notebook with results and takeaways](https://kagap.github.io/notebooks/online_retail.html)

The data and the database are not stored in this repo; the build script fetches them.

## Run it

```bash
pip install -r ../requirements.txt
python build_db.py --out retail.db
RETAIL_DB=retail.db python online_retail.py
```
