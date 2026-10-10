"""Load every rocket launch in history (1942 to today) into a SQLite database.

usage: python build_db.py [--cache DIR] [--out space_launches.db]

Source: Jonathan McDowell's General Catalog of Artificial Space Objects (GCAT), https://planet4589.org/space/gcat/
(tab-separated files, free to use with credit). Four files are downloaded, about 15 MB in total:
launch.tsv (one row per launch, about 76,000 rows: orbital launches, sounding rockets, missile tests), orgs.tsv, sites.tsv, lv.tsv.

Tables:
  launches   one row per launch (date, vehicle, site, agency, state, orbital flag, outcome, payload mass in tonnes)
  sites      launch sites (code, name, state, coordinates)
  orgs       organisations that launched (code, state, name)
  vehicles   one row per launch vehicle name (family, manufacturer, capacities)
"""
import argparse
import re
import sqlite3
import urllib.request
from pathlib import Path

import pandas as pd

BASE = "https://planet4589.org/space/gcat/tsv/"
FILES = {"launch": "launch/launch.tsv", "orgs": "tables/orgs.tsv", "sites": "tables/sites.tsv", "lv": "tables/lv.tsv"}
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
STATE_NAMES = {"SU": "Soviet Union", "US": "United States", "RU": "Russia", "CN": "China", "F": "France", "J": "Japan", "IN": "India",
               "NZ": "New Zealand", "IR": "Iran", "IL": "Israel", "I": "Italy", "I-ESA": "ESA", "I-ELDO": "ELDO", "UK": "United Kingdom",
               "KP": "North Korea", "KR": "South Korea", "AU": "Australia", "BR": "Brazil", "D": "Germany", "CYM": "Cayman Islands"}


def read(cache, key):
    path = cache / Path(FILES[key]).name
    if not path.exists():
        print("downloading", BASE + FILES[key])
        urllib.request.urlretrieve(BASE + FILES[key], path)
    df = pd.read_csv(path, sep="\t", skiprows=[1], dtype=str)                 # row 2 is an 'Updated' comment
    df = df.rename(columns=lambda c: c.lstrip("#"))
    return df.apply(lambda s: s.str.strip())


def parse_date(text):
    m = re.match(r"^(\d{4})(?:\s+([A-Z][a-z]{2})\s+(\d{1,2}))?(?:\s+(\d{2})(\d{2})(?::(\d{2}))?)?", text.replace("?", "").strip())
    if not m or not m.group(2):
        return None, None, None, None
    year, mon, day = int(m.group(1)), MONTHS[m.group(2)], int(m.group(3))
    hour = int(m.group(4)) if m.group(4) else None
    return f"{year:04d}-{mon:02d}-{day:02d}", year, hour, m.group(0)


def num(series):
    return pd.to_numeric(series.replace("-", None), errors="coerce")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data")
    ap.add_argument("--out", default="space_launches.db")
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)

    raw, orgs, sites, lv = (read(cache, k) for k in ("launch", "orgs", "sites", "lv"))
    parsed = raw["Launch_Date"].map(parse_date)
    launches = pd.DataFrame({
        "launch_tag": raw["Launch_Tag"],
        "launch_date": [p[0] for p in parsed],
        "year": [p[1] for p in parsed],
        "hour_utc": [p[2] for p in parsed],
        "vehicle": raw["LV_Type"],
        "variant": raw["Variant"],
        "mission": raw["Mission"],
        "site_code": raw["Launch_Site"],
        "agency_code": raw["Agency"],
        "launch_code": raw["LaunchCode"],
        "orbital": raw["LaunchCode"].str[0].eq("O").astype(int),
        "outcome": raw["LaunchCode"].str[1].map({"S": "success", "F": "failure"}).fillna("other"),
        "fail_code": raw["FailCode"].replace("-", None),
        "category": raw["Category"],
        "payload_tonnes": num(raw["OrbPay"]),
        "apogee_km": num(raw["Apogee"]),
        "inclination_deg": num(raw["Inc"]),
    })
    launches["payload_tonnes"] = launches["payload_tonnes"].where(launches["payload_tonnes"] > 0)

    # the agency field is sometimes a joint one such as 'JSC/MSFC': use the first organisation to find the state
    orgs = orgs.drop_duplicates("Code").set_index("Code")
    first = launches["agency_code"].str.split("/").str[0]
    launches["state_code"] = launches["agency_code"].map(orgs["StateCode"]).fillna(first.map(orgs["StateCode"]))
    launches["state"] = launches["state_code"].map(STATE_NAMES).fillna(launches["state_code"])
    launches = launches.dropna(subset=["launch_date"])
    launches[["year", "hour_utc"]] = launches[["year", "hour_utc"]].astype("Int64")

    sites_out = pd.DataFrame({"site_code": sites["Site"], "state_code": sites["StateCode"], "short_name": sites["ShortName"],
                              "name": sites["Name"], "longitude": num(sites["Longitude"]), "latitude": num(sites["Latitude"])})
    orgs_out = orgs.reset_index()[["Code", "StateCode", "ShortName", "Name"]]
    orgs_out.columns = ["agency_code", "state_code", "short_name", "name"]
    vehicles = lv.drop_duplicates("LV_Name")[["LV_Name", "LV_Family", "LV_Manufacturer", "LEO_Capacity", "GTO_Capacity"]]
    vehicles.columns = ["vehicle", "family", "manufacturer", "leo_capacity_kg", "gto_capacity_kg"]
    for c in ("leo_capacity_kg", "gto_capacity_kg"):
        vehicles[c] = pd.to_numeric(vehicles[c], errors="coerce").where(lambda s: s > 0)

    out = Path(args.out)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    launches.to_sql("launches", con, index=False)
    sites_out.to_sql("sites", con, index=False)
    orgs_out.to_sql("orgs", con, index=False)
    vehicles.to_sql("vehicles", con, index=False)
    con.execute("CREATE INDEX ix_launch_year ON launches (year, orbital)")
    con.execute("CREATE INDEX ix_launch_vehicle ON launches (vehicle)")
    con.commit()
    print(f"wrote {out}: {len(launches):,} launches ({int(launches.orbital.sum()):,} orbital), {len(sites_out)} sites, {len(vehicles)} vehicles")


if __name__ == "__main__":
    main()
