"""Second claim (SUGGESTIVE only): does a stall come before a phone pickup?

Pre-registered rules (see PROTOCOL.md):
  - Score moments t every 30 s.
  - Only moments where the user was active (input) within 10 s before t.
  - Target: a `phone` record in (t+30 s, t+3 min].
  - Features use only records with ts < t. "When" never sees phone records.
    "Where" (the app's own where_score) may see phone records BEFORE t, as in
    the live app. The "time since last pickup" baseline uses pickups before t.
Methods (higher score = pickup more likely):
  baselines: seconds idle; time since last pickup (shorter = higher)
  ours:      stall score (when); where rank (lower rank = higher);
             nudge rule (when + where, as the app decides it)
  optional:  logistic regression on when-features, trained on the first half
             of each person's data, tested on the second half (time split),
             and leave-one-person-out when there are 2+ people.
Metrics: AUC [95% CI, bootstrap over 10-minute blocks], false alarms per hour
at a matched alert rate, share of pickups caught >= 30 s ahead, median lead.

Usage:
  python evaluation/eval_pickups.py --events data/*.jsonl [--max-calls 60] [--offline]
"""
import argparse
import bisect
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from loophole import config, nudge, when_score, where_score  # noqa: E402
from evaluation.eval_titles import BudgetClient, auc, bootstrap_ci, fmt, make_client  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
CACHE = RESULTS / "where_pickup_cache.json"

STEP = 30            # seconds between scored moments
WARMUP = 10 * 60     # skip the first 10 min (no baseline typing rate yet)
LEAD_MIN = 30        # target window starts 30 s after t ...
LEAD_MAX = 180       # ... and ends 3 min after t
BLOCK = 600          # bootstrap block size (10 min)
FALLBACK_RATE = 0.05 # alert rate if the nudge rule almost never fires


# ---------------- pure helpers (tested in tests/test_eval.py) ----------------

def label_at(t, phone_ts):
    """1 if a pickup happens in (t+30, t+180], else 0. phone_ts must be sorted."""
    i = bisect.bisect_right(phone_ts, t + LEAD_MIN)
    return int(i < len(phone_ts) and phone_ts[i] <= t + LEAD_MAX)


def active_at(inputs, t):
    """Was the user active within ACTIVE_WITHIN s before t? Uses inputs with ts < t only.
    inputs: sorted list of (ts, idle_any)."""
    i = bisect.bisect_left(inputs, (t, -1.0)) - 1  # last input strictly before t
    if i < 0:
        return False
    ts, idle_any = inputs[i]
    return idle_any + (t - ts) <= config.ACTIVE_WITHIN


def flag_top(scores, rate, seed=0):
    """Flag exactly round(rate * n) moments with the highest scores.
    Ties are broken at random (seeded), so the alert rate is exactly matched."""
    scores = np.asarray(scores, float)
    k = int(round(rate * len(scores)))
    tie = np.random.default_rng(seed).random(len(scores))
    order = np.lexsort((tie, -scores))  # sort by score desc, then random
    flags = np.zeros(len(scores), bool)
    flags[order[:k]] = True
    return flags


def catch_stats(times, flags, pickups):
    """For each pickup p, was there an alert at t in [p-180, p-30)?
    That is exactly 'warned 30 s or more ahead'. Returns (caught share, median lead s)."""
    alert_t = np.sort(np.asarray(times)[np.asarray(flags, bool)])
    leads = []
    for p in pickups:
        lo = bisect.bisect_left(alert_t, p - LEAD_MAX)
        hi = bisect.bisect_left(alert_t, p - LEAD_MIN)
        if hi > lo:
            leads.append(p - alert_t[lo])  # earliest alert in the window
    caught = len(leads) / len(pickups) if len(pickups) else None
    return caught, (float(np.median(leads)) if leads else None)


# ---------------- build the table of scored moments ----------------

def load(path):
    recs = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    recs.sort(key=lambda r: r["ts"])
    synthetic = any(r.get("synthetic") for r in recs if r["kind"] == "session")
    return recs, synthetic


def moments_for(person, recs):
    """One row per eligible moment for one person. No API calls here; where comes later."""
    feats_src = [r for r in recs if r["kind"] in when_score.ALLOWED_KINDS]  # no phone, ever
    feats_ts = [r["ts"] for r in feats_src]
    where_src = [r for r in recs if r["kind"] in ("app", "phone")]
    where_ts = [r["ts"] for r in where_src]
    phone_ts = sorted(r["ts"] for r in recs if r["kind"] == "phone")
    inputs = [(r["ts"], float(r["idle"]["any"])) for r in recs if r["kind"] == "input"]
    if not inputs:
        return [], [], where_src
    start, end = inputs[0][0], inputs[-1][0]

    rows = []
    t = start + WARMUP
    while t + LEAD_MAX <= end:
        if active_at(inputs, t):
            past = feats_src[:bisect.bisect_left(feats_ts, t)]  # ts < t only
            f = when_score.features(past, t)
            j = bisect.bisect_left(phone_ts, t)  # pickups strictly before t
            since = t - (phone_ts[j - 1] if j else start)
            rows.append({"person": person, "t": t, "y": label_at(t, phone_ts),
                         "features": f, "stall": when_score.stall_score(f),
                         "idle_s": f["idle_s"], "since_pickup": since,
                         "active_recent": f.get("active_recent", True),
                         "where_n": bisect.bisect_left(where_ts, t)})
        t += STEP
    # pickups whose warning window lies inside the scored time range
    # (the denominator for "caught"; includes pickups with no active moment before them)
    last_grid = t - STEP
    pickups = [p for p in phone_ts if start + WARMUP + LEAD_MIN < p <= last_grid + LEAD_MAX]
    return rows, pickups, where_src


def add_where(rows, where_srcs, client, seed=0):
    """Where rank for each moment. Moments are visited in random order so that,
    if the live-call budget runs out, the moments with a rank are a random
    subset (not just the morning). Cached answers are free."""
    order = np.random.default_rng(seed).permutation(len(rows))
    for i in order:
        r = rows[i]
        src = where_srcs[r["person"]][: r["where_n"]]  # ts < t only
        w = where_score.score(src, r["t"], client=client, cache_path=CACHE)
        r["where_rank"] = w["rank"]


# ---------------- metrics ----------------

def evaluate_method(name, rows, score, flags, pickups_by_person, n_moments_total):
    y = np.array([r["y"] for r in rows])
    times = np.array([r["t"] for r in rows])
    groups = [f"{r['person']}-{int(r['t'] // BLOCK)}" for r in rows]
    s = np.asarray(score, float)
    a = auc(y, s)
    ci = bootstrap_ci(lambda i: auc(y[i], s[i]), len(y), groups=groups)
    hours = len(rows) * STEP / 3600
    fa = float(((flags == 1) & (y == 0)).sum() / hours) if hours else None
    caught, leads, n_p = [], [], 0
    for person, pickups in pickups_by_person.items():
        m = np.array([r["person"] == person for r in rows], bool)
        if not m.any():
            continue
        c, lead = catch_stats(times[m], flags[m], pickups)
        if c is not None:
            caught.append(c * len(pickups))
            n_p += len(pickups)
        if lead is not None:
            leads.append(lead)
    return {"method": name, "n_moments": len(rows), "n_positive": int(y.sum()),
            "auc": a, "auc_ci": ci, "alert_rate": float(flags.mean()) if len(flags) else None,
            "false_alarms_per_hour": fa,
            "caught_30s_ahead": (sum(caught) / n_p) if n_p else None, "n_pickups": n_p,
            "median_lead_s": float(np.median(leads)) if leads else None}


FEATURE_KEYS = ["key_drop", "scroll_no_type", "burst_pause_count", "dwell_no_input_s",
                "switches_per_min", "minutes_since_break", "idle_s", "key_rate_now"]


def logistic(rows):
    """Optional: logistic regression on when-features. Time split + leave-one-person-out."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    X = np.array([[r["features"].get(k, 0) or 0 for k in FEATURE_KEYS] for r in rows], float)
    y = np.array([r["y"] for r in rows])
    people = np.array([r["person"] for r in rows])
    t = np.array([r["t"] for r in rows])
    groups = np.array([f"{p}-{int(x // BLOCK)}" for p, x in zip(people, t)])
    out = {}

    # time split: first half of each person's moments trains, second half tests
    train = np.zeros(len(rows), bool)
    for p in set(people):
        m = people == p
        train[m] = t[m] < np.median(t[m])
    out["time_split"] = _fit_score(X, y, groups, train, ~train)

    if len(set(people)) >= 2:  # leave one person out
        pred = np.full(len(rows), np.nan)
        for p in set(people):
            test = people == p
            if len(set(y[~test])) < 2:
                continue
            model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))
            model.fit(X[~test], y[~test])
            pred[test] = model.predict_proba(X[test])[:, 1]
        ok = ~np.isnan(pred)
        yy, pp, gg = y[ok], pred[ok], groups[ok]
        out["leave_one_person_out"] = {
            "n": int(ok.sum()), "auc": auc(yy, pp),
            "auc_ci": bootstrap_ci(lambda i: auc(yy[i], pp[i]), len(yy), groups=gg)}
    return out


def _fit_score(X, y, groups, train, test):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    if len(set(y[train])) < 2 or len(set(y[test])) < 2:
        return {"n": int(test.sum()), "auc": None, "auc_ci": (None, None)}
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))
    model.fit(X[train], y[train])
    p = model.predict_proba(X[test])[:, 1]
    yy, gg = y[test], groups[test]
    return {"n": int(test.sum()), "auc": auc(yy, p),
            "auc_ci": bootstrap_ci(lambda i: auc(yy[i], p[i]), len(yy), groups=gg)}


# ---------------- main ----------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--events", nargs="+", required=True, help="one JSONL file per person")
    ap.add_argument("--max-calls", type=int, default=60, help="cap on live BehaviorGPT calls")
    ap.add_argument("--offline", action="store_true", help="cached where scores only")
    args = ap.parse_args()

    rows, pickups_by_person, where_srcs, synthetic = [], {}, {}, False
    for path in args.events:
        recs, syn = load(path)
        synthetic |= syn
        person = Path(path).stem
        r, pickups, wsrc = moments_for(person, recs)
        rows += r
        pickups_by_person[person] = pickups
        where_srcs[person] = wsrc
    if not rows:
        print("No eligible moments found.")
        return

    budget = {"left": 0 if args.offline else args.max_calls, "used": 0}
    client = BudgetClient(None if args.offline else make_client(), budget)
    RESULTS.mkdir(parents=True, exist_ok=True)
    add_where(rows, where_srcs, client)

    # The nudge rule as the app runs it (no cooldown here: we judge each moment).
    fire = np.array([nudge.decide(r["stall"], r["where_rank"], r["active_recent"], None, r["t"])[0]
                     for r in rows])
    rate = fire.mean() if fire.mean() >= 0.01 else FALLBACK_RATE

    has_where = np.array([r["where_rank"] is not None for r in rows])
    wrows = [r for r, h in zip(rows, has_where) if h]
    methods = [
        ("idle seconds (baseline)", rows, [r["idle_s"] for r in rows]),
        ("time since pickup (baseline)", rows, [-r["since_pickup"] for r in rows]),
        ("stall score (when)", rows, [r["stall"] for r in rows]),
        ("where rank", wrows, [-r["where_rank"] for r in wrows]),
    ]
    results = []
    for name, rr, sc in methods:
        if not rr:
            continue
        flipped = False
        if "baseline" in name:
            # Be generous to baselines: if the guessed direction is worse than
            # chance, flip it. This can only make our methods look worse.
            a = auc([r["y"] for r in rr], sc)
            if a is not None and a < 0.5:
                sc, flipped = [-x for x in sc], True
                name += " (flipped)"
        results.append(evaluate_method(name, rr, sc, flag_top(sc, rate), pickups_by_person, len(rows)))
        results[-1]["flipped"] = flipped
    results.append(evaluate_method("nudge rule (when+where)", rows, fire.astype(float), fire,
                                   pickups_by_person, len(rows)))
    lr = logistic(rows)

    tag = "SYNTHETIC DATA - NOT A RESULT. " if synthetic else ""
    print(f"\n{tag}Stall-before-pickup: SUGGESTIVE evidence only.")
    print(f"People: {len(pickups_by_person)}  scored moments: {len(rows)} "
          f"({len(rows) * STEP / 3600:.1f} active hours)  positive moments: {sum(r['y'] for r in rows)}  "
          f"pickups: {sum(map(len, pickups_by_person.values()))}")
    print(f"Moments with a where rank: {int(has_where.sum())}  live API calls this run: {budget['used']}")
    print(f"Matched alert rate: {rate:.1%} of moments "
          f"({'nudge rule rate' if rate == fire.mean() else 'fallback 5%'})\n")
    print(f"{'method':<42}{'n':>5}  {'AUC [95% CI]':<20}{'FA/hour':>8}{'caught>=30s':>12}{'median lead':>12}")
    for r in results:
        lead = f"{r['median_lead_s']:.0f}s" if r["median_lead_s"] is not None else "n/a"
        caught = f"{r['caught_30s_ahead']:.0%}" if r["caught_30s_ahead"] is not None else "n/a"
        print(f"{r['method']:<42}{r['n_moments']:>5}  {fmt(r['auc'], r['auc_ci']):<20}"
              f"{r['false_alarms_per_hour']:>8.1f}{caught:>12}{lead:>12}")
    for k, v in lr.items():
        print(f"{'logistic regression, ' + k.replace('_', ' '):<42}{v['n']:>5}  {fmt(v['auc'], v['auc_ci']):<20}")
    print("\nCIs resample 10-minute blocks, because moments 30 s apart are not independent.")
    print("Baselines marked (flipped) were scored in their better direction (generous to them).")
    print("FA/hour and caught use the same alert rate for every method except the nudge rule,")
    print("which fires at its own rate (that rate is the matched rate when it is >= 1%).")

    out = {"synthetic": synthetic, "verdict": "suggestive", "files": args.events,
           "n_moments": len(rows), "n_positive": int(sum(r["y"] for r in rows)),
           "n_pickups": sum(map(len, pickups_by_person.values())),
           "n_with_where": int(has_where.sum()), "live_calls": budget["used"],
           "matched_alert_rate": float(rate), "methods": results, "logistic": lr,
           "rules": {"step_s": STEP, "target_window_s": [LEAD_MIN, LEAD_MAX],
                     "active_within_s": config.ACTIVE_WITHIN, "warmup_s": WARMUP}}
    (RESULTS / "pickups.json").write_text(json.dumps(out, indent=1))
    print(f"wrote {RESULTS / 'pickups.json'}")


if __name__ == "__main__":
    main()
