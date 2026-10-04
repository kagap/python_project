"""Download country indicators from the World Bank API into a SQLite database.

usage: python build_db.py [--cache DIR] [--out worldbank.db]

Source: https://api.worldbank.org/v2 (World Development Indicators), 1960-2023. Regional and
income-group aggregates are removed so only real countries remain.
"""
import argparse
import json
import sqlite3
from pathlib import Path

import pandas as pd
import requests

BASE = "https://api.worldbank.org/v2"
INDICATORS = {
    "NY.GDP.PCAP.CD": "gdp_per_capita",             # current US$
    "SP.POP.TOTL": "population",
    "SP.DYN.LE00.IN": "life_expectancy",            # years
    "EN.GHG.CO2.PC.CE.AR5": "co2_per_capita",       # t CO2e, excluding land use
    "SH.DYN.MORT": "under5_mortality",              # per 1,000 live births
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data")
    ap.add_argument("--out", default="worldbank.db")
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(exist_ok=True)

    meta_path, ind_path = cache / "wb_countries.json", cache / "wb_indicators.json"
    if not meta_path.exists():
        r = requests.get(f"{BASE}/country", params={"format": "json", "per_page": 400}, timeout=60)
        meta_path.write_text(json.dumps(r.json()[1]), encoding="utf-8")
    if not ind_path.exists():
        data = []
        for code in INDICATORS:
            r = requests.get(f"{BASE}/country/all/indicator/{code}", timeout=180,
                             params={"format": "json", "per_page": 20000, "date": "1960:2023"}).json()
            data.append({"code": code, "rows": r[1]})
        ind_path.write_text(json.dumps(data), encoding="utf-8")

    countries = pd.DataFrame([
        {"code": c["id"], "name": c["name"], "region": c["region"]["value"],
         "income_group": c["incomeLevel"]["value"]}
        for c in json.loads(meta_path.read_text(encoding="utf-8"))
        if c["region"]["value"] != "Aggregates"])

    frames = []
    for block in json.loads(ind_path.read_text(encoding="utf-8")):
        df = pd.DataFrame([{"country_code": r["countryiso3code"], "year": int(r["date"]),
                            INDICATORS[block["code"]]: r["value"]} for r in block["rows"]])
        frames.append(df.set_index(["country_code", "year"]))
    panel = pd.concat(frames, axis=1).reset_index()
    panel = panel[panel["country_code"].isin(countries["code"])]
    panel = panel.dropna(subset=list(INDICATORS.values()), how="all")

    out = Path(args.out)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    countries.to_sql("countries", con, index=False)
    panel.to_sql("indicators", con, index=False)
    con.execute("CREATE INDEX ix_ind ON indicators (country_code, year)")
    con.commit()
    print(f"wrote {out}: {len(countries)} countries, {len(panel):,} country-year rows")


if __name__ == "__main__":
    main()
