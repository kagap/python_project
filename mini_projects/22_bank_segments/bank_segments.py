"""Who are the bank's customers? Segmenting 45,000 clients with K-means.

Plain-script version of the notebook. Set BANK_DIR to a folder for the downloaded zip.
"""


import io
import os
import urllib.request
import zipfile
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import adjusted_rand_score, roc_auc_score, silhouette_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import OneHotEncoder, StandardScaler

DATA_DIR = Path(os.environ.get("BANK_DIR", "bank_data"))
DATA_DIR.mkdir(exist_ok=True)
AMBER, BLUE, GREY = "#e8a33d", "#2b6cb0", "#a0aec0"
PALETTE = [AMBER, BLUE, "#38a169", "#c0392b", "#805ad5", "#17a2b8", "#4a5568", "#d4ac0d"]
plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
                     "grid.alpha": 0.25, "figure.dpi": 110})

zip_path = DATA_DIR / "bank_marketing.zip"
if not zip_path.exists():
    urllib.request.urlretrieve("https://archive.ics.uci.edu/static/public/222/bank+marketing.zip", zip_path)
with zipfile.ZipFile(zip_path) as outer:
    inner = zipfile.ZipFile(io.BytesIO(outer.read("bank.zip")))
    df = pd.read_csv(inner.open("bank-full.csv"), sep=";")
df["subscribed"] = (df.y == "yes").astype(int)
print(f"{len(df):,} clients, {df.subscribed.mean():.1%} subscribed to the term deposit")
df.head(4)

fig, axes = plt.subplots(1, 3, figsize=(11, 3.4))
axes[0].hist(df.age, bins=range(18, 96, 3), color=AMBER, edgecolor="white")
axes[0].set_title("Age")
axes[1].hist(df.balance.clip(-2000, 15000), bins=50, color=BLUE, edgecolor="white")
axes[1].set_title("Account balance (euro, clipped)")
job = df.job.value_counts()
axes[2].barh(job.index[::-1], job.values[::-1], color=GREY)
axes[2].set_title("Job")
axes[2].tick_params(axis="y", labelsize=7)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
df[["age", "balance"]].describe().round(0)

df["balance_log"] = np.sign(df.balance) * np.log1p(df.balance.abs())
NUMERIC = ["age", "balance_log"]
CATEGORICAL = ["job", "marital", "education", "default", "housing", "loan"]

numeric = StandardScaler().fit_transform(df[NUMERIC])
encoder = OneHotEncoder(sparse_output=False).fit(df[CATEGORICAL])
categorical = encoder.transform(df[CATEGORICAL]) * 0.5
X = np.hstack([numeric, categorical])
print(f"feature matrix: {X.shape[0]:,} clients x {X.shape[1]} columns")

rows = []
for k in range(2, 11):
    km = KMeans(n_clusters=k, n_init=5, random_state=0).fit(X)
    rows.append({"k": k, "inertia": km.inertia_,
                 "silhouette": silhouette_score(X, km.labels_, sample_size=6000, random_state=0)})
selection = pd.DataFrame(rows).set_index("k")

fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
axes[0].plot(selection.index, selection.inertia / 1000, marker="o", color=AMBER)
axes[0].set_xlabel("Number of clusters")
axes[0].set_ylabel("Inertia (thousand)")
axes[0].set_title("Elbow")
axes[1].plot(selection.index, selection.silhouette, marker="o", color=BLUE)
axes[1].set_xlabel("Number of clusters")
axes[1].set_ylabel("Silhouette")
axes[1].set_title("Silhouette")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
selection.round(3).T

K = 6
km = KMeans(n_clusters=K, n_init=20, random_state=0).fit(X)
# number the segments by average age so that Segment 1 is the youngest
order = df.groupby(km.labels_).age.mean().sort_values().index
relabel = {old: new + 1 for new, old in enumerate(order)}
df["segment"] = pd.Series(km.labels_).map(relabel).to_numpy()
df.segment.value_counts().sort_index().to_frame("clients").assign(share=lambda d: (d.clients / len(df)).round(3))

rng = np.random.default_rng(1)
restarts = [KMeans(n_clusters=K, n_init=1, random_state=s).fit(X).labels_ for s in range(10)]
restart_ari = [adjusted_rand_score(km.labels_, labels) for labels in restarts]

boot_ari = []
for _ in range(10):
    idx = rng.choice(len(X), size=len(X), replace=True)
    boot = KMeans(n_clusters=K, n_init=3, random_state=0).fit(X[idx])
    boot_ari.append(adjusted_rand_score(km.labels_, boot.predict(X)))
pd.DataFrame({"ARI vs the final clustering": [np.mean(restart_ari), np.mean(boot_ari)],
              "lowest": [np.min(restart_ari), np.min(boot_ari)]},
             index=["10 random restarts", "10 bootstrap resamples"]).round(3)

def top_share(s):
    vc = s.value_counts(normalize=True)
    return f"{vc.index[0]} ({vc.iloc[0]:.0%})"

profile = df.groupby("segment").agg(
    clients=("age", "size"),
    mean_age=("age", "mean"),
    median_balance=("balance", "median"),
    housing_loan=("housing", lambda s: (s == "yes").mean()),
    personal_loan=("loan", lambda s: (s == "yes").mean()),
    married=("marital", lambda s: (s == "married").mean()),
    tertiary=("education", lambda s: (s == "tertiary").mean()),
    top_job=("job", top_share),
)
profile["share"] = profile.clients / len(df)
profile.round(2)

sample = df.sample(8000, random_state=0)
pca = PCA(n_components=2, random_state=0).fit(X)
xy = pca.transform(X[sample.index])
fig, ax = plt.subplots(figsize=(8, 5.6))
for seg, color in zip(sorted(df.segment.unique()), PALETTE):
    m = (sample.segment == seg).to_numpy()
    ax.scatter(xy[m, 0], xy[m, 1], s=6, alpha=0.5, color=color, label=f"Segment {seg}")
ax.set_xlabel(f"Component 1 ({pca.explained_variance_ratio_[0]:.0%} of the variance)")
ax.set_ylabel(f"Component 2 ({pca.explained_variance_ratio_[1]:.0%})")
ax.set_title("Clients in two dimensions, coloured by segment")
ax.legend(frameon=False, markerscale=3, ncol=3, fontsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

def wilson(successes, n, z=1.96):
    p = successes / n
    centre = (p + z**2 / (2 * n)) / (1 + z**2 / n)
    half = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / (1 + z**2 / n)
    return centre - half, centre + half

overall = df.subscribed.mean()
by_segment = df.groupby("segment").subscribed.agg(["sum", "size"])
by_segment["rate"] = by_segment["sum"] / by_segment["size"]
ci = np.array([wilson(s, n) for s, n in zip(by_segment["sum"], by_segment["size"])])
by_segment["ci_low"], by_segment["ci_high"] = ci[:, 0], ci[:, 1]
by_segment["lift"] = by_segment.rate / overall

fig, ax = plt.subplots(figsize=(8.5, 4))
x = by_segment.index
ax.bar(x, by_segment.rate * 100, color=PALETTE[:len(x)], yerr=[(by_segment.rate - by_segment.ci_low) * 100, (by_segment.ci_high - by_segment.rate) * 100],
       capsize=4)
ax.axhline(overall * 100, color="#4a5568", ls="--", lw=1)
ax.text(x.max() + 0.45, overall * 100, f"average {overall:.1%}", va="bottom", ha="right", fontsize=8)
ax.set_xlabel("Segment")
ax.set_ylabel("Subscribed to the term deposit (%)")
ax.set_title("Response rate by segment (error bars = 95% interval)")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
by_segment.rename(columns={"sum": "subscribed", "size": "clients"}).round(3)

idx_train, idx_test = train_test_split(df.index, test_size=0.3, stratify=df.subscribed, random_state=0)
y = df.subscribed

def targeting_auc(groups):
    rates = y[idx_train].groupby(groups[idx_train]).mean()
    score = groups[idx_test].map(rates).fillna(y[idx_train].mean())
    return roc_auc_score(y[idx_test], score)

alternatives = {
    "K-means segments (6)": df.segment,
    "Age bands (5)": pd.cut(df.age, [0, 29, 39, 49, 59, 120]).astype(str),
    "Job (12)": df.job,
    "Housing loan (2)": df.housing,
    "Random groups (6)": pd.Series(np.random.default_rng(0).integers(0, 6, len(df)), index=df.index),
}
auc = pd.Series({name: targeting_auc(g) for name, g in alternatives.items()}).sort_values()

fig, ax = plt.subplots(figsize=(8.5, 3.6))
ax.barh(auc.index, auc.values, color=[AMBER if n.startswith("K-means") else GREY for n in auc.index])
for y_, v in enumerate(auc.values):
    ax.text(v + 0.003, y_, f"{v:.3f}", va="center", fontsize=9)
ax.axvline(0.5, color="#4a5568", ls="--", lw=1)
ax.set_xlim(0.45, auc.max() + 0.05)
ax.set_xlabel("ROC AUC on unseen clients (0.5 = random)")
ax.set_title("How well each grouping picks out the clients who subscribe")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
auc.round(3).to_frame("AUC")
