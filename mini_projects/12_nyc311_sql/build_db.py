"""Download a sample of NYC 311 service requests (2023) from NYC Open Data into SQLite.

usage: python build_db.py [--cache DIR] [--out nyc311.db]

Source: https://data.cityofnewyork.us (dataset erm2-nwe9). To keep the download to about 30 MB, only
requests created on the 1st and 15th of every month of 2023 are taken (about 210,000 rows).
"""
import argparse
import sqlite3
from pathlib import Path

import pandas as pd
import requests

URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.csv"
COLS = "unique_key,created_date,closed_date,agency,complaint_type,descriptor,borough,incident_zip,status"


def fetch(path):
    header, rows = None, []
    for month in range(1, 13):
        for day in (1, 15):
            where = (f"created_date between '2023-{month:02d}-{day:02d}T00:00:00' "
                     f"and '2023-{month:02d}-{day:02d}T23:59:59'")
            for attempt in range(3):
                try:
                    r = requests.get(URL, timeout=120, params={
                        "$select": COLS, "$order": "unique_key", "$limit": 50000, "$where": where})
                    r.raise_for_status()
                    break
                except requests.RequestException:
                    if attempt == 2:
                        raise
            lines = r.text.splitlines()
            header = header or lines[0]
            rows.extend(lines[1:])
        print(f"  month {month}: {len(rows):,} rows", flush=True)
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data")
    ap.add_argument("--out", default="nyc311.db")
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(exist_ok=True)
    csv_path = cache / "nyc_311_2023_sample.csv"
    if not csv_path.exists():
        fetch(csv_path)

    df = pd.read_csv(csv_path, dtype={"incident_zip": "string"})
    for col in ("created_date", "closed_date"):
        df[col] = pd.to_datetime(df[col]).dt.strftime("%Y-%m-%d %H:%M:%S")
    df["borough"] = df["borough"].str.title()

    out = Path(args.out)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    df.to_sql("requests", con, index=False)
    con.execute("CREATE INDEX ix_requests_type ON requests (complaint_type)")
    con.commit()
    print(f"wrote {out}: {len(df):,} requests")


if __name__ == "__main__":
    main()
