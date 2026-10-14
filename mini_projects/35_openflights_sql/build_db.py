"""Load the OpenFlights airports, airlines and routes into a SQLite database.

usage: python build_db.py [--cache DIR] [--out openflights.db]

Source: OpenFlights, https://openflights.org/data.html (airports and airlines under the Open Database License, routes
from a 2014-era snapshot; about 4 MB in three comma-separated files without header). The files are read from the project's
GitHub repository, https://github.com/jpatokal/openflights/tree/master/data.

Tables:
  airports   one row per airport: id, name, city, country, IATA and ICAO code, latitude, longitude, altitude in feet, time zone
  airlines   one row per airline: id, name, IATA and ICAO code, country, active flag ('Y' or 'N')
  routes     one row per airline and airport pair: airline code, source and destination airport (IATA code and id), codeshare flag, stops
The source writes a missing value as \\N; it is stored as NULL. Routes are one-way: A to B and B to A are two rows.
"""
import argparse
import csv
import sqlite3
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/{}.dat"


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.reader(f):
            yield [None if v == "\\N" or v == "" else v for v in row]


def num(v, kind=float):
    try:
        return kind(v)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data")
    ap.add_argument("--out", default="openflights.db")
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    for name in ("airports", "airlines", "routes"):
        path = cache / f"{name}.dat"
        if not path.exists():
            print("downloading", name)
            urllib.request.urlretrieve(BASE.format(name), path)

    out = Path(args.out)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    con.executescript("""
        CREATE TABLE airports (airport_id INTEGER PRIMARY KEY, name TEXT, city TEXT, country TEXT, iata TEXT, icao TEXT,
                               lat REAL, lon REAL, altitude_ft INTEGER, tz_offset REAL, dst TEXT, tz_name TEXT, type TEXT);
        CREATE TABLE airlines (airline_id INTEGER PRIMARY KEY, name TEXT, alias TEXT, iata TEXT, icao TEXT, callsign TEXT,
                               country TEXT, active TEXT);
        CREATE TABLE routes   (airline_code TEXT, airline_id INTEGER, src TEXT, src_id INTEGER, dst TEXT, dst_id INTEGER,
                               codeshare TEXT, stops INTEGER, equipment TEXT);
    """)
    con.executemany("INSERT INTO airports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(num(r[0], int), r[1], r[2], r[3], r[4], r[5], num(r[6]), num(r[7]), num(r[8], int), num(r[9]), r[10], r[11], r[12])
                     for r in read(cache / "airports.dat")])
    con.executemany("INSERT OR IGNORE INTO airlines VALUES (?,?,?,?,?,?,?,?)",
                    [(num(r[0], int), r[1], r[2], r[3], r[4], r[5], r[6], r[7]) for r in read(cache / "airlines.dat")])
    con.executemany("INSERT INTO routes VALUES (?,?,?,?,?,?,?,?,?)",
                    [(r[0], num(r[1], int), r[2], num(r[3], int), r[4], num(r[5], int), r[6], num(r[7], int), r[8])
                     for r in read(cache / "routes.dat")])
    con.executescript("""
        CREATE INDEX ix_routes_src ON routes (src_id);
        CREATE INDEX ix_routes_dst ON routes (dst_id);
        CREATE INDEX ix_airports_iata ON airports (iata);
    """)
    con.commit()
    n_ap, n_al, n_rt = (con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("airports", "airlines", "routes"))
    print(f"wrote {out}: {n_ap:,} airports, {n_al:,} airlines, {n_rt:,} routes")


if __name__ == "__main__":
    main()
