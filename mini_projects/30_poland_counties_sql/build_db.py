"""Load county-level (powiat) statistics for Poland into a SQLite database.

usage: python build_db.py [--cache DIR] [--out poland_counties.db]

Source: GUS Local Data Bank (BDL) API, https://api.stat.gov.pl/Home/BdlApi (public statistics, free to reuse with
attribution). Seven indicators for all 380 powiats (counties, including cities with powiat status) are fetched, four
pages of 100 counties per indicator (about 30 requests, about 2 MB). The anonymous API allows 1,000 requests per 12 hours
and answers 429 when the quota is exhausted, so the script waits when asked to, and caches every response in --cache.

Tables:
  powiats     one row per county (id, name without the 'Powiat' prefix, voivodship, is_city = city with powiat status)
  indicators  one row per indicator (id, name, unit)
  facts       one row per county, indicator and year (value)
  panel       a view that pivots facts into one row per county and year, one column per indicator
  series_breaks  a view listing county-years where the population jumped by more than 8% (border changes)
"""
import argparse
import hashlib
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://bdl.stat.gov.pl/api/v1/"
YEARS = list(range(2005, 2026))

# name used in the database -> (BDL variable id, label, unit)
INDICATORS = {
    "wage":            (64428,   "Average monthly gross wage and salary", "PLN"),
    "unemployment":    (60270,   "Registered unemployment rate", "%"),
    "population":      (1645341, "Population (thousands)", "thousand persons"),
    "density":         (60559,   "Population per km2", "persons per km2"),
    "median_age":      (746289,  "Median age of the population", "years"),
    "urbanization":    (1725015, "Urbanization rate", "%"),
    "emp_agriculture": (1725160, "Employed persons in agriculture (section A)", "% of employed"),
    "emp_industry":    (1725161, "Employed persons in industry (sections B-E)", "% of employed"),
    "emp_construction": (1725162, "Employed persons in construction (section F)", "% of employed"),
    "emp_services":    (1725163, "Employed persons in market services (G-N, R-U)", "% of employed"),
    "emp_public":      (1725164, "Employed persons in public services (O-Q)", "% of employed"),
}

VOIVODSHIPS = {
    "02": "Dolnośląskie", "04": "Kujawsko-Pomorskie", "06": "Lubelskie", "08": "Lubuskie", "10": "Łódzkie",
    "12": "Małopolskie", "14": "Mazowieckie", "16": "Opolskie", "18": "Podkarpackie", "20": "Podlaskie",
    "22": "Pomorskie", "24": "Śląskie", "26": "Świętokrzyskie", "28": "Warmińsko-Mazurskie", "30": "Wielkopolskie",
    "32": "Zachodniopomorskie",
}


def fetch(path, params, cache):
    q = urllib.parse.urlencode(params, doseq=True)
    url = f"{BASE}{path}?lang=en&format=json&{q}"
    f = cache / (hashlib.md5(url.encode()).hexdigest() + ".json")
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    while True:
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.load(r)
            f.write_text(json.dumps(data), encoding="utf-8")
            time.sleep(0.4)
            return data
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            wait = int(re.search(r"\d+", e.headers.get("Retry-After", "60")).group()) + 5
            print(f"quota reached, waiting {wait}s", flush=True)
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError) as e:
            print("network error, retrying:", e, flush=True)
            time.sleep(10)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data")
    ap.add_argument("--out", default="poland_counties.db")
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)

    powiats, facts = {}, []
    for name, (var_id, label, unit) in INDICATORS.items():
        page = 0
        while True:
            d = fetch(f"data/by-variable/{var_id}", {"unit-level": 5, "year": YEARS, "page-size": 100, "page": page}, cache)
            for r in d["results"]:
                powiats[r["id"]] = r["name"]
                # GUS reports a suppressed or missing value as 0 for some counties (e.g. a wage of 0.0 PLN), so a 0 is stored as NULL
                facts += [(r["id"], name, int(v["year"]), v["val"]) for v in r["values"]
                          if v.get("val") is not None and not (v["val"] == 0 and name in ("wage", "population", "density"))]
            if not d["links"].get("next"):
                break
            page += 1
        print(f"{name}: done", flush=True)

    out = Path(args.out)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    con.execute("CREATE TABLE powiats (powiat_id TEXT PRIMARY KEY, name TEXT, voivodship TEXT, is_city INTEGER)")
    con.execute("CREATE TABLE indicators (indicator TEXT PRIMARY KEY, label TEXT, unit TEXT)")
    con.execute("CREATE TABLE facts (powiat_id TEXT, indicator TEXT, year INTEGER, value REAL, "
                "PRIMARY KEY (powiat_id, indicator, year))")
    for pid, nm in powiats.items():
        city = nm.startswith("City with powiat status")
        clean = re.sub(r"^(Powiat|City with powiat status( Capital City)?)\s+", "", nm)      # "Powiat krakowski" -> "krakowski"
        clean = clean.replace(" since 2013", "")                                              # Wałbrzych became a city county in 2013
        con.execute("INSERT INTO powiats VALUES (?, ?, ?, ?)", (pid, clean, VOIVODSHIPS.get(pid[2:4]), int(city)))
    con.executemany("INSERT INTO indicators VALUES (?, ?, ?)", [(k, v[1], v[2]) for k, v in INDICATORS.items()])
    con.executemany("INSERT OR REPLACE INTO facts VALUES (?, ?, ?, ?)", facts)
    cols = ",\n           ".join(f"MAX(CASE WHEN indicator = '{k}' THEN value END) AS {k}" for k in INDICATORS)
    con.execute(f"CREATE VIEW panel AS\n    SELECT powiat_id, year,\n           {cols}\n    FROM facts\n    GROUP BY powiat_id, year")
    # Borders change (a city leaves or joins a county, gminas move), which shows up as a jump in population from one year to the next.
    # Counties with such a break are excluded from the comparisons between years.
    con.execute("""CREATE VIEW series_breaks AS
    SELECT powiat_id, year
    FROM (SELECT powiat_id, year, population,
                 LAG(population) OVER (PARTITION BY powiat_id ORDER BY year) AS previous
          FROM panel)
    WHERE ABS(population / previous - 1) > 0.08""")
    con.commit()
    print(f"wrote {out}: {len(powiats)} powiats, {len(facts):,} facts")


if __name__ == "__main__":
    main()
