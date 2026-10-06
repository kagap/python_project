"""Load 25 Premier League seasons (2000/01 to 2024/25) into a SQLite database.

usage: python build_db.py [--cache DIR] [--out premier_league.db]

Source: https://www.football-data.co.uk/englandm.php (one CSV per season, about 3 MB in total, free for personal use).
Each season has 380 matches. The files differ slightly between seasons (columns come and go), so only the columns
that exist in every season are kept: result, half-time score, shots, fouls, corners, cards, referee and the
William Hill match odds.
"""
import argparse
import csv
import sqlite3
import urllib.request
from pathlib import Path

import pandas as pd

URL = "https://www.football-data.co.uk/mmz4281/{code}/E0.csv"
SEASONS = [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(2000, 2025)]      # 0001 ... 2425

RENAME = {"Date": "match_date", "HomeTeam": "home_team", "AwayTeam": "away_team", "FTHG": "home_goals",
          "FTAG": "away_goals", "FTR": "result", "HTHG": "ht_home_goals", "HTAG": "ht_away_goals",
          "HTR": "ht_result", "Referee": "referee", "HS": "home_shots", "AS": "away_shots",
          "HST": "home_shots_on_target", "AST": "away_shots_on_target", "HF": "home_fouls", "AF": "away_fouls",
          "HC": "home_corners", "AC": "away_corners", "HY": "home_yellow", "AY": "away_yellow",
          "HR": "home_red", "AR": "away_red", "WHH": "odds_home", "WHD": "odds_draw", "WHA": "odds_away"}


def read_season(path):
    """Some season files have rows with more fields than the header, so extra trailing fields are cut off."""
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        rows = list(csv.reader(f))
    header = [h.strip() for h in rows[0]]
    body = [(r + [""] * len(header))[:len(header)] for r in rows[1:] if any(c.strip() for c in r)]
    return pd.DataFrame(body, columns=header).replace("", pd.NA)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data")
    ap.add_argument("--out", default="premier_league.db")
    args = ap.parse_args()
    cache = Path(args.cache) / "football"
    cache.mkdir(parents=True, exist_ok=True)

    frames = []
    for code in SEASONS:
        path = cache / f"E0_{code}.csv"
        if not path.exists():
            print("downloading season", code)
            urllib.request.urlretrieve(URL.format(code=code), path)
        df = read_season(path)
        df = df.dropna(subset=["HomeTeam", "AwayTeam", "FTHG"])
        df = df[[c for c in RENAME if c in df.columns]].rename(columns=RENAME)
        df.insert(0, "season", f"20{code[:2]}/{code[2:]}")
        frames.append(df)
    matches = pd.concat(frames, ignore_index=True)
    text_cols = {"season", "match_date", "home_team", "away_team", "result", "ht_result", "referee"}
    for col in matches.columns:
        if col not in text_cols:
            matches[col] = pd.to_numeric(matches[col], errors="coerce")
    # two-digit years in the early seasons (19/08/00), four-digit later: dayfirst handles both
    matches["match_date"] = pd.to_datetime(matches["match_date"], dayfirst=True, format="mixed").dt.strftime("%Y-%m-%d")
    for col in ("home_goals", "away_goals", "ht_home_goals", "ht_away_goals"):
        matches[col] = matches[col].astype("Int64")
    matches["referee"] = matches["referee"].str.strip()

    out = Path(args.out)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    matches.to_sql("matches", con, index=False)
    con.execute("CREATE INDEX ix_matches_season ON matches (season, match_date)")
    con.commit()
    print(f"wrote {out}: {len(matches):,} matches in {matches.season.nunique()} seasons")


if __name__ == "__main__":
    main()
