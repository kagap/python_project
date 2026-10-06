# Mini Data Projects

Small, single-story Python scripts, each pulling live data from a free public
API/dataset and turning it into one chart — the Python-page counterpart to the
Power BI and Tableau projects on the portfolio.

Setup (once):

```bash
python -m venv .venv
.venv/Scripts/activate        # or: source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
```

## Static-chart scripts

Run any of them from inside their folder — each writes a `chart.png`.

| Project | What it shows | Data source |
|---|---|---|
| [01_currency_volatility](01_currency_volatility/currency_volatility.py) | How bumpy the zloty's ride against USD/EUR/GBP has been over 5 years | NBP Web API |
| [02_tallest_nations](02_tallest_nations/tallest_nations.py) | The world's 15 tallest countries, men vs women | Our World in Data |
| [03_internet_users](03_internet_users/internet_users.py) | Internet adoption curves for 6 countries since 1995 | Our World in Data |
| [05_keeling_curve](05_keeling_curve/keeling_curve.py) | Atmospheric CO2 since 1958, and how much faster it's rising each decade | NOAA GML |
| [06_nobel_prizes](06_nobel_prizes/nobel_prizes.py) | Share of Nobel laureates who are women, over time and by category | Nobel Prize API |
| [07_earthquakes](07_earthquakes/earthquakes.py) | M5.0+ earthquakes per year worldwide since 1990 | USGS Earthquake API |
| [08_late_delivery_model](08_late_delivery_model/late_delivery_model.py) | Machine-learning model predicting which Olist orders will arrive late (time-based validation, no leakage). Needs the Kaggle CSV files, point `OLIST_DIR` at them | Kaggle (Olist) |
| [09_chinook_sql](09_chinook_sql/chinook_sql.py) | A music store in plain SQL: ten business questions with window functions, a recursive CTE, cohort retention and RFM. The queries are also in [chinook_queries.sql](09_chinook_sql/chinook_queries.sql). Needs `Chinook_Sqlite.sqlite`, point `CHINOOK_DB` at it | [Chinook database](https://github.com/lerocha/chinook-database/releases) |
| [10_online_retail_sql](10_online_retail_sql/online_retail.py) | Two years of an online gift shop in plain SQL: seasonality, customer Pareto, RFM, cohort retention, market basket. Run `build_db.py` first | UCI (Online Retail II) |
| [11_chicago_crime_sql](11_chicago_crime_sql/chicago_crime.py) | A year of crime in Chicago in plain SQL: arrest rates, neighbourhoods, unusual days, hot streaks, seasonality. Run `build_db.py` first | City of Chicago open data |
| [12_nyc311_sql](12_nyc311_sql/nyc311.py) | What New Yorkers complain about, in plain SQL: medians and percentiles from window functions, complaint clocks, borough fingerprints. Run `build_db.py` first | NYC Open Data |
| [13_world_bank_sql](13_world_bank_sql/world_bank.py) | 60 years of countries getting richer and healthier, in plain SQL: growth indices, correlation from sums, rank climbers. Run `build_db.py` first | World Bank API |
| [14_nyc_taxi_sql](14_nyc_taxi_sql/nyc_taxi.py) | A month of New York yellow cabs in plain SQL: week-on-week change with `LAG`, rush hours, airport runs, tipping, traffic speed. Run `build_db.py` first (needs `pyarrow`) | NYC TLC trip records |
| [15_uk_road_safety_sql](15_uk_road_safety_sql/uk_road_safety.py) | A year of road collisions in Britain in plain SQL: three linked tables, severity by speed limit, light, vehicle and age. Run `build_db.py` first | UK Department for Transport |
| [16_eurostat_sql](16_eurostat_sql/eurostat_europe.py) | Jobs and prices across Europe in plain SQL: crisis peaks, Poland against the EU, the 2022 price shock, longest falling streaks. Run `build_db.py` first | Eurostat |
| [17_premier_league_sql](17_premier_league_sql/premier_league.py) | 25 seasons of the Premier League in plain SQL: rebuilt league tables, title races, unbeaten runs, comebacks, odds calibration, referees. Run `build_db.py` first | football-data.co.uk |
| [18_nyc_restaurants_sql](18_nyc_restaurants_sql/nyc_restaurants.py) | New York restaurant inspections in plain SQL: grades by borough, common violations, medians per cuisine, chains, closures, recovery after a failed inspection. Run `build_db.py` first | NYC Open Data |
| [19_movielens_sql](19_movielens_sql/movielens.py) | 100,000 movie ratings in plain SQL: a weighted rating that beats one-vote wonders, genres, user concentration with `NTILE`, release decades, divisive films. Run `build_db.py` first | GroupLens (MovieLens) |
| [20_football_elo](20_football_elo/elo_football.py) | Elo ratings for Europe's top five leagues turned into match probabilities, tested on unseen seasons against Bet365's odds. Downloads its data (about 9 MB) on first run | football-data.co.uk |
| [21_lastfm_recommender](21_lastfm_recommender/lastfm_recommender.py) | A music recommender three ways (popularity, item-item filtering, implicit ALS written in NumPy), scored on hidden listening. Downloads its data (about 2.6 MB) on first run | GroupLens (Last.fm) |

```bash
cd 01_currency_volatility
python currency_volatility.py
```

## Interactive dashboard

[04_wiki_trends_dashboard](04_wiki_trends_dashboard/app.py) — "Wikipedia Pulse":
pick a Wikipedia edition and a date to see the day's most-read articles, then
trace any of them over the last 30 days. Built with Streamlit; free to deploy
on [Streamlit Community Cloud](https://streamlit.io/cloud) since it needs no
API key or secrets — just point it at `mini_projects/04_wiki_trends_dashboard/app.py`.

```bash
cd 04_wiki_trends_dashboard
pip install -r requirements.txt
streamlit run app.py
```
