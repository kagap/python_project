"""
Tallest Nations — which countries produce the tallest people, and how big is
the gap between men and women within each of them?

Pulls Our World in Data's average-height-by-year-of-birth series for men and
women, takes the most recent birth cohort with good coverage, and plots the
15 tallest countries for men as a male-vs-female dumbbell chart.

Data source: Our World in Data (https://ourworldindata.org/human-height) —
public CSV export, no API key required.
"""

import matplotlib.pyplot as plt
import pandas as pd
import requests

MEN_URL = "https://ourworldindata.org/grapher/average-height-of-men.csv"
WOMEN_URL = "https://ourworldindata.org/grapher/average-height-of-women.csv"
TOP_N = 15
HEADERS = {"User-Agent": "Mozilla/5.0"}


def load(url: str, value_col: str) -> pd.DataFrame:
    df = pd.read_csv(url, storage_options=HEADERS)
    df = df.rename(columns={df.columns[-1]: value_col})
    return df[df["Code"].notna() & ~df["Code"].str.startswith("OWID")]


def main() -> None:
    men = load(MEN_URL, "male_cm")
    women = load(WOMEN_URL, "female_cm")

    merged = men.merge(women, on=["Entity", "Code", "Year"])

    # Use the most recent birth-year cohort with solid country coverage.
    latest_year = merged.groupby("Year").size().loc[lambda s: s > 100].index.max()
    latest = merged[merged["Year"] == latest_year]

    top = latest.sort_values("male_cm", ascending=False).head(TOP_N).sort_values("male_cm")

    fig, ax = plt.subplots(figsize=(9, 7))
    ax.hlines(top["Entity"], top["female_cm"], top["male_cm"], color="lightgray", linewidth=2, zorder=1)
    ax.scatter(top["male_cm"], top["Entity"], color="#2b6cb0", label="Men", zorder=2, s=60)
    ax.scatter(top["female_cm"], top["Entity"], color="#d53f8c", label="Women", zorder=2, s=60)

    ax.set_title(f"The world's tallest nations (birth cohort of {latest_year})")
    ax.set_xlabel("Average adult height (cm)")
    ax.legend()
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig("chart.png", dpi=150)
    print("Saved chart.png")

    gap = (top["male_cm"] - top["female_cm"]).sort_values(ascending=False)
    print(f"\nBiggest male-female height gap among the top {TOP_N}:")
    for entity, value in gap.head(3).items():
        print(f"  {top.loc[entity, 'Entity']}: {value:.1f} cm")


if __name__ == "__main__":
    main()
