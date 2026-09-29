"""
Who Wins the Nobel Prize? — 120+ years of laureates, and how unevenly the
prize has been shared between men and women, and between categories.

Pulls every Nobel Prize laureate from the official Nobel Prize API, then
plots the share of prizes going to women per decade, plus a category
breakdown.

Data source: https://api.nobelprize.org — public, no API key required.
"""

import matplotlib.pyplot as plt
import pandas as pd
import requests

API = "https://api.nobelprize.org/2.1/laureates"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_laureates() -> list[dict]:
    response = requests.get(API, params={"limit": 1100}, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.json()["laureates"]


def main() -> None:
    laureates = fetch_laureates()

    rows = []
    for laureate in laureates:
        gender = laureate.get("gender")
        if gender not in ("male", "female"):
            continue  # skip organisations (Red Cross, UN agencies, ...)
        for prize in laureate.get("nobelPrizes", []):
            rows.append(
                {
                    "year": int(prize["awardYear"]),
                    "category": prize["category"]["en"],
                    "gender": gender,
                }
            )

    df = pd.DataFrame(rows)
    df["decade"] = (df["year"] // 10) * 10

    by_decade = df.groupby("decade")["gender"].apply(lambda s: (s == "female").mean() * 100)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5.5))

    ax1.plot(by_decade.index, by_decade.values, marker="o", color="#e8a33d")
    ax1.set_title("Share of Nobel laureates who are women, by decade")
    ax1.set_ylabel("% women")
    ax1.grid(alpha=0.25)

    by_category = df.groupby("category")["gender"].apply(lambda s: (s == "female").mean() * 100).sort_values()
    ax2.barh(by_category.index, by_category.values, color="#2b6cb0")
    ax2.set_title("Share of women, all-time, by category")
    ax2.set_xlabel("% women")

    fig.tight_layout()
    fig.savefig("chart.png", dpi=150)
    print("Saved chart.png")

    print(f"\nTotal laureates analysed: {len(df)}")
    print(f"Overall share of women: {(df['gender'] == 'female').mean() * 100:.1f}%")
    print(f"Most recent decade ({by_decade.index[-1]}s): {by_decade.iloc[-1]:.1f}% women")


if __name__ == "__main__":
    main()
