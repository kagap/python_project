"""What should this listener hear next? Popularity, item-item filtering and implicit ALS on Last.fm listening data.

Plain-script version of the notebook. Set LASTFM_DIR to a folder for the downloaded zip.
"""


import io
import os
import urllib.request
import zipfile
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy.sparse as sp
from sklearn.decomposition import PCA, TruncatedSVD

DATA_DIR = Path(os.environ.get("LASTFM_DIR", "lastfm_data"))
DATA_DIR.mkdir(exist_ok=True)
AMBER, BLUE, GREY = "#e8a33d", "#2b6cb0", "#a0aec0"
plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
                     "grid.alpha": 0.25, "figure.dpi": 110})
rng = np.random.default_rng(42)

zip_path = DATA_DIR / "hetrec2011-lastfm-2k.zip"
if not zip_path.exists():
    urllib.request.urlretrieve("https://files.grouplens.org/datasets/hetrec2011/hetrec2011-lastfm-2k.zip", zip_path)
with zipfile.ZipFile(zip_path) as z:
    plays = pd.read_csv(z.open("user_artists.dat"), sep="\t")
    artists = pd.read_csv(io.TextIOWrapper(z.open("artists.dat"), encoding="utf-8", errors="replace"), sep="\t",
                          usecols=["id", "name"]).set_index("id").name
plays = plays.rename(columns={"userID": "user", "artistID": "artist", "weight": "plays"})

stats = pd.Series({"users": plays.user.nunique(), "artists": plays.artist.nunique(), "user-artist pairs": len(plays),
                   "density": f"{len(plays) / (plays.user.nunique() * plays.artist.nunique()):.2%}",
                   "artists per user (median)": int(plays.groupby("user").size().median()),
                   "most played artist": artists[plays.groupby("artist").plays.sum().idxmax()]})
stats

per_artist = plays.groupby("artist").agg(listeners=("user", "nunique"), plays=("plays", "sum")).sort_values("listeners", ascending=False)
fig, axes = plt.subplots(1, 2, figsize=(10, 3.8))
axes[0].plot(np.arange(1, len(per_artist) + 1), per_artist.listeners.to_numpy(), color=AMBER)
axes[0].set_xscale("log")
axes[0].set_yscale("log")
axes[0].set_xlabel("Artist rank by number of listeners")
axes[0].set_ylabel("Listeners")
axes[0].set_title("A long tail: few artists, many listeners")
share = per_artist.plays.cumsum() / per_artist.plays.sum()
axes[1].plot(np.arange(1, len(share) + 1) / len(share) * 100, share * 100, color=BLUE)
axes[1].set_xlabel("% of artists (most played first)")
axes[1].set_ylabel("% of all plays")
axes[1].set_title("Where the plays go")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
per_artist.head(5).join(artists)

MIN_LISTENERS = 5
keep = per_artist.index[per_artist.listeners >= MIN_LISTENERS]
data = plays[plays.artist.isin(keep)].copy()
user_ids = np.sort(data.user.unique())
artist_ids = np.sort(data.artist.unique())
u_index = {u: i for i, u in enumerate(user_ids)}
a_index = {a: i for i, a in enumerate(artist_ids)}
data["u"] = data.user.map(u_index)
data["a"] = data.artist.map(a_index)
data["conf"] = np.log1p(data.plays)
n_users, n_items = len(user_ids), len(artist_ids)

counts = data.groupby("u").size()
eligible = counts[counts >= 10].index
data["split"] = "train"
for u, group in data[data.u.isin(eligible)].groupby("u"):
    order = rng.permutation(group.index.to_numpy())
    n_test, n_valid = int(round(0.2 * len(order))), int(round(0.1 * len(order)))
    data.loc[order[:n_test], "split"] = "test"
    data.loc[order[n_test:n_test + n_valid], "split"] = "valid"

print(f"{n_users:,} users x {n_items:,} artists after filtering; "
      + ", ".join(f"{k}: {v:,}" for k, v in data.split.value_counts().items()))

def matrix(splits):
    d = data[data.split.isin(splits)]
    return sp.csr_matrix((d.conf.to_numpy(), (d.u.to_numpy(), d.a.to_numpy())), shape=(n_users, n_items))

X_train = matrix(["train"])
X_train_valid = matrix(["train", "valid"])
truth_valid = data[data.split == "valid"].groupby("u").a.apply(set)
truth_test = data[data.split == "test"].groupby("u").a.apply(set)
item_popularity = np.asarray((X_train_valid > 0).sum(axis=0)).ravel()

K = 10

def evaluate(score_matrix, seen, truth):
    """score_matrix: users x items scores. seen: sparse matrix of artists to exclude. truth: user -> hidden artists."""
    users = np.array(sorted(truth.index))
    scores = np.array(score_matrix[users], dtype=np.float32)
    seen_rows = seen[users].tocoo()
    scores[seen_rows.row, seen_rows.col] = -np.inf
    top = np.argpartition(-scores, K, axis=1)[:, :K]
    top = np.take_along_axis(top, np.argsort(-np.take_along_axis(scores, top, axis=1), axis=1), axis=1)
    discounts = 1 / np.log2(np.arange(2, K + 2))
    rec, prec, ndcg = [], [], []
    for row, u in zip(top, users):
        hidden = truth[u]
        hits = np.array([a in hidden for a in row])
        rec.append(hits.sum() / len(hidden))
        prec.append(hits.mean())
        ideal = discounts[:min(len(hidden), K)].sum()
        ndcg.append((hits * discounts).sum() / ideal)
    return {"recall@10": np.mean(rec), "precision@10": np.mean(prec), "ndcg@10": np.mean(ndcg),
            "coverage": len(np.unique(top)) / n_items, "novelty": item_popularity[top].mean()}

popularity_scores = np.tile(np.asarray((X_train > 0).sum(axis=0)).ravel().astype(np.float32), (n_users, 1))
pd.Series(evaluate(popularity_scores, X_train, truth_valid)).round(4).to_frame("popularity baseline (validation)")

def item_item_scores(X, neighbours=50):
    B = (X > 0).astype(np.float32).tocsc()
    norms = np.sqrt(np.asarray(B.sum(axis=0)).ravel()) + 1e-9
    sim = (B.T @ B).toarray() / np.outer(norms, norms)           # cosine similarity of listener sets
    np.fill_diagonal(sim, 0)
    cutoff = -np.partition(-sim, neighbours, axis=1)[:, neighbours][:, None]
    sim[sim < cutoff] = 0                                            # keep each artist's closest neighbours only
    return (X > 0).astype(np.float32) @ sim, sim

rows = []
for neighbours in [10, 30, 100, 300]:
    s, _ = item_item_scores(X_train, neighbours)
    rows.append({"neighbours": neighbours, **evaluate(s, X_train, truth_valid)})
knn_grid = pd.DataFrame(rows).set_index("neighbours")
BEST_K = int(knn_grid["ndcg@10"].idxmax())
print("best neighbourhood size:", BEST_K)
knn_grid.round(4)

def als(X, factors=32, reg=0.5, alpha=15.0, iterations=12, seed=0):
    """Implicit-feedback ALS. X: sparse users x items matrix of confidences (log1p plays)."""
    r = np.random.default_rng(seed)
    n_u, n_i = X.shape
    U = r.normal(0, 0.1, (n_u, factors))
    V = r.normal(0, 0.1, (n_i, factors))
    Xr, Xc = X.tocsr(), X.T.tocsr()

    def solve(fixed, sparse_rows, n_rows):
        YtY = fixed.T @ fixed + reg * np.eye(factors)
        out = np.zeros((n_rows, factors))
        for i in range(n_rows):
            start, end = sparse_rows.indptr[i], sparse_rows.indptr[i + 1]
            idx, vals = sparse_rows.indices[start:end], sparse_rows.data[start:end]
            if len(idx) == 0:
                continue
            Y = fixed[idx]
            c = alpha * vals                                           # extra confidence of the observed pairs
            A = YtY + (Y.T * c) @ Y
            b = Y.T @ (1 + c)
            out[i] = np.linalg.solve(A, b)
        return out

    for _ in range(iterations):
        U = solve(V, Xr, n_u)
        V = solve(U, Xc, n_i)
    return U, V

rows = []
for factors in [8, 16, 32, 64]:
    for alpha in [1.0, 3.0, 5.0, 15.0]:
        U, V = als(X_train, factors=factors, alpha=alpha)
        rows.append({"factors": factors, "alpha": alpha, **evaluate(U @ V.T, X_train, truth_valid)})
als_grid = pd.DataFrame(rows).set_index(["factors", "alpha"])
BEST_F, BEST_ALPHA = als_grid["ndcg@10"].idxmax()
print("best setting:", BEST_F, "factors, alpha", BEST_ALPHA)
als_grid.round(4)

final = {}
pop = np.tile(np.asarray((X_train_valid > 0).sum(axis=0)).ravel().astype(np.float32), (n_users, 1))
final["Popularity"] = evaluate(pop, X_train_valid, truth_test)
s_knn, similarity = item_item_scores(X_train_valid, BEST_K)
final["Item-item CF"] = evaluate(s_knn, X_train_valid, truth_test)
svd = TruncatedSVD(n_components=BEST_F, random_state=0).fit(X_train_valid)
final["Truncated SVD"] = evaluate(svd.transform(X_train_valid) @ svd.components_, X_train_valid, truth_test)
U, V = als(X_train_valid, factors=int(BEST_F), alpha=float(BEST_ALPHA))
final["Implicit ALS"] = evaluate(U @ V.T, X_train_valid, truth_test)
results = pd.DataFrame(final).T
results.round(4)

fig, axes = plt.subplots(1, 3, figsize=(11, 3.8))
colors = [GREY, BLUE, "#38a169", AMBER]
for ax, metric, title in zip(axes, ["recall@10", "ndcg@10", "coverage"], ["Recall@10 (higher is better)", "NDCG@10 (higher is better)", "Share of the catalogue ever recommended"]):
    ax.bar(results.index, results[metric], color=colors)
    for x, v in enumerate(results[metric]):
        ax.text(x, v, f"{v:.3f}", ha="center", va="bottom", fontsize=8)
    ax.set_title(title, fontsize=9)
    ax.tick_params(axis="x", rotation=25, labelsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

train_counts = np.asarray((X_train_valid > 0).sum(axis=1)).ravel()
bands = pd.cut(train_counts, [0, 20, 30, 36, 1000], right=False, labels=["under 20", "20-29", "30-35", "36 or more"])
users = np.array(sorted(truth_test.index))
scores = (U @ V.T)[users].astype(np.float32)
seen = X_train_valid[users].tocoo()
scores[seen.row, seen.col] = -np.inf
top = np.argsort(-scores, axis=1)[:, :K]
per_user = pd.DataFrame({
    "band": bands[users],
    "recall": [np.mean([a in truth_test[u] for a in row]) * K / len(truth_test[u]) for row, u in zip(top, users)],
    "popularity_recall": [np.mean([a in truth_test[u] for a in np.argsort(-pop[0] * (np.asarray(X_train_valid[u].todense()).ravel() == 0))[:K]]) * K / len(truth_test[u]) for u in users],
})
by_band = per_user.groupby("band", observed=True).mean().round(3)
by_band.columns = ["ALS recall@10", "Popularity recall@10"]
by_band.assign(users=per_user.groupby("band", observed=True).size())

names = artists.reindex(artist_ids)
name_to_item = {n: i for i, n in enumerate(names) if isinstance(n, str)}
Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-9)
seeds = [s for s in ["Radiohead", "Metallica", "Lady Gaga", "Miles Davis", "Daft Punk", "Bob Marley"] if s in name_to_item]
pd.DataFrame({seed: [names.iloc[j] for j in np.argsort(-(Vn @ Vn[name_to_item[seed]]))[1:6]] for seed in seeds},
             index=[f"#{i}" for i in range(1, 6)])

top_items = np.argsort(-item_popularity)[:70]
xy = PCA(n_components=2, random_state=0).fit_transform(Vn[top_items])
fig, ax = plt.subplots(figsize=(9.5, 6.4))
ax.scatter(xy[:, 0], xy[:, 1], s=20, color=AMBER)
for (x, y), item in zip(xy, top_items):
    ax.annotate(names.iloc[item], (x, y), fontsize=7, xytext=(3, 3), textcoords="offset points")
ax.set_title("The 70 most popular artists, placed by what the model learned about their listeners")
ax.set_xticks([])
ax.set_yticks([])
ax.grid(False)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
