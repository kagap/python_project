"""Spam or not? A text-message filter evaluated for precision, errors, robustness and data needs.

Plain-script version of the notebook. Set SMS_DIR to a folder for the downloaded zip.
"""


import os
import re
import urllib.request
import zipfile
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, confusion_matrix, f1_score, precision_recall_curve, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import GridSearchCV, StratifiedKFold, cross_validate, train_test_split
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import FeatureUnion, make_pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

DATA_DIR = Path(os.environ.get("SMS_DIR", "sms_data"))
DATA_DIR.mkdir(exist_ok=True)
AMBER, BLUE, GREY = "#e8a33d", "#2b6cb0", "#a0aec0"
plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
                     "grid.alpha": 0.25, "figure.dpi": 110})
pd.set_option("display.max_colwidth", 110)

zip_path = DATA_DIR / "sms_spam.zip"
if not zip_path.exists():
    urllib.request.urlretrieve("https://archive.ics.uci.edu/static/public/228/sms+spam+collection.zip", zip_path)
with zipfile.ZipFile(zip_path) as z:
    raw = pd.read_csv(z.open("SMSSpamCollection"), sep="\t", header=None, names=["label", "text"], quoting=3)
raw["spam"] = (raw.label == "spam").astype(int)

dup = raw.duplicated("text", keep=False)
print(f"{len(raw):,} messages, {raw.spam.mean():.1%} spam")
print(f"{raw.duplicated('text').sum()} are exact duplicates of an earlier message ({raw[raw.duplicated('text')].spam.mean():.0%} of those are spam)")
print(f"messages that appear with conflicting labels: {(raw.groupby('text').spam.nunique() > 1).sum()}")
df = raw.drop_duplicates("text").reset_index(drop=True)
print(f"after removing duplicates: {len(df):,} messages, {df.spam.mean():.1%} spam")

def traits(s):
    return pd.DataFrame({
        "characters": s.str.len(),
        "digits": s.str.count(r"\d"),
        "capitals": s.str.count(r"[A-Z]"),
        "has_number_of_6+_digits": s.str.contains(r"\d{6,}").astype(int),
        "has_currency": s.str.contains(r"[£$€]").astype(int),
        "has_link": s.str.contains(r"http|www\.", case=False).astype(int),
        "exclamation_marks": s.str.count("!"),
    })

t = traits(df.text).assign(spam=df.spam)
summary = t.groupby("spam").mean().T
summary.columns = ["ham", "spam"]
summary.round(2)

fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.7))
bins = np.arange(0, 330, 10)
axes[0].hist(t[t.spam == 0].characters, bins=bins, color=BLUE, alpha=0.8, label="Ham", density=True)
axes[0].hist(t[t.spam == 1].characters, bins=bins, color=AMBER, alpha=0.8, label="Spam", density=True)
axes[0].set_xlabel("Message length (characters)")
axes[0].set_ylabel("Share of messages")
axes[0].set_title("Spam is long and uniform, ham is short", fontsize=10)
axes[0].legend(frameon=False)
flags = ["has_number_of_6+_digits", "has_currency", "has_link"]
x = np.arange(len(flags))
axes[1].bar(x - 0.2, [t[t.spam == 0][f].mean() * 100 for f in flags], width=0.4, color=BLUE, label="Ham")
axes[1].bar(x + 0.2, [t[t.spam == 1][f].mean() * 100 for f in flags], width=0.4, color=AMBER, label="Spam")
axes[1].set_xticks(x, ["phone-like number\n(6+ digits)", "currency sign", "link"])
axes[1].set_ylabel("% of messages")
axes[1].set_title("Spam asks you to call, pay or click", fontsize=10)
axes[1].legend(frameon=False)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

train_text, test_text, y_train, y_test = train_test_split(df.text, df.spam, test_size=0.2, stratify=df.spam, random_state=0)
print(f"train {len(train_text):,} ({y_train.sum()} spam) | test {len(test_text):,} ({y_test.sum()} spam)")

def handmade(texts):
    s = pd.Series(list(texts))
    n = s.str.len().clip(lower=1)
    return np.c_[np.log1p(n), s.str.count(r"\d") / n, s.str.count(r"[A-Z]") / n, s.str.count("!"),
                 s.str.contains(r"http|www\.", case=False).astype(int), s.str.contains(r"[£$€]").astype(int)]

def words():
    return TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)

def chars():
    return TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=3, sublinear_tf=True)

pipelines = {
    "Naive Bayes (word counts)": make_pipeline(CountVectorizer(ngram_range=(1, 2), min_df=2), MultinomialNB(alpha=0.3)),
    "Logistic regression, words": make_pipeline(words(), LogisticRegression(C=10, max_iter=2000)),
    "Logistic regression, words + characters": make_pipeline(FeatureUnion([("w", words()), ("c", chars())]),
                                                            LogisticRegression(C=10, max_iter=2000)),
    "Logistic regression, + hand-made signals": make_pipeline(
        FeatureUnion([("w", words()), ("c", chars()),
                      ("h", make_pipeline(FunctionTransformer(handmade), StandardScaler()))]),
        LogisticRegression(C=10, max_iter=2000)),
}
cv = StratifiedKFold(5, shuffle=True, random_state=0)
rows = {}
for name, pipe in pipelines.items():
    res = cross_validate(pipe, train_text, y_train, cv=cv, scoring={"f1": "f1", "average_precision": "average_precision",
                                                                    "precision": "precision", "recall": "recall"})
    rows[name] = {f"{m} (mean)": res[f"test_{m}"].mean() for m in ["f1", "precision", "recall", "average_precision"]}
    rows[name]["f1 (std)"] = res["test_f1"].std()
cv_results = pd.DataFrame(rows).T
cv_results.round(4)

BEST_NAME = cv_results["average_precision (mean)"].idxmax()
print("chosen by cross-validation:", BEST_NAME)
grid = GridSearchCV(pipelines[BEST_NAME], {"logisticregression__C": [1, 3, 10, 30, 100]}, scoring="average_precision", cv=cv)
grid.fit(train_text, y_train)
print("best C:", grid.best_params_["logisticregression__C"], "| cross-validated average precision:", round(grid.best_score_, 4))
model = grid.best_estimator_

from sklearn.model_selection import cross_val_predict

p_test = model.predict_proba(test_text)[:, 1]
p_cv = cross_val_predict(clone(model), train_text, y_train, cv=cv, method="predict_proba")[:, 1]

prec, rec, thr = precision_recall_curve(y_train, p_cv)
ok = np.where(prec[:-1] >= 0.99)[0]
CAUTIOUS = float(thr[ok[0]]) if len(ok) else 0.9
print(f"high-precision threshold (precision >= 99% in cross-validation): {CAUTIOUS:.3f}")

def report(threshold):
    pred = (p_test >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_test, pred).ravel()
    return {"threshold": threshold, "precision": precision_score(y_test, pred), "recall": recall_score(y_test, pred),
            "F1": f1_score(y_test, pred), "ham blocked (false alarms)": fp, "spam missed": fn, "spam caught": tp}
final = pd.DataFrame({"usual cut-off": report(0.5), "high-precision cut-off": report(CAUTIOUS)}).T
print(f"ROC AUC {roc_auc_score(y_test, p_test):.4f} | average precision {average_precision_score(y_test, p_test):.4f}")
final.round(3)

fig, ax = plt.subplots(figsize=(6.6, 5))
prec_t, rec_t, thr_t = precision_recall_curve(y_test, p_test)
ax.plot(rec_t, prec_t, color=AMBER, lw=2.4)
for threshold, label, color in [(0.5, "0.5", BLUE), (CAUTIOUS, f"{CAUTIOUS:.2f} (high precision)", "#c0392b")]:
    r, p = recall_score(y_test, p_test >= threshold), precision_score(y_test, p_test >= threshold)
    ax.scatter([r], [p], s=70, color=color, zorder=3, label=f"cut-off {label}")
ax.set_xlabel("Recall (share of spam caught)")
ax.set_ylabel("Precision (share of blocked messages that are spam)")
ax.set_ylim(0.8, 1.01)
ax.set_xlim(0.5, 1.01)
ax.set_title("Precision-recall curve on the unseen messages")
ax.legend(frameon=False, loc="lower left")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

reader = make_pipeline(TfidfVectorizer(min_df=3, sublinear_tf=True), LogisticRegression(C=10, max_iter=2000)).fit(train_text, y_train)
vocab = np.array(reader[0].get_feature_names_out())
coef = reader[1].coef_[0]
top_spam = pd.Series(coef, index=vocab).nlargest(15)
top_ham = pd.Series(coef, index=vocab).nsmallest(15)
fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.6))
axes[0].barh(top_spam.index[::-1], top_spam.values[::-1], color=AMBER)
axes[0].set_title("Words that push towards spam", fontsize=10)
axes[1].barh(top_ham.index[::-1], -top_ham.values[::-1], color=BLUE)
axes[1].set_title("Words that push towards a normal message", fontsize=10)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead

errors = pd.DataFrame({"text": test_text, "spam": y_test, "p_spam": p_test})
errors["verdict"] = np.where((errors.p_spam >= 0.5) & (errors.spam == 0), "ham blocked",
                      np.where((errors.p_spam < 0.5) & (errors.spam == 1), "spam missed", ""))
wrong = errors[errors.verdict != ""].copy()
wrong["text"] = wrong.text.str.replace(r"\s+", " ", regex=True).str.slice(0, 115)
wrong["p_spam"] = wrong.p_spam.round(2)
wrong.sort_values(["verdict", "p_spam"], ascending=[True, False])[["verdict", "p_spam", "text"]].reset_index(drop=True)

spam_test = test_text[y_test == 1]
variants = {
    "original": spam_test,
    "all lower case": spam_test.str.lower(),
    "digits removed": spam_test.str.replace(r"\d", "", regex=True),
    "currency signs removed": spam_test.str.replace(r"[£$€]", "", regex=True),
    "digits and currency removed": spam_test.str.replace(r"[\d£$€]", "", regex=True),
}

def high_precision_threshold(pipeline):
    p = cross_val_predict(clone(pipeline), train_text, y_train, cv=cv, method="predict_proba")[:, 1]
    pr, rc, th = precision_recall_curve(y_train, p)
    ok = np.where(pr[:-1] >= 0.99)[0]
    return float(th[ok[0]]) if len(ok) else 0.9

hand_model = clone(pipelines["Logistic regression, + hand-made signals"]).set_params(logisticregression__C=10).fit(train_text, y_train)
candidates = {"chosen model (words + characters)": (model, CAUTIOUS),
              "with hand-made signals": (hand_model, high_precision_threshold(hand_model))}
stress = pd.DataFrame({label: {name: (m.predict_proba(v)[:, 1] >= thr).mean() for name, v in variants.items()}
                       for label, (m, thr) in candidates.items()})
fig, ax = plt.subplots(figsize=(9, 4))
y_pos = np.arange(len(stress))
ax.barh(y_pos + 0.2, stress.iloc[:, 0] * 100, height=0.4, color=AMBER, label=stress.columns[0])
ax.barh(y_pos - 0.2, stress.iloc[:, 1] * 100, height=0.4, color=BLUE, label=stress.columns[1])
ax.set_yticks(y_pos, stress.index)
ax.invert_yaxis()
ax.set_xlim(0, 100)
ax.set_xlabel("% of spam still caught at the model's high-precision cut-off")
ax.set_title("How well does the filter hold up against simple disguises?")
ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2, fontsize=8)
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
stress.round(3)

sizes = [50, 100, 200, 400, 800, 1600, len(train_text)]
rng = np.random.default_rng(0)
curve = []
for n in sizes:
    scores = []
    for _ in range(5):
        if n == len(train_text):
            idx = np.arange(len(train_text))
        else:
            idx = rng.choice(len(train_text), size=n, replace=False)
        ys = y_train.iloc[idx]
        if ys.nunique() < 2:
            continue
        m = clone(model).fit(train_text.iloc[idx], ys)
        pred = m.predict(test_text)
        scores.append((f1_score(y_test, pred), precision_score(y_test, pred, zero_division=0), recall_score(y_test, pred)))
    curve.append((n, *np.mean(scores, axis=0), np.std([s[0] for s in scores])))
curve = pd.DataFrame(curve, columns=["training messages", "F1", "precision", "recall", "F1 std"]).set_index("training messages")

fig, ax = plt.subplots(figsize=(8, 3.9))
ax.errorbar(curve.index, curve.F1, yerr=curve["F1 std"], marker="o", color=AMBER, capsize=3, label="F1")
ax.plot(curve.index, curve.precision, marker="s", ms=4, color=BLUE, label="Precision")
ax.plot(curve.index, curve.recall, marker="^", ms=4, color=GREY, label="Recall")
ax.set_xscale("log")
ax.set_xlabel("Training messages (log scale)")
ax.set_ylim(0, 1.02)
ax.set_title("Learning curve: more labelled messages, better filter")
ax.legend(frameon=False, loc="lower right")
fig.tight_layout()
plt.show()  # use plt.savefig(...) to save instead
curve.round(3)
