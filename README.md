# Python Project

A small collection of Python data projects: a Flask web app that analyzes Polish wage and inflation statistics, plus a set of standalone mini data-visualization scripts.

## 📌 What's Inside

### `projekt/` — Wage Analysis Web App

A Flask application (`projekt/app.py`) that visualizes average monthly gross wages in the Polish enterprise sector using official statistics from [stat.gov.pl](https://stat.gov.pl/obszary-tematyczne/rynek-pracy/pracujacy-zatrudnieni-wynagrodzenia-koszty-pracy/wyrownania-sezonowe-przecietne-zatrudnienie-i-przecietne-miesieczne-wynagrodzenie,20,7.html). It's a small student/team project (see `projekt/readme.txt` for the original project brief, in Polish).

- Requires a username/password login (backed by a SQLite database via Flask-SQLAlchemy; a default `admin`/`admin` user is auto-created on first run).
- After logging in, `/analysis` reads two CSV datasets — monthly gross wages and annual consumer price indices (inflation) — and renders four `matplotlib` charts (via Jinja templates in `projekt/templates/`):
  - average yearly wage trend,
  - year-over-year wage growth by month,
  - median wage by year,
  - nominal vs. inflation-adjusted ("real") wage over time.
- Data files used: `projekt/2._przecietne_miesieczne_wynagrodzenia_brutto_w_sektorze_przedsiebiorstw_-_dane_miesieczne.csv` and `projekt/roczne_wskazniki_cen_towarow_i_uslug_konsumpcyjnych_od_1950_roku_2.csv`.

#### Running `projekt/` locally

The app needs Flask, Flask-SQLAlchemy, pandas, and matplotlib (there's no `requirements.txt` committed for this part of the repo, so install them manually):

```bash
cd projekt
pip install flask flask_sqlalchemy pandas matplotlib
python app.py
```

Then open `http://127.0.0.1:5000` and log in with `admin` / `admin`.

### `mini_projects/` — Small Data-Viz Scripts

A set of short, single-purpose Python scripts, each pulling data from a free public API/dataset and turning it into one chart (currency volatility, tallest nations, internet adoption), plus a small Streamlit dashboard ("Wikipedia Pulse") for exploring Wikipedia's most-read articles. See [`mini_projects/README.md`](mini_projects/README.md) for details on each script and how to run them.

## ⚠️ Note on repo size

This repository is larger than expected for its content (~60 MB `.git` history). That's because `projekt/venv/` — a full Python virtual environment with all installed packages (Flask, pandas, matplotlib, numpy, etc.) — was committed directly into the repository instead of being excluded. The top-level `.gitignore` only ignores `.venv/`, not `projekt/venv/`, so it slipped through. It hasn't been removed here; flagging it in case it's worth cleaning up (e.g. removing it from history) later.

## Requirements

- Python 3.9+ (the committed `projekt/venv` was built against 3.9)
- `pip` for installing dependencies
