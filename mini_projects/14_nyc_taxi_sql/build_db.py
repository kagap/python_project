"""Load one month of NYC yellow taxi trips (January 2023) into a SQLite database.

usage: python build_db.py [--cache DIR] [--out taxi.db]

Source: NYC Taxi & Limousine Commission trip records, https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page
The parquet file (about 48 MB, roughly 3 million trips) and the small taxi-zone lookup are downloaded once
into the cache folder. Needs pyarrow (pip install pyarrow). The resulting database is about 300 MB.
"""
import argparse
import sqlite3
import urllib.request
from pathlib import Path

import pandas as pd

BASE = "https://d37ci6vzurychx.cloudfront.net"
TRIPS = f"{BASE}/trip-data/yellow_tripdata_2023-01.parquet"
ZONES = f"{BASE}/misc/taxi_zone_lookup.csv"

# codes from the TLC data dictionary
PAYMENT = {0: "Flex Fare trip", 1: "Credit card", 2: "Cash", 3: "No charge", 4: "Dispute", 5: "Unknown", 6: "Voided trip"}
RATECODE = {1: "Standard rate", 2: "JFK", 3: "Newark", 4: "Nassau or Westchester", 5: "Negotiated fare", 6: "Group ride"}

COLUMNS = ["VendorID", "tpep_pickup_datetime", "tpep_dropoff_datetime", "passenger_count", "trip_distance",
           "RatecodeID", "PULocationID", "DOLocationID", "payment_type", "fare_amount", "tip_amount",
           "tolls_amount", "total_amount"]


def fetch(url, path):
    if not path.exists():
        print("downloading", url)
        urllib.request.urlretrieve(url, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data")
    ap.add_argument("--out", default="taxi.db")
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(exist_ok=True)
    fetch(TRIPS, cache / "yellow_tripdata_2023-01.parquet")
    fetch(ZONES, cache / "taxi_zone_lookup.csv")

    trips = pd.read_parquet(cache / "yellow_tripdata_2023-01.parquet", columns=COLUMNS)
    trips = trips.rename(columns={
        "VendorID": "vendor_id", "tpep_pickup_datetime": "pickup_time", "tpep_dropoff_datetime": "dropoff_time",
        "RatecodeID": "ratecode_id", "PULocationID": "pickup_zone_id", "DOLocationID": "dropoff_zone_id"})
    for col in ("pickup_time", "dropoff_time"):
        trips[col] = trips[col].dt.strftime("%Y-%m-%d %H:%M:%S")

    # keep_default_na=False: the zone literally named "N/A" would otherwise become NULL
    zones = pd.read_csv(cache / "taxi_zone_lookup.csv", keep_default_na=False).rename(columns={
        "LocationID": "zone_id", "Borough": "borough", "Zone": "zone"})

    out = Path(args.out)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    trips.to_sql("trips", con, index=False, chunksize=200_000)
    zones.to_sql("zones", con, index=False)
    pd.DataFrame({"payment_type": list(PAYMENT), "payment_name": list(PAYMENT.values())}).to_sql(
        "payment_types", con, index=False)
    pd.DataFrame({"ratecode_id": list(RATECODE), "ratecode_name": list(RATECODE.values())}).to_sql(
        "ratecodes", con, index=False)
    con.execute("CREATE INDEX ix_trips_pickup_zone ON trips (pickup_zone_id)")
    con.commit()
    print(f"wrote {out}: {len(trips):,} trips, {len(zones)} zones")


if __name__ == "__main__":
    main()
