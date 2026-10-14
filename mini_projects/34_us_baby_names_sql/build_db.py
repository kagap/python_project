"""Load the US Social Security Administration baby names (1880 to 2010) into a SQLite database.

usage: python build_db.py [--cache DIR] [--out baby_names.db] [--zip names.zip]

Source: US Social Security Administration, "Popular Baby Names" (public domain), https://www.ssa.gov/oact/babynames/limits.html
Every year is a text file `yobYYYY.txt` with lines `name,sex,births`, one line per name and sex that was given to at least five babies.
ssa.gov refuses scripted downloads, so by default the 131 yearly files are fetched from a public copy of the same data on GitHub
(wesm/pydata-book, about 21 MB). If you prefer the original, download `names.zip` in a browser from the SSA page above (it is newer
and runs to the latest year) and pass it with --zip.

One table, `names`: year, name, sex ('F' or 'M'), births. A name given to fewer than five babies of one sex in a year is not in the data,
so yearly totals are slightly lower than real birth counts and rare names are missing.
"""
import argparse
import csv
import io
import sqlite3
import urllib.request
import zipfile
from pathlib import Path

MIRROR = "https://raw.githubusercontent.com/wesm/pydata-book/3rd-edition/datasets/babynames/yob{year}.txt"
YEARS = range(1880, 2011)


def rows_from_text(year, text):
    for name, sex, births in csv.reader(io.StringIO(text)):
        yield year, name, sex, int(births)


def read_zip(path):
    with zipfile.ZipFile(path) as z:
        for member in sorted(z.namelist()):
            if member.startswith("yob") and member.endswith(".txt"):
                yield from rows_from_text(int(member[3:7]), z.read(member).decode("ascii"))


def read_mirror(cache):
    cache.mkdir(parents=True, exist_ok=True)
    for year in YEARS:
        path = cache / f"yob{year}.txt"
        if not path.exists():
            print("downloading", year, end="\r")
            urllib.request.urlretrieve(MIRROR.format(year=year), path)
        yield from rows_from_text(year, path.read_text(encoding="ascii"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data")
    ap.add_argument("--out", default="baby_names.db")
    ap.add_argument("--zip", help="names.zip from ssa.gov instead of downloading the yearly files")
    args = ap.parse_args()

    out = Path(args.out)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    con.execute("CREATE TABLE names (year INTEGER NOT NULL, name TEXT NOT NULL, sex TEXT NOT NULL, births INTEGER NOT NULL)")
    rows = read_zip(args.zip) if args.zip else read_mirror(Path(args.cache))
    con.executemany("INSERT INTO names VALUES (?, ?, ?, ?)", rows)
    con.execute("CREATE INDEX ix_names_name ON names (name, sex, year)")
    con.execute("CREATE INDEX ix_names_year ON names (year, sex, births)")
    con.commit()
    n, first, last, total = con.execute("SELECT COUNT(*), MIN(year), MAX(year), SUM(births) FROM names").fetchone()
    print(f"wrote {out}: {n:,} rows, {first} to {last}, {total:,} births")


if __name__ == "__main__":
    main()
