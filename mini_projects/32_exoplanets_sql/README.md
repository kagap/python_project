# 6,445 Worlds Beyond the Solar System

Every confirmed exoplanet (6,445 planets around 4,842 stars) in plain SQL, with SQL only (window functions, medians from ROW_NUMBER, LAG within systems and more), on the NASA Exoplanet Archive.
Python only runs the queries and draws the charts.

- [`exoplanets_queries.sql`](exoplanets_queries.sql): every query, runnable in any SQLite client
- [`exoplanets.py`](exoplanets.py): the same queries plus the charts, as a script
- [`build_db.py`](build_db.py): downloads the data and builds the SQLite database (about 1.4 MB download from the archive's TAP service)
- [Rendered notebook with results and takeaways](https://kagap.github.io/notebooks/exoplanets.html)

The data and the database are not stored in this repo; the build script fetches them.

## Run it

```bash
pip install -r ../requirements.txt
python build_db.py --out exoplanets.db
EXO_DB=exoplanets.db python exoplanets.py
```
