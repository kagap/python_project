"""Load every Indian Premier League match (2008 onwards), ball by ball, into a SQLite database.

usage: python build_db.py [--cache DIR] [--out ipl.db]

Source: Cricsheet, https://cricsheet.org/downloads/ (ipl_csv2.zip, about 7 MB, ODC-By licence).
The zip holds one file with every delivery (about 280,000 rows) and one small "info" file per match
(teams, venue, toss, winner, player of the match). Both are parsed here into two tables:
  matches     one row per match
  deliveries  one row per ball bowled
"""
import argparse
import csv
import io
import sqlite3
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

URL = "https://cricsheet.org/downloads/ipl_csv2.zip"
DELIVERY_COLS = ["match_id", "innings", "ball", "batting_team", "bowling_team", "striker", "non_striker", "bowler",
                 "runs_off_bat", "extras", "wides", "noballs", "byes", "legbyes", "penalty", "wicket_type",
                 "player_dismissed"]


# The same ground appears under several spellings (with and without the city) and some were renamed.
VENUE_ALIASES = {
    "M.Chinnaswamy Stadium": "M Chinnaswamy Stadium",
    "Feroz Shah Kotla": "Arun Jaitley Stadium",                       # renamed in 2019
    "Punjab Cricket Association Stadium": "Punjab Cricket Association IS Bindra Stadium",
    "Zayed Cricket Stadium": "Sheikh Zayed Stadium",
    "Sardar Patel Stadium": "Narendra Modi Stadium",                   # the Motera ground, renamed
    "Subrata Roy Sahara Stadium": "Maharashtra Cricket Association Stadium",   # Pune, sponsor name
}


def clean_venue(name):
    base = name.split(",")[0].strip()
    return VENUE_ALIASES.get(base, base)


def parse_info(text):
    """The info file is 'info,key,value' lines; a few keys repeat (team, umpire, player)."""
    rec = {"team1": None, "team2": None}
    teams = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3 or row[0] != "info":
            continue
        key, value = row[1], row[2]
        if key == "team":
            teams.append(value)
        elif key in ("season", "date", "venue", "city", "toss_winner", "toss_decision", "player_of_match", "winner",
                     "winner_runs", "winner_wickets", "outcome", "method", "match_number", "match_id", "eliminator") \
                and key not in rec:
            rec[key] = value
    rec["team1"], rec["team2"] = (teams + [None, None])[:2]
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data")
    ap.add_argument("--out", default="ipl.db")
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(exist_ok=True)
    zpath = cache / "ipl_csv2.zip"
    if not zpath.exists():
        print("downloading", URL)
        urllib.request.urlretrieve(URL, zpath)

    with zipfile.ZipFile(zpath) as z:
        info_names = [n for n in z.namelist() if n.endswith("_info.csv")]
        matches = pd.DataFrame([parse_info(z.read(n).decode("utf-8")) for n in info_names])
        deliveries = pd.read_csv(z.open("all_matches.csv"), usecols=DELIVERY_COLS, low_memory=False)

    matches["match_id"] = matches["match_id"].astype(int)
    matches["venue_raw"] = matches["venue"]
    matches["venue"] = matches["venue"].map(clean_venue)
    matches["date"] = pd.to_datetime(matches["date"], format="%Y/%m/%d").dt.strftime("%Y-%m-%d")
    for col in ("winner_runs", "winner_wickets", "match_number"):
        matches[col] = pd.to_numeric(matches[col], errors="coerce").astype("Int64")
    matches = matches.rename(columns={"winner_runs": "won_by_runs", "winner_wickets": "won_by_wickets"})
    matches["super_over"] = matches["eliminator"].notna().astype(int)
    matches = matches.drop(columns="eliminator").sort_values(["date", "match_id"])

    deliveries["over"] = deliveries["ball"].astype(float).astype(int)                    # 0 = first over
    deliveries["ball_in_over"] = ((deliveries["ball"].astype(float) - deliveries["over"]) * 10).round().astype(int)
    for col in ("wides", "noballs", "byes", "legbyes", "penalty"):
        deliveries[col] = deliveries[col].fillna(0).astype(int)
    deliveries = deliveries.rename(columns={"ball": "ball_label"})

    out = Path(args.out)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    matches.to_sql("matches", con, index=False)
    deliveries.to_sql("deliveries", con, index=False, chunksize=100_000)
    con.execute("CREATE INDEX ix_del_match ON deliveries (match_id, innings)")
    con.execute("CREATE INDEX ix_del_striker ON deliveries (striker)")
    con.execute("CREATE INDEX ix_del_bowler ON deliveries (bowler)")
    con.commit()
    print(f"wrote {out}: {len(matches):,} matches, {len(deliveries):,} deliveries")


if __name__ == "__main__":
    main()
