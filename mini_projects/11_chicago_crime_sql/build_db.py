"""Download Chicago crime reports for 2023 from the city's open data portal into SQLite.

usage: python build_db.py [--cache DIR] [--out chicago.db]

Source: https://data.cityofchicago.org (crimes dataset ijzp-q8t2 and community areas igwz-8jzy).
About 260,000 rows (roughly 30 MB of CSV) are downloaded once into the cache folder.
"""
import argparse
import sqlite3
from pathlib import Path

import pandas as pd
import requests

CRIMES = "https://data.cityofchicago.org/resource/ijzp-q8t2.csv"
AREAS = "https://data.cityofchicago.org/resource/igwz-8jzy.csv"
COLS = "id,date,primary_type,description,location_description,arrest,domestic,district,community_area"


def fetch_crimes(path, page=50000):
    header, rows = None, []
    for offset in range(0, 1_000_000, page):
        r = requests.get(CRIMES, timeout=180, params={
            "$select": COLS, "$order": "id", "$limit": page, "$offset": offset,
            "$where": "date between '2023-01-01T00:00:00' and '2023-12-31T23:59:59'"})
        r.raise_for_status()
        lines = r.text.splitlines()
        header = header or lines[0]
        rows.extend(lines[1:])
        print(f"  {len(rows):,} rows", flush=True)
        if len(lines) - 1 < page:
            break
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data")
    ap.add_argument("--out", default="chicago.db")
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(exist_ok=True)

    crimes_csv = cache / "chicago_crimes_2023.csv"
    if not crimes_csv.exists():
        fetch_crimes(crimes_csv)
    areas_csv = cache / "chicago_areas_names.csv"
    if not areas_csv.exists():
        r = requests.get(AREAS, params={"$select": "area_numbe,community", "$limit": 100}, timeout=60)
        r.raise_for_status()
        areas_csv.write_text(r.text, encoding="utf-8")

    crimes = pd.read_csv(crimes_csv)
    crimes["date"] = pd.to_datetime(crimes["date"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    crimes["arrest"] = crimes["arrest"].astype(str).str.lower().eq("true").astype(int)
    crimes["domestic"] = crimes["domestic"].astype(str).str.lower().eq("true").astype(int)
    crimes["community_area"] = crimes["community_area"].astype("Int64")
    crimes["district"] = crimes["district"].astype("Int64")

    areas = pd.read_csv(areas_csv).rename(columns={"area_numbe": "area_number"})
    areas["area_number"] = areas["area_number"].astype(int)
    areas["community"] = areas["community"].str.title()

    out = Path(args.out)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    crimes.to_sql("crimes", con, index=False)
    areas.to_sql("community_areas", con, index=False)
    con.execute("CREATE INDEX ix_crimes_area ON crimes (community_area)")
    con.commit()
    print(f"wrote {out}: {len(crimes):,} crimes, {len(areas)} community areas")


if __name__ == "__main__":
    main()
