"""Download the UCI Online Retail II dataset and load it into a SQLite database.

usage: python build_db.py [--cache DIR] [--out retail.db]

The zip (about 46 MB) is downloaded once into the cache folder. The Excel file has two sheets
(Dec 2009 - Dec 2010 and Dec 2010 - Dec 2011) that overlap in the first days of December 2010,
so the overlap is taken from the second sheet only.
"""
import argparse
import sqlite3
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

URL = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data")
    ap.add_argument("--out", default="retail.db")
    args = ap.parse_args()

    cache = Path(args.cache)
    cache.mkdir(exist_ok=True)
    zpath = cache / "online_retail_ii.zip"
    if not zpath.exists():
        print("downloading", URL)
        urllib.request.urlretrieve(URL, zpath)

    with zipfile.ZipFile(zpath) as z, z.open("online_retail_II.xlsx") as f:
        sheets = pd.read_excel(f, sheet_name=None, engine="openpyxl")
    first, second = list(sheets.values())
    first = first[first["InvoiceDate"] < second["InvoiceDate"].min()]   # drop the overlapping days
    df = pd.concat([first, second], ignore_index=True)
    df = df.rename(columns={"Invoice": "invoice", "StockCode": "stock_code", "Description": "description",
                            "Quantity": "quantity", "InvoiceDate": "invoice_date", "Price": "price",
                            "Customer ID": "customer_id", "Country": "country"})
    df["invoice"] = df["invoice"].astype(str)
    df["stock_code"] = df["stock_code"].astype(str)
    df["customer_id"] = df["customer_id"].astype("Int64")
    df["invoice_date"] = df["invoice_date"].dt.strftime("%Y-%m-%d %H:%M:%S")

    out = Path(args.out)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    df.to_sql("invoice_lines", con, index=False)
    con.execute("CREATE INDEX ix_invoice ON invoice_lines (invoice)")
    con.execute("CREATE INDEX ix_customer ON invoice_lines (customer_id)")
    con.commit()
    print(f"wrote {out} with {len(df):,} rows")


if __name__ == "__main__":
    main()
