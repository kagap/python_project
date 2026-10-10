"""Load the NOAA HURDAT2 Atlantic hurricane database (1851 onwards) into a SQLite database.

usage: python build_db.py [--cache DIR] [--out hurricanes.db] [--url URL]

Source: NOAA National Hurricane Center, HURDAT2 "best track" file for the Atlantic basin,
https://www.nhc.noaa.gov/data/hurdat/ (about 7 MB text file, public domain). The file name contains the update date, so
the newest one is found on that page (the default URL below is the file used for this project).

The text has one header line per storm ("AL012025, ARTHUR, 31,") followed by one line per 6-hourly (or special) observation.
It is parsed into three tables:
  storms   one row per storm: id, year, name, first and last observation, peak wind and lowest pressure
  tracks   one row per observation: time, status, position, maximum sustained wind (knots), central pressure (mb), record identifier
Wind of -99 and pressure of -999 mean 'missing' in the source and are stored as NULL.
"""
import argparse
import sqlite3
import urllib.request
from pathlib import Path

import pandas as pd

URL = "https://www.nhc.noaa.gov/data/hurdat/hurdat2-1851-2025-092326.txt"


def parse(path):
    storms, tracks = [], []
    storm_id = None
    for line in Path(path).read_text(encoding="ascii", errors="replace").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3 and parts[0][:2] == "AL" and len(parts[0]) == 8:           # header: AL012025, NAME, rows,
            storm_id = parts[0]
            storms.append((storm_id, int(parts[0][4:]), int(parts[0][2:4]), parts[1].title() if parts[1] != "UNNAMED" else None))
        elif storm_id and len(parts) >= 8:
            date, hhmm, record, status, lat, lon, wind, pressure = parts[:8]
            lat_v = float(lat[:-1]) * (1 if lat[-1] == "N" else -1)
            lon_v = float(lon[:-1]) * (1 if lon[-1] == "E" else -1)
            tracks.append((storm_id, f"{date[:4]}-{date[4:6]}-{date[6:]} {hhmm[:2]}:{hhmm[2:]}", record or None, status,
                           lat_v, lon_v, int(wind) if int(wind) > 0 else None, int(pressure) if int(pressure) > 0 else None))
    return storms, tracks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data")
    ap.add_argument("--out", default="hurricanes.db")
    ap.add_argument("--url", default=URL)
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / "hurdat2.txt"
    if not path.exists():
        print("downloading", args.url)
        urllib.request.urlretrieve(args.url, path)

    storms, tracks = parse(path)
    out = Path(args.out)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    pd.DataFrame(storms, columns=["storm_id", "year", "number", "name"]).to_sql("storm_names", con, index=False)
    pd.DataFrame(tracks, columns=["storm_id", "obs_time", "record_id", "status", "lat", "lon", "max_wind_kt", "min_pressure_mb"]).to_sql(
        "tracks", con, index=False)
    con.execute("CREATE INDEX ix_tracks_storm ON tracks (storm_id, obs_time)")
    con.execute("""CREATE TABLE storms AS
        SELECT n.storm_id, n.year, n.number, n.name,
               MIN(t.obs_time) AS first_obs, MAX(t.obs_time) AS last_obs, COUNT(*) AS observations,
               MAX(t.max_wind_kt) AS peak_wind_kt, MIN(t.min_pressure_mb) AS lowest_pressure_mb
        FROM storm_names n JOIN tracks t USING (storm_id)
        GROUP BY n.storm_id""")
    con.execute("DROP TABLE storm_names")
    con.commit()
    n_storms, n_obs = con.execute("SELECT COUNT(*), SUM(observations) FROM storms").fetchone()
    print(f"wrote {out}: {n_storms:,} storms, {n_obs:,} observations")


if __name__ == "__main__":
    main()
