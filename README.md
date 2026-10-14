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

The app needs Flask, Flask-SQLAlchemy, pandas, and matplotlib (listed in `projekt/requirements.txt`):

```bash
cd projekt
pip install -r requirements.txt
python app.py
```

Then open `http://127.0.0.1:5000` and log in with `admin` / `admin` (a demo account created on first run: change the password before exposing the app anywhere).

### `mini_projects/` — Small Data-Viz Scripts

36 small projects: short Python scripts that pull data from a free public API or dataset and turn it into charts or models, SQL case studies (most of the numbers 09 to 19 and 28 to 36: plain SQL on SQLite with the queries in `.sql` files), and a small Streamlit dashboard ("Wikipedia Pulse"). See [`mini_projects/README.md`](mini_projects/README.md) for details on each script and how to run them.

## Note on repo size

The `.git` history is about 60 MB because an earlier commit contained `projekt/venv/`, a full Python virtual environment, plus compiled caches and the app's SQLite file. They are no longer tracked and `.gitignore` excludes them, but they still sit in the old history; a fresh clone downloads them unless it is shallow (`git clone --depth 1`). The app creates its database (`projekt/instance/database.db`) on first run.

## Requirements

- Python 3.9+
- `pip` for installing dependencies
