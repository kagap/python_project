"""Load the NASA Exoplanet Archive (confirmed planets) into a SQLite database.

usage: python build_db.py [--cache DIR] [--out exoplanets.db]

Source: NASA Exoplanet Archive, Planetary Systems Composite Parameters table (pscomppars), fetched from the TAP service at
https://exoplanetarchive.ipac.caltech.edu/TAP/sync as CSV (about 6,450 confirmed planets, 1.4 MB, public data).
One row per planet, with the best available value of each parameter pulled together from different publications.

Table:
  planets   one row per planet: discovery (method, year, facility), orbit, size, mass (and where the mass comes from), host star, distance
"""
import argparse
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

TAP = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"
QUERY = ("select pl_name,hostname,discoverymethod,disc_year,disc_facility,disc_locale,pl_orbper,pl_orbsmax,pl_rade,pl_bmasse,"
         "pl_bmassprov,pl_eqt,pl_insol,pl_orbeccen,st_spectype,st_teff,st_rad,st_mass,st_age,sy_dist,sy_snum,sy_pnum,sy_vmag "
         "from pscomppars")
COLUMNS = {
    "pl_name": "planet", "hostname": "host", "discoverymethod": "method", "disc_year": "disc_year", "disc_facility": "facility",
    "disc_locale": "locale", "pl_orbper": "period_days", "pl_orbsmax": "semi_major_axis_au", "pl_rade": "radius_earth",
    "pl_bmasse": "mass_earth", "pl_bmassprov": "mass_source", "pl_eqt": "eq_temp_k", "pl_insol": "insolation_earth",
    "pl_orbeccen": "eccentricity", "st_spectype": "spectral_type", "st_teff": "star_teff_k", "st_rad": "star_radius_sun",
    "st_mass": "star_mass_sun", "st_age": "star_age_gyr", "sy_dist": "distance_pc", "sy_snum": "stars_in_system",
    "sy_pnum": "planets_in_system", "sy_vmag": "star_vmag",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data")
    ap.add_argument("--out", default="exoplanets.db")
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    csv_path = cache / "pscomppars.csv"
    if not csv_path.exists():
        url = TAP + "?" + urllib.parse.urlencode({"query": QUERY, "format": "csv"})
        print("downloading", TAP)
        urllib.request.urlretrieve(url, csv_path)

    d = pd.read_csv(csv_path).rename(columns=COLUMNS)
    d["locale"] = d["locale"].str.capitalize().replace({"Multiple locales": "Multiple"})
    d["mass_source"] = d["mass_source"].replace({"M-R relationship": "estimated from radius", "Mass": "measured",
                                                  "Msini": "minimum mass (Msini)", "Msin(i)/sin(i)": "minimum mass (Msini)"})
    # a radius is observed only for transiting planets; for the others the archive lists a value estimated from the mass
    d["radius_source"] = d["method"].isin(["Transit", "Transit Timing Variations"]).map({True: "measured (transit)", False: "estimated"})
    out = Path(args.out)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    d.to_sql("planets", con, index=False)
    con.execute("CREATE INDEX ix_planets_host ON planets (host)")
    con.commit()
    print(f"wrote {out}: {len(d):,} planets around {d['host'].nunique():,} stars")


if __name__ == "__main__":
    main()
