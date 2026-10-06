"""Load the MovieLens "latest small" dataset into a SQLite database.

usage: python build_db.py [--cache DIR] [--out movielens.db]

Source: GroupLens Research, https://grouplens.org/datasets/movielens/latest/ (ml-latest-small.zip, about 1 MB:
100,000 ratings by 610 users on 9,700 movies). The data may be used for research and personal projects but
must not be redistributed, which is why this repo contains only the script that downloads it.
"""
import argparse
import sqlite3
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

URL = "https://files.grouplens.org/datasets/movielens/ml-latest-small.zip"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data")
    ap.add_argument("--out", default="movielens.db")
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(exist_ok=True)
    zpath = cache / "ml-latest-small.zip"
    if not zpath.exists():
        print("downloading", URL)
        urllib.request.urlretrieve(URL, zpath)

    with zipfile.ZipFile(zpath) as z:
        movies = pd.read_csv(z.open("ml-latest-small/movies.csv"))
        ratings = pd.read_csv(z.open("ml-latest-small/ratings.csv"))
        tags = pd.read_csv(z.open("ml-latest-small/tags.csv"))

    movies = movies.rename(columns={"movieId": "movie_id"})
    movies["release_year"] = movies["title"].str.extract(r"\((\d{4})\)\s*$")[0].astype("Int64")
    genres = (movies[["movie_id", "genres"]].assign(genre=lambda d: d["genres"].str.split("|"))
              .explode("genre")[["movie_id", "genre"]])
    genres = genres[genres["genre"] != "(no genres listed)"]
    movies = movies[["movie_id", "title", "release_year"]]

    ratings = ratings.rename(columns={"userId": "user_id", "movieId": "movie_id"})
    ratings["rated_at"] = pd.to_datetime(ratings["timestamp"], unit="s").dt.strftime("%Y-%m-%d %H:%M:%S")
    ratings = ratings.drop(columns="timestamp")
    tags = tags.rename(columns={"userId": "user_id", "movieId": "movie_id"})
    tags["tagged_at"] = pd.to_datetime(tags["timestamp"], unit="s").dt.strftime("%Y-%m-%d %H:%M:%S")
    tags = tags.drop(columns="timestamp")

    out = Path(args.out)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    movies.to_sql("movies", con, index=False)
    genres.to_sql("movie_genres", con, index=False)
    ratings.to_sql("ratings", con, index=False)
    tags.to_sql("tags", con, index=False)
    con.execute("CREATE INDEX ix_ratings_movie ON ratings (movie_id)")
    con.execute("CREATE INDEX ix_ratings_user ON ratings (user_id)")
    con.commit()
    print(f"wrote {out}: {len(movies):,} movies, {len(ratings):,} ratings, {ratings.user_id.nunique()} users")


if __name__ == "__main__":
    main()
