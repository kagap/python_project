"""Download NYC restaurant inspection results (2025 onwards) from NYC Open Data into SQLite.

usage: python build_db.py [--cache DIR] [--out restaurants.db]

Source: DOHMH New York City Restaurant Inspection Results, https://data.cityofnewyork.us (dataset 43nn-pn8j).
About 163,000 rows (roughly 33 MB of CSV) are downloaded once into the cache folder. Each row is one violation
found during one inspection, or a row without a violation code when an inspection found nothing.
"""
import argparse
import sqlite3
from pathlib import Path

import pandas as pd
import requests

URL = "https://data.cityofnewyork.us/resource/43nn-pn8j.csv"
COLS = "camis,dba,boro,zipcode,cuisine_description,inspection_date,action,violation_code,critical_flag,score,grade,inspection_type"
SINCE = "2025-01-01"


def fetch_inspections(path, page=50000):
    header, rows = None, []
    for offset in range(0, 1_000_000, page):
        r = requests.get(URL, timeout=180, params={
            "$select": COLS, "$where": f"inspection_date >= '{SINCE}'",
            "$order": ":id", "$limit": page, "$offset": offset})
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
    ap.add_argument("--out", default="restaurants.db")
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(exist_ok=True)

    csv_path = cache / "nyc_restaurant_inspections.csv"
    if not csv_path.exists():
        fetch_inspections(csv_path)
    lookup_path = cache / "nyc_violation_codes.csv"
    if not lookup_path.exists():
        r = requests.get(URL, timeout=120, params={
            "$select": "violation_code,violation_description", "$group": "violation_code,violation_description",
            "$limit": 5000})
        r.raise_for_status()
        lookup_path.write_text(r.text, encoding="utf-8")

    df = pd.read_csv(csv_path, dtype={"camis": "string", "zipcode": "string", "score": "Int64"})
    df["inspection_date"] = pd.to_datetime(df["inspection_date"]).dt.strftime("%Y-%m-%d")
    df["boro"] = df["boro"].str.title()

    codes = pd.read_csv(lookup_path).dropna(subset=["violation_code"])
    codes = codes.groupby("violation_code", as_index=False).first()      # one description per code

    out = Path(args.out)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    df.to_sql("inspections", con, index=False)
    codes.to_sql("violation_codes", con, index=False)
    con.execute("CREATE INDEX ix_insp_camis ON inspections (camis, inspection_date)")
    con.commit()
    print(f"wrote {out}: {len(df):,} rows, {df['camis'].nunique():,} restaurants, {len(codes)} violation codes")


if __name__ == "__main__":
    main()
