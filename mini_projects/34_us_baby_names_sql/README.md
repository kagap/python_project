# 131 Years of American Baby Names

131 years of American baby names (1,690,784 name-and-sex rows, 322 million births, 1880 to 2010) in plain SQL, with SQL only (window functions for shares and running totals, gaps and islands, `LAG`, conditional pivots and a recursive calendar CTE) on the Social Security Administration's baby-name counts.
Python only runs the queries and draws the charts.

- [`us_baby_names_queries.sql`](us_baby_names_queries.sql): every query, runnable in any SQLite client
- [`us_baby_names.py`](us_baby_names.py): the same queries plus the charts, as a script
- [`build_db.py`](build_db.py): downloads the data and builds the SQLite database (131 yearly text files, about 21 MB, from a public GitHub copy of the SSA data; or pass the original `names.zip` from ssa.gov with `--zip`, because ssa.gov refuses scripted downloads)
- [Rendered notebook with results and takeaways](https://kagap.github.io/notebooks/us_baby_names.html)

The data and the database are not stored in this repo; the build script fetches them.

## Run it

```bash
pip install -r ../requirements.txt
python build_db.py --out baby_names.db
BABY_NAMES_DB=baby_names.db python us_baby_names.py
```
