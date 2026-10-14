# Twenty Thousand Board Games

21,831 board games from BoardGameGeek (a snapshot of January 2022, games with at least 30 ratings) in plain SQL, with SQL only (a regression computed from sums that reverse-engineers the site's Bayesian average, `RANK()` to compare the raw and the adjusted ranking, a **recursive CTE** that splits the text lists of mechanics and designers into rows, shares by period for rising and falling mechanics, `NTILE()` for the long tail of ratings and window sums) on the data published by the TidyTuesday project.
Python only runs the queries and draws the charts.

- [`boardgames_queries.sql`](boardgames_queries.sql): every query, runnable in any SQLite client against the database that `build_db.py` creates (it also defines the three views the queries use)
- [`boardgames.py`](boardgames.py): the same queries plus the charts, as a script
- [`build_db.py`](build_db.py): downloads the data and builds the SQLite database (two CSV files, about 38 MB, from the TidyTuesday repository)
- [Rendered notebook with results and takeaways](https://kagap.github.io/notebooks/boardgames.html)

The lists of categories, mechanics and designers come in the source as one text per game; `build_db.py` normalises them to `a|b|c` and defines the views `game_mechanics`, `game_categories` and `game_designers` with a recursive CTE. Only games with at least 30 ratings are in the data, so recent years are thin, and the raters are BoardGameGeek members.

The data and the database are not stored in this repo; the build script fetches them. The data is [BoardGameGeek](https://boardgamegeek.com) as published in [TidyTuesday](https://github.com/rfordatascience/tidytuesday/tree/master/data/2022/2022-01-25) (CC0).

## Run it

```bash
pip install -r ../requirements.txt
python build_db.py --out boardgames.db
BOARDGAMES_DB=boardgames.db python boardgames.py
```
