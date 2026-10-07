"""Is it getting warmer where you live? Fifteen years of weather in twelve cities, with honest uncertainty.

Plain-script version of the notebook. Set CLIMATE_DIR to a folder for the downloaded weather files.
"""


import json
import os
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
from scipy import stats

DATA_DIR = Path(os.environ.get("CLIMATE_DIR", "climate_data"))
DATA_DIR.mkdir(exist_ok=True)
AMBER, BLUE, GREY = "#e8a33d", "#2b6cb0", "#a0aec0"
plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
                     "grid.alpha": 0.25, "figure.dpi": 110})

CITIES = {"Warsaw": (52.23, 21.01), "London": (51.51, -0.13), "Berlin": (52.52, 13.40), "Madrid": (40.42, -3.70),
          "Rome": (41.90, 12.50), "Athens": (37.98, 23.73), "Stockholm": (59.33, 18.07), "Moscow": (55.76, 37.62),
          "New York": (40.71, -74.01), "Tokyo": (35.68, 139.69), "Sydney": (-33.87, 151.21), "Nairobi": (-1.29, 36.82)}

def load_city(name, lat, lon):
    path = DATA_DIR / f"{name.replace(' ', '_')}.json"
    if not path.exists():
        for attempt in range(10):
            try:
                r = requests.get("https://archive-api.open-meteo.com/v1/archive", timeout=60, params={
                    "latitude": lat, "longitude": lon, "start_date": "2010-01-01", "end_date": "2024-12-31",
                    "daily": "temperature_2m_mean,temperature_2m_max,temperature_2m_min,precipitation_sum", "timezone": "auto"})
            except requests.RequestException:
                time.sleep(30)
                continue
            if r.status_code == 429:                       # the API allows only a few requests a minute
                time.sleep(65)
                continue
            r.raise_for_status()
            path.write_text(json.dumps(r.json()["daily"]), encoding="utf-8")
            time.sleep(8)
            break
    return json.loads(path.read_text(encoding="utf-8"))

frames = []
for name, (lat, lon) in CITIES.items():
    d = load_city(name, lat, lon)
    frames.append(pd.DataFrame({"city": name, "date": pd.to_datetime(d["time"]), "tmean": d["temperature_2m_mean"], "tmax": d["temperature_2m_max"],
                                "tmin": d["temperature_2m_min"], "rain": d["precipitation_sum"]}))
weather = pd.concat(frames, ignore_index=True)
weather["year"] = weather.date.dt.year
weather["month"] = weather.date.dt.month
print(f"{len(weather):,} city-days, {weather.date.min():%Y-%m-%d} to {weather.date.max():%Y-%m-%d}, missing values: {int(weather.isna().sum().sum())}")
annual = weather.groupby("city").agg(mean_temp_c=("tmean", "mean"), hottest_day_c=("tmax", "max"), coldest_night_c=("tmin", "min"),
                                    rain_mm_per_year=("rain", lambda s: s.sum() / 15)).sort_values("mean_temp_c")
annual.round(1)

def harmonics(day_of_year, order=2):
    t = 2 * np.pi * np.asarray(day_of_year) / 365.25
    cols = [np.ones_like(t)]
    for k in range(1, order + 1):
        cols += [np.sin(k * t), np.cos(k * t)]
    return np.column_stack(cols)

fits, rows = {}, []
for city, g in weather.groupby("city"):
    X = harmonics(g.date.dt.dayofyear)
    beta, *_ = np.linalg.lstsq(X, g.tmean.to_numpy(), rcond=None)
    weather.loc[g.index, "seasonal"] = X @ beta
    cycle = harmonics(np.arange(1, 366)) @ beta
    rows.append({"city": city, "coldest_day_of_year": int(np.argmin(cycle)) + 1, "warmest_day_of_year": int(np.argmax(cycle)) + 1,
                 "seasonal_swing_c": cycle.max() - cycle.min(),
                 "share_explained": 1 - np.var(g.tmean - X @ beta) / np.var(g.tmean)})
weather["anomaly"] = weather.tmean - weather.seasonal
seasons = pd.DataFrame(rows).set_index("city").sort_values("seasonal_swing_c")
seasons.round(2)

fig, axes = plt.subplots(1, 2, figsize=(11.5, 4))
w = weather[(weather.city == "Warsaw") & (weather.year.between(2022, 2024))]
axes[0].plot(w.date, w.tmean, color=GREY, lw=0.8, label="Daily mean temperature")
axes[0].plot(w.date, w.seasonal, color=AMBER, lw=2.4, label="Typical year (harmonic fit)")
axes[0].set_ylabel("Degrees C")
axes[0].set_title("Warsaw, 2022-2024: the cycle and the weather around it", fontsize=10)
axes[0].legend(frameon=False, fontsize=8, loc="upper left")
axes[1].barh(seasons.index, seasons.seasonal_swing_c, color=BLUE)
for y, v in enumerate(seasons.seasonal_swing_c):
    axes[1].text(v + 0.2, y, f"{v:.1f}", va="center", fontsize=8)
axes[1].set_xlabel("Difference between the warmest and the coldest day of a typical year (degrees C)", fontsize=8)
axes[1].set_title("How big is the seasonal swing?", fontsize=10)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

yearly = weather.groupby(["city", "year"]).tmean.mean().unstack(0)
t_crit = stats.t.ppf(0.975, df=len(yearly) - 2)
rows = []
for city in yearly.columns:
    res = stats.linregress(yearly.index, yearly[city])
    rows.append({"city": city, "trend_c_per_decade": res.slope * 10, "ci_low": (res.slope - t_crit * res.stderr) * 10,
                 "ci_high": (res.slope + t_crit * res.stderr) * 10, "p_value": res.pvalue,
                 "warmest_year": int(yearly[city].idxmax()), "mean_2024_vs_2010_2019": yearly.loc[2024, city] - yearly.loc[2010:2019, city].mean()})
trend = pd.DataFrame(rows).set_index("city").sort_values("trend_c_per_decade")
trend["distinguishable_from_zero"] = (trend.ci_low > 0) | (trend.ci_high < 0)
print(f"{trend.distinguishable_from_zero.sum()} of {len(trend)} cities have a trend whose 95% interval excludes zero")
trend.round(3)

fig, axes = plt.subplots(3, 4, figsize=(12.5, 7.4), sharex=True)
for ax, city in zip(axes.ravel(), yearly.columns):
    row = trend.loc[city]
    res = stats.linregress(yearly.index, yearly[city])
    ax.plot(yearly.index, yearly[city], marker="o", ms=3, color=GREY, lw=1)
    ax.plot(yearly.index, res.intercept + res.slope * yearly.index, color=AMBER if row.trend_c_per_decade > 0 else BLUE, lw=2)
    ax.set_title(f"{city}: {row.trend_c_per_decade:+.2f} per decade", fontsize=9)
    ax.tick_params(labelsize=7)
fig.suptitle("Annual mean temperature (degrees C) with a straight-line trend", fontsize=11)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

fig, ax = plt.subplots(figsize=(8.5, 4.8))
y = np.arange(len(trend))
ax.errorbar(trend.trend_c_per_decade, y, xerr=[trend.trend_c_per_decade - trend.ci_low, trend.ci_high - trend.trend_c_per_decade],
            fmt="o", color=AMBER, ecolor=GREY, capsize=3)
ax.axvline(0, color="#4a5568", lw=1)
ax.set_yticks(y, trend.index)
ax.set_xlabel("Trend in annual mean temperature, degrees C per decade (95% interval)")
ax.set_title("Warming rate by city, 2010-2024")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

season_of = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
             6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}
w = weather.assign(season=weather.month.map(season_of), season_year=weather.year + (weather.month == 12).astype(int))
counts = w.groupby(["city", "season", "season_year"]).tmean.agg(["mean", "size"]).reset_index()
counts = counts[counts["size"] >= 88]                      # drop the incomplete first and last winters
rows = []
for (city, season), g in counts.groupby(["city", "season"]):
    res = stats.linregress(g.season_year, g["mean"])
    tc = stats.t.ppf(0.975, df=len(g) - 2)
    rows.append({"city": city, "season": season, "trend": res.slope * 10, "significant": (res.slope - tc * res.stderr > 0) or (res.slope + tc * res.stderr < 0)})
by_season = pd.DataFrame(rows)
grid = by_season.pivot(index="city", columns="season", values="trend")[["winter", "spring", "summer", "autumn"]].loc[trend.index]
sig = by_season.pivot(index="city", columns="season", values="significant")[["winter", "spring", "summer", "autumn"]].loc[trend.index]
print(f"{int(sig.values.sum())} of {sig.size} season-trends are significant at 5%; about {0.05 * sig.size:.1f} would be expected by chance alone")

fig, ax = plt.subplots(figsize=(7, 5.4))
im = ax.imshow(grid.values, cmap="RdBu_r", vmin=-3.5, vmax=3.5, aspect="auto")
ax.set_xticks(range(4), grid.columns)
ax.set_yticks(range(len(grid)), grid.index)
for i in range(grid.shape[0]):
    for j in range(grid.shape[1]):
        v = grid.values[i, j]
        ax.text(j, i, f"{v:+.1f}" + ("*" if sig.values[i, j] else ""), ha="center", va="center", fontsize=8,
                color="white" if abs(v) > 2.0 else "black")
ax.grid(False)
ax.set_title("Trend per decade by season (degrees C, * = interval excludes 0)", fontsize=10)
fig.colorbar(im, ax=ax, shrink=0.8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

def longest_run(mask):
    best = run = 0
    for v in mask:
        run = run + 1 if v else 0
        best = max(best, run)
    return best

def heatwave_days(mask, length=3):
    """Days that belong to a run of at least `length` consecutive hot days."""
    total = run = 0
    for v in list(mask) + [False]:
        if v:
            run += 1
        else:
            if run >= length:
                total += run
            run = 0
    return total

rows = []
for (city, year), g in weather.groupby(["city", "year"]):
    hot = (g.tmax >= 30).to_numpy()
    rows.append({"city": city, "year": year, "hot_days": hot.sum(), "heatwave_days": heatwave_days(hot),
                 "frost_days": int((g.tmin < 0).sum()), "rain_mm": g.rain.sum(), "heavy_rain_days": int((g.rain >= 10).sum())})
extremes = pd.DataFrame(rows)

def theil(metric):
    out = []
    for city, g in extremes.groupby("city"):
        slope, intercept, lo, hi = stats.theilslopes(g[metric], g.year, alpha=0.95)
        out.append({"city": city, f"{metric} per year, 2010": g[metric].iloc[:3].mean(), f"{metric} per year, 2022-24": g[metric].iloc[-3:].mean(),
                    "change per decade": slope * 10, "ci_low": lo * 10, "ci_high": hi * 10,
                    "significant": (lo > 0) or (hi < 0)})
    return pd.DataFrame(out).set_index("city")

hot_trend, frost_trend = theil("hot_days"), theil("frost_days")
hot_trend.rename(columns={"hot_days per year, 2010": "first 3 years (avg)", "hot_days per year, 2022-24": "last 3 years (avg)"}).round(1).sort_values("change per decade", ascending=False)

fig, axes = plt.subplots(1, 2, figsize=(12, 4.2))
for city, color in [("Madrid", "#c0392b"), ("Rome", AMBER), ("Athens", "#805ad5"), ("Warsaw", BLUE), ("Berlin", "#38a169"), ("London", GREY)]:
    g = extremes[extremes.city == city]
    axes[0].plot(g.year, g.hot_days, marker="o", ms=3, color=color, label=city)
axes[0].set_ylabel("Days with a maximum of 30 degrees C or more")
axes[0].set_title("Hot days per year", fontsize=10)
axes[0].legend(frameon=False, fontsize=8, ncol=2)
for city, color in [("Moscow", "#805ad5"), ("Stockholm", BLUE), ("Warsaw", AMBER), ("Berlin", "#38a169"), ("New York", "#c0392b")]:
    g = extremes[extremes.city == city]
    axes[1].plot(g.year, g.frost_days, marker="o", ms=3, color=color, label=city)
axes[1].set_ylabel("Days with a minimum below 0 degrees C")
axes[1].set_title("Frost days per year", fontsize=10)
axes[1].legend(frameon=False, fontsize=8, ncol=2)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
pd.concat([frost_trend[["change per decade", "ci_low", "ci_high", "significant"]].add_prefix("frost: ")], axis=1).round(1).sort_values("frost: change per decade").head(6)

rank_2024 = yearly.rank(ascending=False).loc[2024].astype(int).rename("2024 rank (1 = warmest of 15)")
warmest = yearly.idxmax().rename("warmest year")
anomaly_2024 = (yearly.loc[2024] - yearly.loc[2010:2019].mean()).rename("2024 minus 2010-2019 average (C)")
in_context = pd.concat([rank_2024, warmest, anomaly_2024], axis=1).sort_values("2024 rank (1 = warmest of 15)")
print(f"2024 was the warmest of the 15 years in {(rank_2024 == 1).sum()} of {len(rank_2024)} cities")
in_context.round(2)

def theil_metric(metric):
    out = []
    for city, g in extremes.groupby("city"):
        slope, _, lo, hi = stats.theilslopes(g[metric], g.year, alpha=0.95)
        out.append({"city": city, "average per year": g[metric].mean(), "change per decade": slope * 10, "ci_low": lo * 10, "ci_high": hi * 10,
                    "significant": (lo > 0) or (hi < 0)})
    return pd.DataFrame(out).set_index("city")

rain_trend = theil_metric("rain_mm")
print(f"annual rainfall: {rain_trend.significant.sum()} of {len(rain_trend)} cities have a trend distinguishable from zero")
rain_trend.round(0).sort_values("change per decade")
