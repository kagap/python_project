"""Download European unemployment and inflation series from Eurostat into a SQLite database.

usage: python build_db.py [--cache DIR] [--out eurostat.db]

Source: Eurostat dissemination API (SDMX-CSV), https://ec.europa.eu/eurostat
  une_rt_m      monthly unemployment rate, seasonally adjusted, % of the active population (total and under 25)
  prc_hicp_manr monthly HICP inflation, annual rate of change, for all items, food, housing and energy, transport
About 3 MB in total. The API returns country codes only, so the names below are added here.
"""
import argparse
import sqlite3
import urllib.request
from pathlib import Path

import pandas as pd

API = "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data"
SERIES = {
    "eurostat_unemployment.csv": f"{API}/une_rt_m/M.SA.TOTAL.PC_ACT.T.?startPeriod=2005-01&format=SDMX-CSV",
    "eurostat_unemployment_youth.csv": f"{API}/une_rt_m/M.SA.Y_LT25.PC_ACT.T.?startPeriod=2005-01&format=SDMX-CSV",
    "eurostat_inflation.csv": f"{API}/prc_hicp_manr/M.RCH_A.CP00+CP01+CP04+CP07.?startPeriod=2015-01&format=SDMX-CSV",
}
COICOP = {"CP00": "All items", "CP01": "Food and non-alcoholic drinks", "CP04": "Housing, water, electricity, gas",
          "CP07": "Transport"}
COUNTRIES = {
    "AL": "Albania", "AT": "Austria", "BA": "Bosnia and Herzegovina", "BE": "Belgium", "BG": "Bulgaria",
    "CH": "Switzerland", "CY": "Cyprus", "CZ": "Czechia", "DE": "Germany", "DK": "Denmark", "EE": "Estonia",
    "EL": "Greece", "ES": "Spain", "FI": "Finland", "FR": "France", "HR": "Croatia", "HU": "Hungary",
    "IE": "Ireland", "IS": "Iceland", "IT": "Italy", "JP": "Japan", "LT": "Lithuania", "LU": "Luxembourg",
    "LV": "Latvia", "ME": "Montenegro", "MK": "North Macedonia", "MT": "Malta", "NL": "Netherlands",
    "NO": "Norway", "PL": "Poland", "PT": "Portugal", "RO": "Romania", "RS": "Serbia", "SE": "Sweden",
    "SI": "Slovenia", "SK": "Slovakia", "TR": "Turkiye", "UK": "United Kingdom", "US": "United States",
    "XK": "Kosovo",
}
AGGREGATES = {"EA": "Euro area", "EA19": "Euro area (19)", "EA20": "Euro area (20)", "EA21": "Euro area (21)",
              "EEA": "European Economic Area", "EU": "European Union", "EU27_2020": "European Union (27)",
              "EU28": "European Union (28)"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data")
    ap.add_argument("--out", default="eurostat.db")
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(exist_ok=True)
    for name, url in SERIES.items():
        if not (cache / name).exists():
            print("downloading", name)
            urllib.request.urlretrieve(url, cache / name)

    def monthly(name, **extra):
        df = pd.read_csv(cache / name)
        return df.rename(columns={"geo": "country_code", "TIME_PERIOD": "month", "OBS_VALUE": "value"}).assign(**extra)

    total = monthly("eurostat_unemployment.csv")[["country_code", "month", "value"]].rename(columns={"value": "rate_total"})
    youth = monthly("eurostat_unemployment_youth.csv")[["country_code", "month", "value"]].rename(columns={"value": "rate_youth"})
    unemployment = total.merge(youth, on=["country_code", "month"], how="outer")

    inflation = monthly("eurostat_inflation.csv")[["country_code", "month", "coicop", "value"]].rename(
        columns={"value": "annual_rate"})

    names = {**COUNTRIES, **AGGREGATES}
    codes = sorted(set(unemployment.country_code) | set(inflation.country_code))
    countries = pd.DataFrame({"country_code": codes,
                              "country": [names.get(c, c) for c in codes],
                              "is_aggregate": [int(c in AGGREGATES) for c in codes]})
    categories = pd.DataFrame({"coicop": list(COICOP), "category": list(COICOP.values())})

    out = Path(args.out)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    countries.to_sql("countries", con, index=False)
    unemployment.to_sql("unemployment", con, index=False)
    inflation.to_sql("inflation", con, index=False)
    categories.to_sql("categories", con, index=False)
    con.commit()
    print(f"wrote {out}: {len(countries)} geos, {len(unemployment):,} unemployment and {len(inflation):,} inflation rows")


if __name__ == "__main__":
    main()
