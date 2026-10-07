"""Who will miss their next credit card payment? A credit risk model with calibration, a cost-based threshold and a fairness audit.

Plain-script version of the notebook. Set CREDIT_DIR to a folder for the downloaded zip.
"""


import os
import urllib.request
import zipfile
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

DATA_DIR = Path(os.environ.get("CREDIT_DIR", "credit_data"))
DATA_DIR.mkdir(exist_ok=True)
AMBER, BLUE, GREY = "#e8a33d", "#2b6cb0", "#a0aec0"
plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
                     "grid.alpha": 0.25, "figure.dpi": 110})

zip_path = DATA_DIR / "default_credit.zip"
if not zip_path.exists():
    urllib.request.urlretrieve("https://archive.ics.uci.edu/static/public/350/default+of+credit+card+clients.zip", zip_path)
with zipfile.ZipFile(zip_path) as z:
    raw = pd.read_excel(z.open("default of credit card clients.xls"), header=1)
df = raw.rename(columns={"PAY_0": "PAY_1", "default payment next month": "default"}).drop(columns="ID")
df["EDUCATION"] = df.EDUCATION.where(df.EDUCATION.isin([1, 2, 3]), 4)          # 1 graduate school, 2 university, 3 high school, 4 other
df["MARRIAGE"] = df.MARRIAGE.where(df.MARRIAGE.isin([1, 2]), 3)               # 1 married, 2 single, 3 other
print(f"{len(df):,} clients, {df.default.mean():.1%} defaulted the following month")
df[["LIMIT_BAL", "AGE", "PAY_1", "BILL_AMT1", "PAY_AMT1", "default"]].describe().round(1).T

fig, axes = plt.subplots(1, 3, figsize=(12, 3.6))
pay = df.groupby("PAY_1").default.agg(["mean", "size"])
pay = pay[pay["size"] >= 50]
axes[0].bar(pay.index.astype(str), pay["mean"] * 100, color=AMBER)
axes[0].set_xlabel("Months of delay last month (-2 = no use)")
axes[0].set_ylabel("Defaulted next month (%)")
axes[0].set_title("The latest payment status says a lot", fontsize=10)
limit = df.groupby(pd.qcut(df.LIMIT_BAL, 6, duplicates="drop"), observed=True).default.mean() * 100
axes[1].bar([f"{int(i.left / 1000)}k" for i in limit.index], limit.values, color=BLUE)
axes[1].set_xlabel("Credit limit (from, in NT dollars)")
axes[1].set_title("Higher limit, lower default rate", fontsize=10)
age = df.groupby(pd.cut(df.AGE, [20, 25, 30, 35, 40, 50, 80]), observed=True).default.mean() * 100
axes[2].bar([f"{int(i.left) + 1}+" for i in age.index], age.values, color=GREY)
axes[2].set_xlabel("Age")
axes[2].set_title("Age matters much less", fontsize=10)
for ax in axes[1:]:
    ax.set_ylim(0, 40)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

pay_cols = [f"PAY_{i}" for i in range(1, 7)]
bill_cols = [f"BILL_AMT{i}" for i in range(1, 7)]
amt_cols = [f"PAY_AMT{i}" for i in range(1, 7)]

feat = df.copy()
feat["utilisation"] = feat.BILL_AMT1 / feat.LIMIT_BAL
feat["repayment_ratio"] = (feat.PAY_AMT1 / feat.BILL_AMT2.where(feat.BILL_AMT2 > 0)).clip(0, 2).fillna(1)
feat["months_late"] = (feat[pay_cols] > 0).sum(axis=1)
feat["max_delay"] = feat[pay_cols].max(axis=1)
feat["avg_bill"] = feat[bill_cols].mean(axis=1)
feat["avg_payment"] = feat[amt_cols].mean(axis=1)

FEATURES = ["LIMIT_BAL", "EDUCATION", "MARRIAGE", "AGE"] + pay_cols + bill_cols + amt_cols + [
    "utilisation", "repayment_ratio", "months_late", "max_delay", "avg_bill", "avg_payment"]

idx_train, idx_rest = train_test_split(feat.index, test_size=0.4, stratify=feat.default, random_state=0)
idx_valid, idx_test = train_test_split(idx_rest, test_size=0.5, stratify=feat.default[idx_rest], random_state=0)
X, y = feat[FEATURES], feat.default
print(f"train {len(idx_train):,} | validation {len(idx_valid):,} | test {len(idx_test):,}; default rate {y[idx_train].mean():.3f} / {y[idx_valid].mean():.3f} / {y[idx_test].mean():.3f}")

models = {
    "Logistic regression": make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)),
    "Gradient boosting": HistGradientBoostingClassifier(learning_rate=0.05, max_iter=300, max_leaf_nodes=15, l2_regularization=1.0,
                                                         early_stopping=True, validation_fraction=0.15, random_state=0),
}
for model in models.values():
    model.fit(X.loc[idx_train], y[idx_train])

def metrics(p, truth):
    return {"ROC AUC": roc_auc_score(truth, p), "avg precision": average_precision_score(truth, p),
            "log-loss": log_loss(truth, p), "Brier": brier_score_loss(truth, p)}

p_valid = {name: m.predict_proba(X.loc[idx_valid])[:, 1] for name, m in models.items()}
p_test = {name: m.predict_proba(X.loc[idx_test])[:, 1] for name, m in models.items()}
pd.DataFrame({name: metrics(p, y[idx_test]) for name, p in p_test.items()}).T.round(4)

with_sex = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(X.loc[idx_train].assign(SEX=df.SEX), y[idx_train])
auc_with = roc_auc_score(y[idx_test], with_sex.predict_proba(X.loc[idx_test].assign(SEX=df.SEX))[:, 1])
print(f"logistic AUC without sex {roc_auc_score(y[idx_test], p_test['Logistic regression']):.4f}, with sex {auc_with:.4f}")

calibrators = {name: IsotonicRegression(out_of_bounds="clip").fit(p_valid[name], y[idx_valid]) for name in models}
p_test_cal = {name: calibrators[name].predict(p_test[name]) for name in models}

def reliability(p, truth, bins=10):
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    which = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, bins - 1)
    return np.array([(p[which == b].mean(), truth[which == b].mean()) for b in range(bins) if (which == b).sum() > 20])

fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.6), sharey=True)
for ax, name in zip(axes, models):
    ax.plot([0, 1], [0, 1], color=GREY, ls="--", label="Perfect")
    raw_pts = reliability(p_test[name], y[idx_test].to_numpy())
    cal_pts = reliability(p_test_cal[name], y[idx_test].to_numpy())
    ax.plot(raw_pts[:, 0], raw_pts[:, 1], marker="o", color=BLUE, label="As trained")
    ax.plot(cal_pts[:, 0], cal_pts[:, 1], marker="s", color=AMBER, label="After isotonic calibration")
    ax.set_xlabel("Predicted probability of default")
    ax.set_title(name, fontsize=10)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
axes[0].set_ylabel("Share that really defaulted")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
pd.DataFrame({f"{name} ({kind})": {"Brier": brier_score_loss(y[idx_test], p), "log-loss": log_loss(y[idx_test], np.clip(p, 1e-6, 1 - 1e-6))}
              for name in models for kind, p in [("raw", p_test[name]), ("calibrated", p_test_cal[name])]}).T.round(4)

BEST = "Gradient boosting"
thresholds = np.linspace(0.05, 0.95, 91)

def cost_per_client(p, truth, threshold, c_miss, c_reject):
    rejected = p >= threshold
    missed = (~rejected) & (truth == 1)
    wrongly_rejected = rejected & (truth == 0)
    return (c_miss * missed.sum() + c_reject * wrongly_rejected.sum()) / len(truth)

p_v, y_v = calibrators[BEST].predict(p_valid[BEST]), y[idx_valid].to_numpy()
p_t, y_t = p_test_cal[BEST], y[idx_test].to_numpy()

rows = []
for c_miss in [2, 3, 5, 10]:
    costs_v = [cost_per_client(p_v, y_v, t, c_miss, 1) for t in thresholds]
    t_star = thresholds[int(np.argmin(costs_v))]
    rows.append({"cost of a miss (units)": c_miss, "best threshold": t_star,
                 "rejected (%)": (p_t >= t_star).mean() * 100,
                 "cost at best threshold": cost_per_client(p_t, y_t, t_star, c_miss, 1),
                 "cost at 0.5": cost_per_client(p_t, y_t, 0.5, c_miss, 1),
                 "cost of approving everyone": c_miss * y_t.mean()})
policy = pd.DataFrame(rows).set_index("cost of a miss (units)")
THRESHOLD = float(policy.loc[3, "best threshold"])
print(f"threshold used from here on (miss = 3 units): {THRESHOLD:.2f}")
policy.round(3)

fig, axes = plt.subplots(1, 2, figsize=(11, 4))
for c_miss, color in [(2, GREY), (3, AMBER), (5, BLUE), (10, "#c0392b")]:
    costs = [cost_per_client(p_t, y_t, t, c_miss, 1) for t in thresholds]
    axes[0].plot(thresholds, costs, color=color, lw=2, label=f"a miss costs {c_miss} units")
    axes[0].scatter([thresholds[int(np.argmin(costs))]], [min(costs)], color=color, zorder=3)
axes[0].set_xlabel("Threshold: reject when the probability of default is above")
axes[0].set_ylabel("Expected cost per client (units)")
axes[0].set_title("The best cut-off moves with the cost of a mistake", fontsize=10)
axes[0].legend(frameon=False, fontsize=8)

order = np.argsort(-p_t)
captured = np.cumsum(y_t[order]) / y_t.sum() * 100
axes[1].plot(np.arange(1, len(order) + 1) / len(order) * 100, captured, color=AMBER, lw=2.2, label="Model")
axes[1].plot([0, 100], [0, 100], color=GREY, ls="--", label="Random")
axes[1].set_xlabel("% of clients reviewed, highest risk first")
axes[1].set_ylabel("% of all defaulters found")
axes[1].set_title("Cumulative gains", fontsize=10)
axes[1].legend(frameon=False)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

audit_df = feat.loc[idx_test].assign(p=p_t, rejected=(p_t >= THRESHOLD))
audit_df["sex"] = audit_df.SEX.map({1: "male", 2: "female"})
audit_df["education"] = audit_df.EDUCATION.map({1: "graduate school", 2: "university", 3: "high school", 4: "other"})
audit_df["marital status"] = audit_df.MARRIAGE.map({1: "married", 2: "single", 3: "other"})
audit_df["age band"] = pd.cut(audit_df.AGE, [20, 29, 39, 49, 80], labels=["21-29", "30-39", "40-49", "50+"]).astype(str)

rows = []
for attribute in ["sex", "education", "marital status", "age band"]:
    for group, g in audit_df.groupby(attribute):
        if len(g) < 100:
            continue
        good, bad = g[g.default == 0], g[g.default == 1]
        rows.append({"attribute": attribute, "group": group, "clients": len(g), "defaulters": len(bad),
                     "default rate": g.default.mean(), "avg predicted": g.p.mean(),
                     "approved": 1 - g.rejected.mean(),
                     "good clients rejected": good.rejected.mean(),
                     "defaulters approved": 1 - bad.rejected.mean(),
                     "AUC": roc_auc_score(g.default, g.p)})
audit = pd.DataFrame(rows).set_index(["attribute", "group"])
audit.round(3)

fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), sharey=True)
labels = [f"{a}: {g}" for a, g in audit.index]
y_pos = np.arange(len(audit))
axes[0].barh(y_pos, audit["good clients rejected"] * 100, color=BLUE)
axes[0].set_title("Good clients wrongly rejected (%)", fontsize=10)
axes[1].barh(y_pos, audit["defaulters approved"] * 100, color=AMBER)
axes[1].set_title("Defaulters wrongly approved (%)", fontsize=10)
axes[0].set_yticks(y_pos, labels, fontsize=8)
axes[0].invert_yaxis()
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

from sklearn.inspection import permutation_importance

imp = permutation_importance(models[BEST], X.loc[idx_test], y[idx_test], scoring="roc_auc", n_repeats=5, random_state=0)
importance = pd.Series(imp.importances_mean, index=FEATURES).sort_values().tail(10)
fig, ax = plt.subplots(figsize=(8, 4.2))
ax.barh(importance.index, importance.values, color=AMBER)
ax.set_xlabel("Fall in ROC AUC when the feature is shuffled")
ax.set_title("The ten features the model relies on most")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
importance.sort_values(ascending=False).round(4).to_frame("AUC lost").head(5)
