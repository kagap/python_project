"""
Currency Volatility — how bumpy has the zloty's ride against USD, EUR and GBP
really been?

Pulls daily NBP (Narodowy Bank Polski) reference rates over the last few years
and plots each currency's rolling 30-day volatility (annualised standard
deviation of daily returns), then prints the five most turbulent single days.

Data source: NBP Web API (https://api.nbp.pl) — public, no API key required.
"""

import datetime as dt

import matplotlib.pyplot as plt
import pandas as pd
import requests

NBP_API = "https://api.nbp.pl/api/exchangerates/rates/A/{code}/{start}/{end}/?format=json"
CURRENCIES = ["USD", "EUR", "GBP"]
YEARS_BACK = 5


def fetch_rates(code: str, years_back: int = YEARS_BACK) -> pd.Series:
    """Fetch daily NBP mid rates for `code`, working around the API's 367-day range limit."""
    end = dt.date.today()
    start = end.replace(year=end.year - years_back)

    frames = []
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + dt.timedelta(days=366), end)
        url = NBP_API.format(code=code, start=chunk_start.isoformat(), end=chunk_end.isoformat())
        response = requests.get(url, timeout=15)
        if response.ok:
            frames.append(pd.DataFrame(response.json()["rates"]))
        chunk_start = chunk_end + dt.timedelta(days=1)

    df = pd.concat(frames, ignore_index=True)
    df["effectiveDate"] = pd.to_datetime(df["effectiveDate"])
    return df.set_index("effectiveDate")["mid"].rename(code)


def main() -> None:
    rates = pd.concat([fetch_rates(code) for code in CURRENCIES], axis=1).sort_index()

    daily_returns = rates.pct_change()
    volatility = daily_returns.rolling(30).std() * (252 ** 0.5) * 100  # annualised, in %

    fig, ax = plt.subplots(figsize=(10, 5.5))
    for code in CURRENCIES:
        ax.plot(volatility.index, volatility[code], label=code, linewidth=1.6)

    ax.set_title(f"How bumpy has the zloty's ride been? ({YEARS_BACK}-year rolling volatility)")
    ax.set_ylabel("Annualised 30-day volatility (%)")
    ax.legend()
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig("chart.png", dpi=150)
    print("Saved chart.png")

    biggest_moves = daily_returns.abs().stack().sort_values(ascending=False).head(5)
    print("\nBiggest single-day moves:")
    for (date, code), move in biggest_moves.items():
        print(f"  {date.date()}  {code}  {move * 100:+.2f}%")


if __name__ == "__main__":
    main()
