"""Load the Project Gutenberg catalogue into a normalised SQLite database.

usage: python build_db.py [--cache DIR] [--out gutenberg.db]

Source: https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv (about 21 MB, one row per book, public domain
metadata). Authors, languages, subjects and bookshelves are packed into semicolon-separated text fields, so they are
split into proper tables:
  books            one row per book (id, type, issue date, title)
  book_languages   one row per book and language
  authors          one row per distinct author name and life span
  book_authors     which author wrote which book
  book_subjects    one row per book and subject heading
  book_shelves     one row per book and bookshelf
"""
import argparse
import re
import sqlite3
import urllib.request
from pathlib import Path

import pandas as pd

URL = "https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv"
# "Austen, Jane, 1775-1817" or "Homer, 750? BCE-650? BCE" or "Various"
LIFESPAN = re.compile(r",\s*(?P<born>\d{1,4})?\??\s*(?P<bce1>BCE)?\s*-\s*(?P<died>\d{1,4})?\??\s*(?P<bce2>BCE)?\s*$")


def split_field(value):
    if pd.isna(value):
        return []
    return [p.strip() for p in str(value).split(";") if p.strip()]


def parse_author(text):
    """Return (name, birth_year, death_year) with BCE years as negative numbers."""
    name = re.sub(r"\s*\[[^\]]*\]\s*$", "", text.strip())      # drop a trailing role such as [Translator] first
    m = LIFESPAN.search(name)
    born = died = None
    if m:
        name = name[:m.start()].strip().rstrip(",")
        if m.group("born"):
            born = -int(m.group("born")) if (m.group("bce1") or m.group("bce2")) else int(m.group("born"))
        if m.group("died"):
            died = -int(m.group("died")) if m.group("bce2") else int(m.group("died"))
    name = re.sub(r"\s*\[[^\]]*\]\s*$", "", name)          # drop a trailing role such as [Translator]
    return name, born, died


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data")
    ap.add_argument("--out", default="gutenberg.db")
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(exist_ok=True)
    csv_path = cache / "pg_catalog.csv"
    if not csv_path.exists():
        print("downloading", URL)
        urllib.request.urlretrieve(URL, csv_path)

    raw = pd.read_csv(csv_path, dtype=str)
    raw = raw.rename(columns={"Text#": "book_id", "Type": "type", "Issued": "issued", "Title": "title", "Language": "language",
                              "Authors": "authors", "Subjects": "subjects", "LoCC": "locc", "Bookshelves": "bookshelves"})
    raw["book_id"] = raw["book_id"].astype(int)
    raw["issued"] = pd.to_datetime(raw["issued"], errors="coerce").dt.strftime("%Y-%m-%d")

    languages, authors_rows, subjects, shelves = [], [], [], []
    for r in raw.itertuples(index=False):
        languages += [(r.book_id, lang) for lang in split_field(r.language)]
        authors_rows += [(r.book_id, *parse_author(a)) for a in split_field(r.authors)]
        subjects += [(r.book_id, s) for s in split_field(r.subjects)]
        shelves += [(r.book_id, s) for s in split_field(r.bookshelves)]

    book_authors = pd.DataFrame(authors_rows, columns=["book_id", "name", "birth_year", "death_year"])
    authors = (book_authors.groupby("name", as_index=False)
               .agg(birth_year=("birth_year", "first"), death_year=("death_year", "first")))
    authors[["birth_year", "death_year"]] = authors[["birth_year", "death_year"]].astype("Int64")   # whole years, NULL when unknown
    authors.insert(0, "author_id", range(1, len(authors) + 1))
    book_authors = book_authors.merge(authors[["author_id", "name"]], on="name")[["book_id", "author_id"]].drop_duplicates()

    out = Path(args.out)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    raw[["book_id", "type", "issued", "title"]].to_sql("books", con, index=False)
    pd.DataFrame(languages, columns=["book_id", "language"]).to_sql("book_languages", con, index=False)
    authors.to_sql("authors", con, index=False)
    book_authors.to_sql("book_authors", con, index=False)
    pd.DataFrame(subjects, columns=["book_id", "subject"]).to_sql("book_subjects", con, index=False)
    pd.DataFrame(shelves, columns=["book_id", "shelf"]).to_sql("book_shelves", con, index=False)
    for stmt in ("CREATE INDEX ix_ba_book ON book_authors (book_id)", "CREATE INDEX ix_ba_author ON book_authors (author_id)",
                 "CREATE INDEX ix_bl_book ON book_languages (book_id)", "CREATE INDEX ix_bs_book ON book_subjects (book_id)"):
        con.execute(stmt)
    con.commit()
    print(f"wrote {out}: {len(raw):,} books, {len(authors):,} authors, {len(subjects):,} subject links")


if __name__ == "__main__":
    main()
