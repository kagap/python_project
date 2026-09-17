"""
Wikipedia Pulse — an interactive dashboard answering "what is the world
reading about right now?" using Wikimedia's public pageviews API (no key
required).

Run locally:
    streamlit run app.py

Deploy for free on Streamlit Community Cloud by pointing it at this file.
"""

import datetime as dt

import pandas as pd
import requests
import streamlit as st

API_BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews"
USER_AGENT = "wikipedia-pulse-portfolio-demo/1.0 (contact: k.molasy.km@gmail.com)"
PROJECTS = {
    "English": "en.wikipedia",
    "Polish": "pl.wikipedia",
    "German": "de.wikipedia",
    "French": "fr.wikipedia",
    "Spanish": "es.wikipedia",
}
NOISE_PREFIXES = ("Special:", "Wikipedia:", "Portal:", "Main_Page", "File:", "Category:")

st.set_page_config(page_title="Wikipedia Pulse", page_icon="📈", layout="wide")


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_top_articles(project: str, date: dt.date) -> pd.DataFrame:
    url = f"{API_BASE}/top/{project}/all-access/{date:%Y/%m/%d}"
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    response.raise_for_status()
    articles = response.json()["items"][0]["articles"]
    df = pd.DataFrame(articles)
    return df[~df["article"].str.startswith(NOISE_PREFIXES)]


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_article_trend(project: str, article: str, days: int) -> pd.DataFrame:
    end = dt.date.today() - dt.timedelta(days=2)
    start = end - dt.timedelta(days=days)
    url = (
        f"{API_BASE}/per-article/{project}/all-access/all-agents/"
        f"{article}/daily/{start:%Y%m%d}/{end:%Y%m%d}"
    )
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    response.raise_for_status()
    df = pd.DataFrame(response.json()["items"])
    df["date"] = pd.to_datetime(df["timestamp"], format="%Y%m%d%H")
    return df[["date", "views"]].set_index("date")


st.title("📈 Wikipedia Pulse")
st.caption("What is the world reading about right now? Live data from the Wikimedia pageviews API.")

with st.sidebar:
    st.header("Settings")
    language = st.selectbox("Wikipedia edition", list(PROJECTS.keys()))
    project = PROJECTS[language]
    date = st.date_input(
        "Date",
        value=dt.date.today() - dt.timedelta(days=2),
        max_value=dt.date.today() - dt.timedelta(days=1),
    )
    top_n = st.slider("How many articles?", min_value=5, max_value=30, value=15)

try:
    top_articles = fetch_top_articles(project, date).head(top_n)
except requests.HTTPError:
    st.error("No data available for that date yet — try picking an earlier day.")
    st.stop()

col1, col2 = st.columns([3, 2])

with col1:
    st.subheader(f"Most-read articles on {language} Wikipedia — {date:%d %b %Y}")
    chart_data = top_articles.set_index("article")["views"].iloc[::-1]
    st.bar_chart(chart_data, horizontal=True)

with col2:
    st.subheader("Trend spotlight")
    article_choice = st.selectbox("Pick an article to trace its last 30 days", top_articles["article"])
    trend = fetch_article_trend(project, article_choice, days=30)
    st.line_chart(trend)
    st.metric("Views on selected day", f"{top_articles.set_index('article').loc[article_choice, 'views']:,}")

st.caption("Source: Wikimedia Foundation Pageviews API — wikimedia.org/api/rest_v1/metrics/pageviews")
