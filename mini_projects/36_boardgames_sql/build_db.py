"""Load the BoardGameGeek ratings and game details (TidyTuesday, 2022-01-25) into a SQLite database.

usage: python build_db.py [--cache DIR] [--out boardgames.db]

Source: BoardGameGeek, as published in the TidyTuesday project (https://github.com/rfordatascience/tidytuesday, CC0), two CSV files:
  ratings.csv   21,831 games with at least 30 ratings: rank, average rating, Bayesian average, number of ratings (about 5 MB)
  details.csv   21,631 of them with year, players, playing time, age, owners and the lists of categories, mechanics, designers (about 33 MB)

One table, `games`, joins the two files. The lists are stored as text with the items separated by a pipe, for example
'Action Points|Cooperative Game|Hand Management'. Three views split them into one row per game and item with a **recursive CTE**:
game_mechanics(game_id, mechanic), game_categories(game_id, category) and game_designers(game_id, designer).
Placeholder values of the source are stored as NULL: a publication year of 0 or after 2023 (or before 1000), 0 players, 0 minutes.
The data is a snapshot of January 2022 and holds only games that have at least 30 ratings, so the newest years are thin.
"""
import argparse
import ast
import sqlite3
import urllib.request
from pathlib import Path

import pandas as pd

BASE = "https://raw.githubusercontent.com/rfordatascience/tidytuesday/master/data/2022/2022-01-25/{}.csv"
LISTS = {"mechanics": "game_mechanics", "categories": "game_categories", "designers": "game_designers"}
ITEM = {"mechanics": "mechanic", "categories": "category", "designers": "designer"}


def to_pipe(value):
    """\"['A', \\\"B's\\\"]\" -> 'A|B's' (None for missing or empty)."""
    if not isinstance(value, str) or not value.strip():
        return None
    items = [str(x).replace("|", "/").strip() for x in ast.literal_eval(value)]
    return "|".join(i for i in items if i) or None


def view_sql(column, view, item):
    return f"""
    CREATE VIEW {view} AS
    WITH RECURSIVE split(game_id, item, rest) AS (
        SELECT game_id, '', {column} || '|' FROM games WHERE {column} IS NOT NULL
        UNION ALL
        SELECT game_id, substr(rest, 1, instr(rest, '|') - 1), substr(rest, instr(rest, '|') + 1) FROM split WHERE rest <> ''
    )
    SELECT game_id, item AS {item} FROM split WHERE item <> '';"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data")
    ap.add_argument("--out", default="boardgames.db")
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    for name in ("ratings", "details"):
        path = cache / f"{name}.csv"
        if not path.exists():
            print("downloading", name)
            urllib.request.urlretrieve(BASE.format(name), path)

    r = pd.read_csv(cache / "ratings.csv", usecols=["id", "name", "year", "rank", "average", "bayes_average", "users_rated"])
    d = pd.read_csv(cache / "details.csv", usecols=["id", "yearpublished", "minplayers", "maxplayers", "playingtime", "minage", "boardgamecategory",
                                                      "boardgamemechanic", "boardgamedesigner", "owned", "trading", "wanting", "wishing"])
    g = r.merge(d, on="id", how="left")
    year = g.yearpublished.fillna(g.year)
    g["year"] = year.where((year >= 1000) & (year <= 2023)).astype("Int64")
    for col, bad in (("minplayers", 0), ("maxplayers", 0), ("playingtime", 0), ("minage", 0)):
        g[col] = g[col].where(g[col] != bad).astype("Int64")
    for col in ("owned", "trading", "wanting", "wishing"):
        g[col] = g[col].astype("Int64")
    g["categories"] = g.boardgamecategory.map(to_pipe)
    g["mechanics"] = g.boardgamemechanic.map(to_pipe)
    g["designers"] = g.boardgamedesigner.map(to_pipe)
    g = g.rename(columns={"id": "game_id", "average": "avg_rating", "bayes_average": "bayes_rating", "minplayers": "min_players", "maxplayers": "max_players",
                          "playingtime": "playing_time", "minage": "min_age"})
    cols = ["game_id", "name", "year", "rank", "avg_rating", "bayes_rating", "users_rated", "min_players", "max_players", "playing_time", "min_age",
            "owned", "trading", "wanting", "wishing", "categories", "mechanics", "designers"]

    out = Path(args.out)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    g[cols].to_sql("games", con, index=False)
    con.execute("CREATE UNIQUE INDEX ix_games_id ON games (game_id)")
    for column, view in LISTS.items():
        con.execute(view_sql(column, view, ITEM[column]))
    con.commit()
    n = con.execute("SELECT COUNT(*), SUM(year IS NULL), SUM(mechanics IS NOT NULL), SUM(owned IS NOT NULL) FROM games").fetchone()
    links = [con.execute(f"SELECT COUNT(*) FROM {v}").fetchone()[0] for v in LISTS.values()]
    print(f"wrote {out}: {n[0]:,} games ({n[1]} without a year, {n[2]:,} with mechanics, {n[3]:,} with details); "
          f"{links[0]:,} mechanic, {links[1]:,} category and {links[2]:,} designer links")


if __name__ == "__main__":
    main()
