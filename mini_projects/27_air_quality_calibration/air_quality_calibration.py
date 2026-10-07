"""Can a 10-euro sensor replace a 100,000-euro analyser? Calibrating low-cost air quality sensors, drift and re-calibration.

Plain-script version of the notebook. Set AIR_DIR to a folder for the downloaded zip.
"""


import os
import urllib.request
import zipfile
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score

DATA_DIR = Path(os.environ.get("AIR_DIR", "air_data"))
DATA_DIR.mkdir(exist_ok=True)
AMBER, BLUE, GREY = "#e8a33d", "#2b6cb0", "#a0aec0"
plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
                     "grid.alpha": 0.25, "figure.dpi": 110})

zip_path = DATA_DIR / "air_quality.zip"
if not zip_path.exists():
    urllib.request.urlretrieve("https://archive.ics.uci.edu/static/public/360/air+quality.zip", zip_path)
with zipfile.ZipFile(zip_path) as z:
    raw = pd.read_csv(z.open("AirQualityUCI.csv"), sep=";", decimal=",")
raw = raw.loc[:, ~raw.columns.str.startswith("Unnamed")].dropna(how="all")
raw["time"] = pd.to_datetime(raw.Date + " " + raw.Time.str.replace(".", ":", regex=False), format="%d/%m/%Y %H:%M:%S")
air = raw.drop(columns=["Date", "Time"]).set_index("time").replace(-200, np.nan)
air = air.rename(columns={"CO(GT)": "CO", "C6H6(GT)": "benzene", "NO2(GT)": "NO2", "NOx(GT)": "NOx", "NMHC(GT)": "NMHC",
                          "PT08.S1(CO)": "S1_CO", "PT08.S2(NMHC)": "S2_NMHC", "PT08.S3(NOx)": "S3_NOx", "PT08.S4(NO2)": "S4_NO2", "PT08.S5(O3)": "S5_O3"})

REFERENCE = ["CO", "benzene", "NO2", "NOx", "NMHC"]               # the analyser (ground truth)
SENSORS = ["S1_CO", "S2_NMHC", "S3_NOx", "S4_NO2", "S5_O3"]        # the cheap sensors
WEATHER = ["T", "RH", "AH"]
print(f"{len(air):,} hourly records, {air.index.min():%d %b %Y} to {air.index.max():%d %b %Y}")
(air.isna().mean() * 100).round(1).to_frame("% missing").T

monthly_missing = air.isna().groupby([air.index.year, air.index.month]).mean() * 100
monthly_missing.index = [f"{y}-{m:02d}" for y, m in monthly_missing.index]
cols = REFERENCE + SENSORS + WEATHER
fig, axes = plt.subplots(1, 2, figsize=(12, 4.3), gridspec_kw={"width_ratios": [1, 1.5]})
share = air[cols].isna().mean() * 100
axes[0].barh(cols[::-1], share[cols][::-1], color=[AMBER if c in REFERENCE else BLUE if c in SENSORS else GREY for c in cols[::-1]])
axes[0].set_xlabel("% of hours with no reading")
axes[0].set_title("Reference gas analysers (amber) miss far more than sensors", fontsize=9)
im = axes[1].imshow(monthly_missing[cols].T.values, cmap="Oranges", aspect="auto", vmin=0, vmax=100)
axes[1].set_yticks(range(len(cols)), cols, fontsize=8)
axes[1].set_xticks(range(len(monthly_missing)), monthly_missing.index, rotation=70, fontsize=8)
axes[1].grid(False)
axes[1].set_title("Share missing by month", fontsize=9)
fig.colorbar(im, ax=axes[1], shrink=0.8, label="%")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

air["hour"] = air.index.hour
air["weekend"] = air.index.dayofweek >= 5
profile = air.groupby(["weekend", "hour"]).CO.mean().unstack(0)
month_means = air.CO.resample("MS").mean()
fig, axes = plt.subplots(1, 2, figsize=(11.5, 3.8))
axes[0].plot(profile.index, profile[False], color=AMBER, lw=2.4, label="Working days")
axes[0].plot(profile.index, profile[True], color=BLUE, lw=2.4, label="Weekends")
axes[0].set_xlabel("Hour of day")
axes[0].set_ylabel("Carbon monoxide (mg/m3, analyser)")
axes[0].set_title("The traffic rhythm of a road-side station", fontsize=10)
axes[0].legend(frameon=False)
axes[1].bar(month_means.index.strftime("%b %y"), month_means.values, color=GREY)
axes[1].tick_params(axis="x", rotation=60, labelsize=8)
axes[1].set_title("Monthly average CO", fontsize=10)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

corr = air[REFERENCE[:4] + SENSORS + WEATHER].corr()
fig, ax = plt.subplots(figsize=(8, 6.6))
im = ax.imshow(corr.values, cmap="RdBu_r", vmin=-1, vmax=1)
ax.set_xticks(range(len(corr)), corr.columns, rotation=60, ha="right", fontsize=8)
ax.set_yticks(range(len(corr)), corr.columns, fontsize=8)
for i in range(len(corr)):
    for j in range(len(corr)):
        ax.text(j, i, f"{corr.values[i, j]:.1f}", ha="center", va="center", fontsize=6.5,
                color="white" if abs(corr.values[i, j]) > 0.7 else "black")
ax.grid(False)
ax.set_title("Correlations: analysers (first four), sensors, temperature and humidity", fontsize=10)
fig.colorbar(im, ax=ax, shrink=0.75)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

FEATURES = SENSORS + WEATHER
clean = air.dropna(subset=FEATURES).copy()
clean["dow"] = clean.index.dayofweek

def split(target):
    d = clean.dropna(subset=[target])
    return (d[d.index < "2004-09-01"], d[(d.index >= "2004-09-01") & (d.index < "2004-11-01")], d[d.index >= "2004-11-01"])

train, valid, test = split("CO")
print(f"carbon monoxide: train {len(train):,} h | validation {len(valid):,} h | test {len(test):,} h")
pd.DataFrame({"train (Mar-Aug 2004)": train.CO.describe(), "test (Nov 2004-Apr 2005)": test.CO.describe()}).T.round(2)

MATCHING = {"CO": "S1_CO", "benzene": "S2_NMHC", "NO2": "S4_NO2", "NOx": "S3_NOx"}

def rolling_predict(frame, target, start, weeks_back=8):
    """Refit a linear model every week on the previous `weeks_back` weeks of reference data, then predict the coming week."""
    preds = pd.Series(np.nan, index=frame.index)
    fallback = LinearRegression().fit(frame[frame.index < start][FEATURES], frame[frame.index < start][target])
    for w0 in pd.date_range(start, frame.index.max() + pd.Timedelta(days=7), freq="7D"):
        cur = frame[(frame.index >= w0) & (frame.index < w0 + pd.Timedelta(days=7))]
        if cur.empty:
            continue
        hist = frame[(frame.index >= w0 - pd.Timedelta(weeks=weeks_back)) & (frame.index < w0)]
        model = LinearRegression().fit(hist[FEATURES], hist[target]) if len(hist) >= 100 else fallback
        preds.loc[cur.index] = model.predict(cur[FEATURES])
    return preds[preds.index >= start]

def run(target):
    tr, va, te = split(target)
    out = {}
    out["One sensor, linear"] = LinearRegression().fit(tr[[MATCHING[target]]], tr[target]).predict(te[[MATCHING[target]]])
    out["All sensors + weather, linear"] = LinearRegression().fit(tr[FEATURES], tr[target]).predict(te[FEATURES])
    grid = []
    for lr in [0.05, 0.1]:
        for leaves in [8, 16]:
            m = HistGradientBoostingRegressor(learning_rate=lr, max_leaf_nodes=leaves, max_iter=300, random_state=0).fit(tr[FEATURES], tr[target])
            grid.append((mean_absolute_error(va[target], m.predict(va[FEATURES])), lr, leaves))
    _, lr, leaves = min(grid)
    trva = pd.concat([tr, va])
    out["Gradient-boosted trees"] = HistGradientBoostingRegressor(learning_rate=lr, max_leaf_nodes=leaves, max_iter=300, random_state=0).fit(
        trva[FEATURES], trva[target]).predict(te[FEATURES])
    out["Weekly re-calibration (linear)"] = rolling_predict(pd.concat([tr, va, te]), target, pd.Timestamp("2004-11-01")).reindex(te.index).to_numpy()
    return te, out

def score(truth, pred):
    return {"RMSE": float(np.sqrt(np.mean((truth - pred) ** 2))), "MAE": mean_absolute_error(truth, pred), "R2": r2_score(truth, pred),
            "bias": float(np.mean(pred - truth))}

test_co, preds_co = run("CO")
results_co = pd.DataFrame({name: score(test_co.CO.to_numpy(), p) for name, p in preds_co.items()}).T
results_co.round(3)

fig, axes = plt.subplots(1, 2, figsize=(11, 3.8))
colors = [GREY, BLUE, "#38a169", AMBER]
axes[0].barh(results_co.index[::-1], results_co.RMSE[::-1], color=colors[::-1])
for y, v in enumerate(results_co.RMSE[::-1]):
    axes[0].text(v + 0.01, y, f"{v:.2f}", va="center", fontsize=9)
axes[0].set_xlabel("Error on the test months, RMSE (mg/m3)")
axes[0].set_title("Typical error (lower is better)", fontsize=10)
axes[1].barh(results_co.index[::-1], results_co.R2[::-1], color=colors[::-1])
for y, v in enumerate(results_co.R2[::-1]):
    axes[1].text(v + 0.01, y, f"{v:.2f}", va="center", fontsize=9)
axes[1].set_xlim(0, 1.05)
axes[1].set_yticklabels([])
axes[1].set_title("R2 (higher is better)", fontsize=10)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

static_linear, static_trees, recal = preds_co["All sensors + weather, linear"], preds_co["Gradient-boosted trees"], preds_co["Weekly re-calibration (linear)"]
frame = pd.DataFrame({"truth": test_co.CO.to_numpy(), "static linear": static_linear, "static trees": static_trees, "re-calibrated": recal}, index=test_co.index)
monthly = frame.groupby(frame.index.to_period("M"))
rmse_by_month = pd.DataFrame({c: monthly.apply(lambda g: np.sqrt(np.mean((g[c] - g.truth) ** 2))) for c in ["static linear", "static trees", "re-calibrated"]})
bias_by_month = pd.DataFrame({c: monthly.apply(lambda g: np.mean(g[c] - g.truth)) for c in ["static linear", "static trees", "re-calibrated"]})
months = [str(m) for m in rmse_by_month.index]

fig, axes = plt.subplots(1, 2, figsize=(11.5, 4))
for col, color in zip(rmse_by_month.columns, [BLUE, "#38a169", AMBER]):
    axes[0].plot(months, rmse_by_month[col], marker="o", color=color, label=col, lw=2)
    axes[1].plot(months, bias_by_month[col], marker="o", color=color, label=col, lw=2)
axes[0].set_ylabel("RMSE (mg/m3)")
axes[0].set_title("Error by month", fontsize=10)
axes[0].legend(frameon=False, fontsize=8)
axes[1].axhline(0, color="#4a5568", lw=1)
axes[1].set_ylabel("Average error: prediction minus truth (mg/m3)")
axes[1].set_title("Bias by month (below 0 = reads too low)", fontsize=10)
for ax in axes:
    ax.tick_params(axis="x", rotation=45, labelsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
rmse_by_month.round(2).T

w = frame.loc["2004-11-15":"2004-11-24"]
fig, ax = plt.subplots(figsize=(11, 3.9))
ax.plot(w.index, w.truth, color="#2d3748", lw=1.6, label="Reference analyser")
ax.plot(w.index, w["static linear"], color=BLUE, lw=1.2, label="Static linear (fitted 2004)")
ax.plot(w.index, w["re-calibrated"], color=AMBER, lw=1.6, label="Weekly re-calibrated")
ax.set_ylabel("Carbon monoxide (mg/m3)")
ax.set_title("Ten days in November 2004: the cheap sensor against the analyser")
ax.legend(frameon=False, ncol=3, fontsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

rows = {}
for target in ["CO", "benzene", "NO2", "NOx"]:
    te, preds = run(target)
    for name, p in preds.items():
        rows[(target, name)] = score(te[target].to_numpy(), p)
all_gases = pd.DataFrame(rows).T
all_gases.index.names = ["gas", "model"]
r2_table = all_gases.R2.unstack("model")[["One sensor, linear", "All sensors + weather, linear", "Gradient-boosted trees", "Weekly re-calibration (linear)"]]
r2_table.round(3)

from sklearn.inspection import permutation_importance

tr, va, te = split("CO")
trva = pd.concat([tr, va])
forest = HistGradientBoostingRegressor(learning_rate=0.05, max_leaf_nodes=8, max_iter=300, random_state=0).fit(trva[FEATURES], trva.CO)
imp = permutation_importance(forest, te[FEATURES], te.CO, n_repeats=5, random_state=0, scoring="neg_mean_absolute_error")
importance = pd.Series(imp.importances_mean, index=FEATURES).sort_values()
fig, ax = plt.subplots(figsize=(7.5, 3.9))
ax.barh(importance.index, importance.values, color=[BLUE if f in SENSORS else AMBER for f in importance.index])
ax.set_xlabel("Increase in mean absolute error when the input is shuffled (mg/m3)")
ax.set_title("Inputs the CO model relies on (blue = sensors, amber = weather)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
importance.sort_values(ascending=False).round(3).to_frame("extra error")
