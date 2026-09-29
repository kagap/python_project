"""
The Keeling Curve — is atmospheric CO2 still climbing, and is it climbing
faster than it used to?

Plots the full Mauna Loa monthly CO2 record (the famous sawtooth-shaped
"Keeling Curve") since 1958, then compares how fast CO2 was rising per
decade.

Data source: NOAA Global Monitoring Laboratory — public CSV, no API key
required.
"""

import matplotlib.pyplot as plt
import pandas as pd

DATA_URL = "https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_mm_mlo.csv"


def main() -> None:
    df = pd.read_csv(DATA_URL, comment="#")
    df = df[df["average"] > 0]  # drop missing-month placeholders

    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.plot(df["decimal date"], df["average"], color="#a0aec0", linewidth=0.8, label="Monthly average")
    ax.plot(df["decimal date"], df["deseasonalized"], color="#e8a33d", linewidth=2, label="Trend (deseasonalized)")

    ax.set_title("The Keeling Curve: atmospheric CO2 at Mauna Loa since 1958")
    ax.set_ylabel("CO2 (parts per million)")
    ax.legend()
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig("chart.png", dpi=150)
    print("Saved chart.png")

    df["decade"] = (df["year"] // 10) * 10
    yearly = df.groupby("year")["average"].mean()
    annual_increase = yearly.diff()
    decade_avg_increase = annual_increase.groupby(yearly.index // 10 * 10).mean()

    print("\nAverage annual CO2 increase, by decade (ppm/year):")
    for decade, value in decade_avg_increase.items():
        if pd.notna(value):
            print(f"  {int(decade)}s: {value:.2f}")


if __name__ == "__main__":
    main()
