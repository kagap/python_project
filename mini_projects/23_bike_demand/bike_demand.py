"""How many bikes will Washington rent tomorrow? Hourly demand forecasting with prediction intervals.

Plain-script version of the notebook. Set BIKE_DIR to a folder for the downloaded zip.
"""


import io
import os
import urllib.request
import zipfile
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance

DATA_DIR = Path(os.environ.get("BIKE_DIR", "bike_data"))
DATA_DIR.mkdir(exist_ok=True)
AMBER, BLUE, GREY = "#e8a33d", "#2b6cb0", "#a0aec0"
plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
                     "grid.alpha": 0.25, "figure.dpi": 110})

zip_path = DATA_DIR / "bike_sharing.zip"
if not zip_path.exists():
    urllib.request.urlretrieve("https://archive.ics.uci.edu/static/public/275/bike+sharing+dataset.zip", zip_path)
with zipfile.ZipFile(zip_path) as z:
    raw = pd.read_csv(z.open("hour.csv"), parse_dates=["dteday"])
raw["time"] = raw.dteday + pd.to_timedelta(raw.hr, unit="h")
raw = raw.set_index("time")

full_index = pd.date_range(raw.index.min(), raw.index.max(), freq="h")
missing = full_index.difference(raw.index)
print(f"{len(raw):,} hours in the file, {len(missing)} missing; {np.mean(missing.hour < 6):.0%} of the missing hours are between midnight and 6 a.m.")

d = raw.reindex(full_index)
d[["cnt", "casual", "registered"]] = d[["cnt", "casual", "registered"]].fillna(0)
d[["weathersit", "temp", "atemp", "hum", "windspeed"]] = d[["weathersit", "temp", "atemp", "hum", "windspeed"]].ffill()
d["hour"] = d.index.hour
d["dow"] = d.index.dayofweek
d["month"] = d.index.month
d["holiday"] = d.holiday.fillna(0).astype(int)
d["workingday"] = ((d.dow < 5) & (d.holiday == 0)).astype(int)
d["weather"] = d.weathersit.astype(int)                  # 1 clear, 2 mist, 3 light rain or snow, 4 heavy rain or snow
d["temp_c"] = d.temp * 41
d["feels_c"] = d.atemp * 50
d["humidity"] = d.hum * 100
d["wind"] = d.windspeed * 67
d[["cnt", "temp_c", "humidity", "wind"]].describe().round(1).T

fig, axes = plt.subplots(1, 3, figsize=(12, 3.8))

profile = d.groupby(["workingday", "hour"]).cnt.mean().unstack(0)
axes[0].plot(profile.index, profile[1], color=AMBER, lw=2.2, label="Working day")
axes[0].plot(profile.index, profile[0], color=BLUE, lw=2.2, label="Weekend or holiday")
axes[0].set_xlabel("Hour of day")
axes[0].set_ylabel("Average rentals per hour")
axes[0].set_title("Commuter peaks on working days", fontsize=10)
axes[0].legend(frameon=False, fontsize=8)

monthly = d.groupby([d.index.year, d.index.month]).cnt.sum().unstack(0) / 1000
axes[1].plot(monthly.index, monthly[2011], marker="o", ms=4, color=GREY, label="2011")
axes[1].plot(monthly.index, monthly[2012], marker="o", ms=4, color=AMBER, label="2012")
axes[1].set_xlabel("Month")
axes[1].set_ylabel("Rentals (thousand)")
axes[1].set_title("Seasonality, and a growing system", fontsize=10)
axes[1].legend(frameon=False, fontsize=8)

bins = pd.cut(d.temp_c, np.arange(-5, 45, 5))
by_temp = d.groupby(bins, observed=True).cnt.mean()
axes[2].bar([f"{int(i.left)}" for i in by_temp.index], by_temp.values, color=AMBER)
axes[2].set_xlabel("Temperature (from, degrees C)")
axes[2].set_ylabel("Average rentals per hour")
axes[2].set_title("Warmer weather, more rentals", fontsize=10)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
pd.DataFrame({"2011": [d[d.index.year == 2011].cnt.sum()], "2012": [d[d.index.year == 2012].cnt.sum()]},
             index=["rentals"]).assign(growth=lambda x: x["2012"] / x["2011"] - 1).round(3)

weather = d.groupby("weather").agg(hours=("cnt", "size"), avg_rentals=("cnt", "mean"), avg_temp=("temp_c", "mean"))
weather.index = weather.index.map({1: "1 clear", 2: "2 mist or cloud", 3: "3 light rain or snow", 4: "4 heavy rain or snow"})
weather.round(1)

# demand features that only use the past (nothing from the forecast day itself)
d["lag_24"] = d.cnt.shift(24)
d["lag_48"] = d.cnt.shift(48)
d["lag_168"] = d.cnt.shift(168)
d["same_hour_7d"] = sum(d.cnt.shift(24 * k) for k in range(1, 8)) / 7
daily = d.cnt.resample("D").sum()
d["yesterday_total"] = daily.shift(1).reindex(d.index.normalize()).to_numpy()
d["week_avg_total"] = daily.rolling(7).mean().shift(1).reindex(d.index.normalize()).to_numpy()

CALENDAR_WEATHER = ["hour", "dow", "month", "workingday", "holiday", "weather", "temp_c", "feels_c", "humidity", "wind"]
LAGS = ["lag_24", "lag_48", "lag_168", "same_hour_7d", "yesterday_total", "week_avg_total"]

ready = d.dropna(subset=LAGS).copy()                       # the first week has no history
train = ready[ready.index < "2012-04-01"]
valid = ready[(ready.index >= "2012-04-01") & (ready.index < "2012-07-01")]
train_valid = ready[ready.index < "2012-07-01"]
test = ready[ready.index >= "2012-07-01"]
print(f"train {len(train):,} h | validation {len(valid):,} h | test {len(test):,} h ({test.index.min():%d %b %Y} to {test.index.max():%d %b %Y})")

def score(y, pred):
    err = y - pred
    return {"MAE": np.abs(err).mean(),
            "RMSE": np.sqrt((err ** 2).mean()),
            "WAPE": np.abs(err).sum() / y.sum(),      # share of the demand that was missed
            "bias": pred.sum() / y.sum() - 1}         # negative = the model forecasts too little overall

def fit_gbm(features, frame, **params):
    model = HistGradientBoostingRegressor(loss="poisson", random_state=0, **params)
    return model.fit(frame[features], frame.cnt)

# small grid on the validation months
rows = []
for lr in [0.05, 0.1]:
    for depth in [4, 6, None]:
        for leaves in [15, 31]:
            m = fit_gbm(CALENDAR_WEATHER + LAGS, train, learning_rate=lr, max_depth=depth, max_leaf_nodes=leaves, max_iter=300)
            rows.append({"learning_rate": lr, "max_depth": depth, "max_leaf_nodes": leaves,
                         **score(valid.cnt, m.predict(valid[CALENDAR_WEATHER + LAGS]))})
grid = pd.DataFrame(rows).sort_values("MAE")
best = grid.iloc[0]
PARAMS = dict(learning_rate=float(best.learning_rate), max_depth=None if pd.isna(best.max_depth) else int(best.max_depth),
              max_leaf_nodes=int(best.max_leaf_nodes), max_iter=300)
print("chosen on the validation months:", PARAMS)
grid.head(4).round(2)

# refit on everything before the test period and predict July-December 2012
profile = train_valid.groupby(["dow", "hour"]).cnt.mean()
predictions = {
    "Same hour last week": test.lag_168.to_numpy(),
    "Weekday x hour average": profile.reindex(pd.MultiIndex.from_arrays([test.dow, test.hour])).to_numpy(),
}
model_cal = fit_gbm(CALENDAR_WEATHER, train_valid, **PARAMS)
predictions["Trees: calendar + weather"] = model_cal.predict(test[CALENDAR_WEATHER])
model_full = fit_gbm(CALENDAR_WEATHER + LAGS, train_valid, **PARAMS)
predictions["Trees + recent demand"] = model_full.predict(test[CALENDAR_WEATHER + LAGS])

# Trees cannot predict above the demand levels they saw in training, but the system keeps growing. A scale-free version predicts demand
# *relative to the recent level* and multiplies back, so a busier month does not push the features outside what the trees know.
def relative(frame):
    scale = frame.week_avg_total / 24                              # average hourly demand over the past seven days
    rel = frame[CALENDAR_WEATHER].copy()
    for col in ["lag_24", "lag_48", "lag_168", "same_hour_7d"]:
        rel[col + "_rel"] = frame[col] / scale
    rel["yesterday_vs_week"] = frame.yesterday_total / frame.week_avg_total
    return rel, scale

rel_train, scale_train = relative(train_valid)
rel_test, scale_test = relative(test)
model_rel = HistGradientBoostingRegressor(loss="poisson", random_state=0, **PARAMS).fit(
    rel_train, train_valid.cnt / scale_train, sample_weight=scale_train)
predictions["Trees on demand relative to recent level"] = model_rel.predict(rel_test) * scale_test.to_numpy()

results = pd.DataFrame({name: score(test.cnt, p) for name, p in predictions.items()}).T
BEST = results.MAE.idxmin()
print("best model on the unseen months:", BEST)
results.round(3)

fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.8))
colors = [GREY, BLUE, "#38a169", "#805ad5", AMBER]
axes[0].barh(results.index[::-1], results.MAE[::-1], color=colors[::-1])
for y, v in enumerate(results.MAE[::-1]):
    axes[0].text(v + 1, y, f"{v:.0f}", va="center", fontsize=9)
axes[0].set_xlabel("Mean absolute error (rentals per hour)")
axes[0].set_title("Average miss per hour (lower is better)", fontsize=10)
axes[1].barh(results.index[::-1], results.WAPE[::-1] * 100, color=colors[::-1])
for y, v in enumerate(results.WAPE[::-1] * 100):
    axes[1].text(v + 0.5, y, f"{v:.0f}%", va="center", fontsize=9)
axes[1].set_xlabel("Share of total demand missed (%)")
axes[1].set_title("Weighted absolute percentage error", fontsize=10)
axes[1].set_yticklabels([])
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

# the quantile trees use the same scale-free set-up as the best point model: they predict a quantile of demand relative to the recent level
def fit_quantile(q, frame):
    rel, scale = relative(frame)
    model = HistGradientBoostingRegressor(loss="quantile", quantile=q, random_state=0, learning_rate=PARAMS["learning_rate"],
                                          max_depth=PARAMS["max_depth"], max_leaf_nodes=PARAMS["max_leaf_nodes"], max_iter=300)
    return model.fit(rel, frame.cnt / scale)

def predict_quantile(model, frame):
    rel, scale = relative(frame)
    return model.predict(rel) * scale.to_numpy()

y_test = test.cnt.to_numpy()

# 1. raw quantile trees, fitted on everything before the test period
raw_lo = predict_quantile(fit_quantile(0.1, train_valid), test)
raw_hi = predict_quantile(fit_quantile(0.9, train_valid), test)

# 2. conformalised: fit on the training months, measure the misses on the validation months, widen by that amount
q_lo, q_hi = fit_quantile(0.1, train), fit_quantile(0.9, train)
yv = valid.cnt.to_numpy()
scores = np.maximum(predict_quantile(q_lo, valid) - yv, yv - predict_quantile(q_hi, valid))
level = min(np.ceil((len(valid) + 1) * 0.8) / len(valid), 1.0)
widen = np.quantile(scores, level, method="higher")
lower = np.maximum(predict_quantile(q_lo, test) - widen, 0)
upper = predict_quantile(q_hi, test) + widen

def coverage(lo, hi):
    return {"coverage": ((y_test >= lo) & (y_test <= hi)).mean(), "average_width": np.mean(hi - lo)}

intervals = pd.DataFrame({"Raw quantile trees": coverage(raw_lo, raw_hi), "Conformalised": coverage(lower, upper)}).T
print(f"the interval is widened by {widen:.0f} rentals on each side")
intervals.round(3)

week = slice("2012-09-17", "2012-09-23")
w = test.loc[week]
fig, ax = plt.subplots(figsize=(10.5, 4))
idx = test.index.get_indexer(w.index)
ax.fill_between(w.index, lower[idx], upper[idx], color=AMBER, alpha=0.25, label="80% interval (conformalised)")
ax.plot(w.index, predictions[BEST][idx], color=AMBER, lw=2, label="Forecast")
ax.plot(w.index, w.cnt, color="#2d3748", lw=1.3, marker="o", ms=2.5, label="Actual")
ax.set_ylabel("Rentals per hour")
ax.set_title("A normal week in September 2012: forecast against what happened")
ax.legend(frameon=False, ncol=3)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

best_pred = predictions[BEST]
by_hour = pd.DataFrame({"hour": test.hour, "model": np.abs(test.cnt - best_pred), "last_week": np.abs(test.cnt - test.lag_168)}).groupby("hour").mean()
fig, ax = plt.subplots(figsize=(9, 3.8))
ax.plot(by_hour.index, by_hour.last_week, color=GREY, lw=2, label="Same hour last week")
ax.plot(by_hour.index, by_hour.model, color=AMBER, lw=2.4, label="Best model (relative trees)")
ax.set_xlabel("Hour of day")
ax.set_ylabel("Mean absolute error")
ax.set_xticks(range(0, 24, 2))
ax.set_title("Errors are biggest at the commuter peaks, where the demand is")
ax.legend(frameon=False)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

daily_err = pd.DataFrame({"actual": test.cnt, "forecast": best_pred, "abs_err": np.abs(test.cnt - best_pred),
                          "weather": test.weather, "temp_c": test.temp_c}).resample("D").agg(
    {"actual": "sum", "forecast": "sum", "abs_err": "sum", "weather": "max", "temp_c": "mean"})
daily_err["missed_pct"] = daily_err.abs_err / daily_err.actual * 100
daily_err["holiday"] = test.holiday.resample("D").max().astype(int)
worst = daily_err.sort_values("abs_err", ascending=False).head(6)
worst.assign(actual=worst.actual.round(0), forecast=worst.forecast.round(0), abs_err=worst.abs_err.round(0),
             missed_pct=worst.missed_pct.round(0), temp_c=worst.temp_c.round(1)).rename(columns={"weather": "worst_weather"})

# two kinds of day the model finds hard: public holidays, and the two days of the hurricane
sandy = daily_err.loc["2012-10-29":"2012-10-30"]
groups = {"ordinary days": daily_err[(daily_err.holiday == 0) & ~daily_err.index.isin(sandy.index)],
          "public holidays": daily_err[daily_err.holiday == 1],
          "Hurricane Sandy days": sandy}
pd.DataFrame({name: {"days": len(g), "actual rentals per day": g.actual.mean(), "forecast per day": g.forecast.mean(),
                     "error per day": g.abs_err.mean(), "error as % of the demand": 100 * g.abs_err.sum() / g.actual.sum()}
              for name, g in groups.items()}).T.round(0)

fig, ax = plt.subplots(figsize=(10.5, 3.8))
span = daily_err.loc["2012-10-15":"2012-11-12"]
ax.bar(span.index, span.actual / 1000, color="#2d3748", width=0.8, label="Actual", alpha=0.85)
ax.plot(span.index, span.forecast / 1000, color=AMBER, lw=2.5, marker="o", ms=4, label="Forecast")
ax.set_ylabel("Rentals per day (thousand)")
ax.set_title("Hurricane Sandy (29-30 October 2012) in the daily totals")
ax.legend(frameon=False)
fig.autofmt_xdate()
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

# done by hand because the model predicts a relative quantity: shuffle one column, predict, multiply back, measure the error in rentals
rng = np.random.default_rng(0)
base_error = np.abs(y_test - model_rel.predict(rel_test) * scale_test.to_numpy()).mean()
rows = {}
for col in rel_test.columns:
    extra = []
    for _ in range(5):
        shuffled = rel_test.copy()
        shuffled[col] = rng.permutation(shuffled[col].to_numpy())
        extra.append(np.abs(y_test - model_rel.predict(shuffled) * scale_test.to_numpy()).mean() - base_error)
    rows[col] = np.mean(extra)
importance = pd.Series(rows).sort_values()
recent = [c for c in importance.index if c.endswith("_rel") or c == "yesterday_vs_week"]
fig, ax = plt.subplots(figsize=(8, 5))
ax.barh(importance.index, importance.values, color=[AMBER if f in recent else BLUE for f in importance.index])
ax.set_xlabel("Increase in mean absolute error when the feature is shuffled (rentals per hour)")
ax.set_title("Feature importance (amber = recent demand, blue = calendar and weather)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
importance.sort_values(ascending=False).head(5).round(1).to_frame("extra error")
