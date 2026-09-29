"""
Is the Earth Getting Shakier? — counting significant earthquakes (M5.0+)
per year since 1990 using the USGS global catalogue.

Data source: USGS Earthquake Hazards Program — public API, no API key
required.
"""

import time

import matplotlib.pyplot as plt
import requests

API = "https://earthquake.usgs.gov/fdsnws/event/1/count"
HEADERS = {"User-Agent": "Mozilla/5.0"}
START_YEAR = 1990
END_YEAR = 2025
MIN_MAGNITUDE = 5.0


def quakes_in_year(year: int) -> int:
    params = {
        "starttime": f"{year}-01-01",
        "endtime": f"{year + 1}-01-01",
        "minmagnitude": MIN_MAGNITUDE,
    }
    response = requests.get(API, params=params, headers=HEADERS, timeout=20)
    response.raise_for_status()
    return int(response.text)


def main() -> None:
    years = list(range(START_YEAR, END_YEAR + 1))
    counts = []
    for year in years:
        counts.append(quakes_in_year(year))
        time.sleep(0.3)

    mean_count = sum(counts) / len(counts)

    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.bar(years, counts, color="#2b6cb0")
    ax.axhline(mean_count, color="#e8a33d", linewidth=2, linestyle="--", label=f"{START_YEAR}-{END_YEAR} average")

    ax.set_title(f"Earthquakes of magnitude {MIN_MAGNITUDE}+ per year, worldwide")
    ax.set_ylabel("Number of earthquakes")
    ax.legend()
    fig.tight_layout()
    fig.savefig("chart.png", dpi=150)
    print("Saved chart.png")

    busiest_year = years[counts.index(max(counts))]
    print(f"\nAverage M{MIN_MAGNITUDE}+ earthquakes per year: {mean_count:.0f}")
    print(f"Busiest year: {busiest_year} ({max(counts)} earthquakes)")
    print(f"Quietest year: {years[counts.index(min(counts))]} ({min(counts)} earthquakes)")


if __name__ == "__main__":
    main()
