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
