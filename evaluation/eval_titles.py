"""Headline test: can BehaviorGPT tell on-task from off-task titles on the SAME site?

Reads evaluation/titles.csv (owner,title,domain,label). Each owner labels their
own rows on/off BEFORE running this script (see PROTOCOL.md). Compares:
  (a) domain rule  - youtube/reddit/tiktok/... = off-task
  (b) keyword rule - "tutorial", "how to", ... = on; "prank", "vlog", ... = off;
                     otherwise the domain rule
  (c) BehaviorGPT  - put the title after a fixed, neutral work history and ask
                     for the next item. Score = rank of the first distraction
                     (d_) item (lower = more off-task) and the share of d_ items
                     in the top 10.
Prints accuracy and AUC with 95% bootstrap intervals and writes
evaluation/results/titles.json.

Scoring goes through focusguard/where_score.score(), the same code the app
uses. API answers are cached in evaluation/results/where_title_cache.json, so a
rerun makes no new calls. --max-calls caps live calls (default 100).

Usage:
  python evaluation/eval_titles.py                     # real labeled rows only
  python evaluation/eval_titles.py --include-examples  # also the made-up rows
"""
import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from focusguard import config  # noqa: E402
from focusguard.catalog import is_distraction  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
CACHE = RESULTS / "where_title_cache.json"  # same format as where_score's cache

# ---- pre-registered rules (do not change after seeing scores) ----
OFF_DOMAINS = ("youtube.com", "reddit.com", "tiktok.com", "instagram.com", "twitter.com",
               "x.com", "facebook.com", "netflix.com", "twitch.tv", "9gag.com", "buzzfeed.com")
ON_WORDS = ("tutorial", "how to", "lecture", "docs", "course")
OFF_WORDS = ("prank", "vlog", "reaction", "funny", "compilation", "drama")


# ---------------- shared statistics (eval_pickups.py uses these too) ----------------

def auc(y, s):
    """Area under the ROC curve. None when only one class is present."""
    y = np.asarray(y)
    if len(set(y.tolist())) < 2:
        return None
    return float(roc_auc_score(y, s))


def bootstrap_ci(stat, n, groups=None, reps=2000, seed=0):
    """95% percentile interval for stat(idx), where idx is a resample of row indexes.

    groups: optional list with a group id per row. Then whole groups are
    resampled (a cluster bootstrap), which is the honest choice when rows
    next to each other are not independent (e.g. moments 30 s apart).
    Resamples where stat() returns None (e.g. one class only) are skipped.
    Returns (low, high), or (None, None) if too few resamples worked.
    """
    rng = np.random.default_rng(seed)
    if groups is None:
        blocks = [np.array([i]) for i in range(n)]
    else:
        groups = np.asarray(groups)
        blocks = [np.flatnonzero(groups == g) for g in dict.fromkeys(groups.tolist())]
    vals = []
    for _ in range(reps):
        pick = rng.integers(0, len(blocks), len(blocks))
        idx = np.concatenate([blocks[i] for i in pick])
        v = stat(idx)
        if v is not None:
            vals.append(v)
    if len(vals) < reps * 0.5:
        return (None, None)
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5)))


def fmt(v, ci=None):
    if v is None:
        return "  n/a"
    s = f"{v:.2f}"
    if ci and ci[0] is not None:
        s += f" [{ci[0]:.2f}, {ci[1]:.2f}]"
    return s


# ---------------- the three methods ----------------

def domain_rule(domain: str) -> int:
    """1 = off-task."""
    d = (domain or "").lower().removeprefix("www.")
    return int(any(d == x or d.endswith("." + x) for x in OFF_DOMAINS))


def keyword_rule(title: str, domain: str) -> int:
    """1 = off-task. On-task words win if both kinds appear."""
    t = (title or "").lower()
    if any(w in t for w in ON_WORDS):
        return 0
    if any(w in t for w in OFF_WORDS):
        return 1
    return domain_rule(domain)


# The fixed neutral work history, as tracker `app` records. where_score turns
# these into catalog items: Code -> f_vscode, github.com -> f_github, ...
def _app(app, bundle=None, domain=None, title=None):
    return {"kind": "app", "app": app, "bundle": bundle, "domain": domain,
            "url": f"https://{domain}/" if domain else None, "title": title}


WORK_RECORDS = [
    _app("Code", "com.microsoft.VSCode"),
    _app("Google Chrome", "com.google.Chrome", "github.com", "Pull request #12"),
    _app("Code", "com.microsoft.VSCode"),
    _app("Google Chrome", "com.google.Chrome", "stackoverflow.com", "A question"),
    _app("Google Chrome", "com.google.Chrome", "docs.python.org", "json"),
    _app("Terminal", "com.apple.Terminal"),
    _app("Code", "com.microsoft.VSCode"),
]


class BudgetClient:
    """Wraps the real client and refuses calls past the budget.
    where_score.score() turns the refusal into rank=None (it never raises)."""

    def __init__(self, real, budget):
        self.real, self.budget = real, budget

    def complete(self, *a, **kw):
        if self.real is None or self.budget["left"] <= 0:
            raise RuntimeError("live-call budget used up (or no client)")
        self.budget["left"] -= 1
        self.budget["used"] += 1
        return self.real.complete(*a, **kw)


def work_plus_title(title, domain):
    """Records: 7 work events, then the title as a browser tab (made 1 s apart)."""
    recs = [dict(r, ts=float(i)) for i, r in enumerate(WORK_RECORDS)]
    if title is not None:
        recs.append(dict(_app("Google Chrome", "com.google.Chrome", domain, title),
                         ts=float(len(recs))))
    return recs


def bgpt_score(title, domain, client):
    """Score one title with the SAME code the app uses (where_score.score).
    Returns dict(rank, share, ...); rank is 0-based (0 = a distraction is the
    top guess). rank None = API failure / over budget."""
    from focusguard import where_score
    recs = work_plus_title(title, domain)
    return where_score.score(recs, t=len(recs) + 1.0, client=client, cache_path=CACHE)


def make_client():
    try:
        from behaviorgpt import UnboxAIClient
        return UnboxAIClient(market="us", api_key=config.API_KEY)
    except Exception as e:
        print(f"No BehaviorGPT client ({e}); using cached scores only.")
        return None


# ---------------- main ----------------

def load_rows(path, include_examples):
    rows, skipped = [], {"unlabeled": 0, "example": 0}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            label = (r.get("label") or "").strip().lower()
            if r["owner"].strip() == "example" and not include_examples:
                skipped["example"] += 1
                continue
            if label not in ("on", "off"):
                skipped["unlabeled"] += 1
                continue
            rows.append({**r, "label": label})
    return rows, skipped


def evaluate(rows, client, budget):
    y = np.array([int(r["label"] == "off") for r in rows])  # 1 = off-task
    preds = {"domain rule": [], "keyword rule": []}
    bg = []
    for r in rows:
        preds["domain rule"].append(domain_rule(r["domain"]))
        preds["keyword rule"].append(keyword_rule(r["title"], r["domain"]))
        b = bgpt_score(r["title"], r["domain"], client)
        bg.append(b if b["rank"] is not None else None)

    # BehaviorGPT: rows where the call failed are left out (n is printed).
    ok = np.array([b is not None for b in bg])
    rank = np.array([b["rank"] if b else np.nan for b in bg], float)
    share = np.array([b["share"] if b else np.nan for b in bg])

    methods = {
        # name: (score where higher = more off-task, 0/1 prediction, rows used)
        "domain rule": (np.array(preds["domain rule"], float), np.array(preds["domain rule"]), np.ones(len(y), bool)),
        "keyword rule": (np.array(preds["keyword rule"], float), np.array(preds["keyword rule"]), np.ones(len(y), bool)),
        "BehaviorGPT rank": (-rank, (rank <= config.RANK_THRESHOLD).astype(int), ok),
        "BehaviorGPT top-10 share": (share, (share >= 0.5).astype(int), ok),
    }
    out = {}
    for name, (score, pred, use) in methods.items():
        idx_all = np.flatnonzero(use)
        yy, ss, pp = y[idx_all], score[idx_all], pred[idx_all]
        acc_fn = lambda i: float((pp[i] == yy[i]).mean())  # noqa: E731
        auc_fn = lambda i: auc(yy[i], ss[i])  # noqa: E731
        all_i = np.arange(len(yy))
        out[name] = {
            "n": int(len(yy)),
            "accuracy": acc_fn(all_i) if len(yy) else None,
            "accuracy_ci": bootstrap_ci(acc_fn, len(yy)) if len(yy) else (None, None),
            "auc": auc_fn(all_i) if len(yy) else None,
            "auc_ci": bootstrap_ci(auc_fn, len(yy)) if len(yy) else (None, None),
        }
    # Paired difference: BehaviorGPT rank AUC minus keyword-rule AUC, same rows.
    idx_ok = np.flatnonzero(ok)
    kw = methods["keyword rule"][0][idx_ok]
    br = (-rank)[idx_ok]
    yy = y[idx_ok]

    def diff(i):
        a, b = auc(yy[i], br[i]), auc(yy[i], kw[i])
        return None if a is None or b is None else a - b
    d = diff(np.arange(len(yy))) if len(yy) else None
    out["_diff_bgpt_rank_minus_keyword_auc"] = {
        "value": d, "ci": bootstrap_ci(diff, len(yy)) if len(yy) else (None, None)}
    per_row = [{"owner": r["owner"], "title": r["title"], "domain": r["domain"],
                "label": r["label"], "keyword": k, "bgpt_rank": None if b is None else b["rank"],
                "bgpt_share": None if b is None else b["share"]}
               for r, k, b in zip(rows, preds["keyword rule"], bg)]
    return out, per_row


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--titles", default=str(HERE / "titles.csv"))
    ap.add_argument("--include-examples", action="store_true",
                    help="also use the made-up owner=example rows (pipeline test only)")
    ap.add_argument("--max-calls", type=int, default=100, help="cap on live API calls")
    ap.add_argument("--offline", action="store_true", help="cached scores only, no API calls")
    args = ap.parse_args()

    rows, skipped = load_rows(args.titles, args.include_examples)
    print(f"Titles used: {len(rows)}  (skipped {skipped['unlabeled']} unlabeled, "
          f"{skipped['example']} example rows)")
    if not rows:
        print("Nothing to score. Label rows in titles.csv first (on|off).")
        return
    n_off = sum(r["label"] == "off" for r in rows)
    print(f"Labels: {n_off} off-task, {len(rows) - n_off} on-task")
    if args.include_examples:
        print("NOTE: includes made-up EXAMPLE rows. Not a result.")

    budget = {"left": 0 if args.offline else args.max_calls, "used": 0}
    client = BudgetClient(None if args.offline else make_client(), budget)
    RESULTS.mkdir(parents=True, exist_ok=True)
    base = bgpt_score(None, None, client)  # work history alone, for context
    res, per_row = evaluate(rows, client, budget)
    print(f"Live API calls this run: {budget['used']} (rest from cache)\n")

    print(f"{'method':<26}{'n':>4}  {'accuracy [95% CI]':<22}{'AUC [95% CI]':<22}")
    for name, r in res.items():
        if name.startswith("_"):
            continue
        print(f"{name:<26}{r['n']:>4}  {fmt(r['accuracy'], r['accuracy_ci']):<22}"
              f"{fmt(r['auc'], r['auc_ci']):<22}")
    d = res["_diff_bgpt_rank_minus_keyword_auc"]
    print(f"\nAUC difference, BehaviorGPT rank minus keyword rule (paired): "
          f"{fmt(d['value'], d['ci'])}")
    print("Accuracy thresholds (pre-registered, same as the app): rank <= "
          f"{config.RANK_THRESHOLD} = off; share >= 0.5 = off. Ranks are 0-based.")
    print(f"Context: work history with no title gives rank {base['rank']}, share {base['share']}.")

    RESULTS.mkdir(parents=True, exist_ok=True)
    out = {"n": len(rows), "n_off": n_off, "includes_examples": args.include_examples,
           "skipped": skipped, "live_calls": budget["used"], "methods": res, "rows": per_row,
           "work_history_items": base["items_sent"],
           "work_only_rank": base["rank"], "work_only_share": base["share"]}
    (RESULTS / "titles.json").write_text(json.dumps(out, indent=1))
    print(f"wrote {RESULTS / 'titles.json'}")


if __name__ == "__main__":
    main()
