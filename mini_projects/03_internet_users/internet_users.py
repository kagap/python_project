"""
How Connected Is the World? — tracking the internet adoption curve for a
handful of countries, from the dial-up era to (nearly) universal access.

Pulls Our World in Data's share of the population using the internet, plots
the adoption curve for a mix of rich, poor and mid-income countries, and
prints today's most- and least-connected countries.

Data source: Our World in Data (https://ourworldindata.org/internet) —
public CSV export, no API key required.
"""

import matplotlib.pyplot as plt
import pandas as pd

DATA_URL = "https://ourworldindata.org/grapher/share-of-individuals-using-the-internet.csv"
VALUE_COL = "Share of the population using the Internet"
HEADERS = {"User-Agent": "Mozilla/5.0"}
SPOTLIGHT_COUNTRIES = ["Poland", "United States", "South Korea", "Nigeria", "India", "Iceland"]


def main() -> None:
    df = pd.read_csv(DATA_URL, storage_options=HEADERS)
    countries = df[df["Code"].notna() & ~df["Code"].str.startswith(("OWID", "WB_"))]

    fig, ax = plt.subplots(figsize=(9.5, 6))
    for country in SPOTLIGHT_COUNTRIES:
        series = countries[countries["Entity"] == country].sort_values("Year")
        ax.plot(series["Year"], series[VALUE_COL], label=country, linewidth=2)

    ax.set_title("How connected is the world? Internet adoption over time")
    ax.set_ylabel("Share of population online (%)")
    ax.set_xlim(1995, countries["Year"].max())
    ax.legend()
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig("chart.png", dpi=150)
    print("Saved chart.png")

    # Some years only have a handful of early-reporting countries; use the
    # most recent year with broad coverage for a fair top/bottom comparison.
    latest_year = countries.groupby("Year").size().loc[lambda s: s > 150].index.max()
    latest = countries[countries["Year"] == latest_year]
    top = latest.sort_values(VALUE_COL, ascending=False).head(5)
    bottom = latest.sort_values(VALUE_COL).head(5)

    print(f"\nMost connected countries in {latest_year}:")
    for _, row in top.iterrows():
        print(f"  {row['Entity']}: {row[VALUE_COL]:.1f}%")

    print(f"\nLeast connected countries in {latest_year}:")
    for _, row in bottom.iterrows():
        print(f"  {row['Entity']}: {row[VALUE_COL]:.1f}%")


if __name__ == "__main__":
    main()
