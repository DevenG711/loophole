"""Second claim (SUGGESTIVE only): does a stall come before a phone pickup?

Pre-registered rules (see PROTOCOL.md):
  - Score moments t every 30 s.
  - Only moments where the user was active (input) within 10 s before t.
  - Target: a pickup in (t+30 s, t+3 min].
  - Features use only records with ts < t. "When" never sees phone records.
    "Where" (the app's own where_score) may see phone records BEFORE t, as in
    the live app. The "time since last pickup" baseline uses pickups before t.
Methods (higher score = pickup more likely):
  baselines: seconds idle; time since last pickup (shorter = higher)
  ours:      stall score (when); where rank (lower rank = higher);
             nudge rule (when + where)
  optional:  logistic regression on when-features, trained on the first half
             of each person's data, tested on the second half (time split),
             and leave-one-person-out when there are 2+ people.
Metrics: AUC [95% CI, bootstrap over 10-minute blocks], false alarms per hour
at a matched alert rate, share of pickups caught >= 30 s ahead, median lead.

Pre-registered vs tuned (see "Deviations" in PROTOCOL.md):
  pre-registered  where rank = first d_* item; nudge = stall >= STALL_THRESHOLD
                  and rank <= RANK_THRESHOLD (10) and input within 10 s.
  old when+where  the previous live rule (nudge.decide_when_where): rank skips
                  STICKY_DISTRACTIONS, rank <= NUDGE_RANK_THRESHOLD (3) and the
                  "just returned" rule. Tuned IN-SAMPLE on the files given here.
  live (memory)   the live rule since 2026-11-08 (PROTOCOL.md deviation 9):
                  nudge only when precursor.match(records before t, t)["text"]
                  is set, or the pickup pace is high (pace.pace, deviation 14),
                  and active_recent (no cooldown: we judge each moment).
                  Leave-future-out by construction. POST-HOC.

Labels (added 2026-11-08, also in the deviations note):
  - phone records with a source in precursor.NOT_EPISODE_SOURCES are not
    pickups: "test" pings, and "button"/"hotkey" self-reports (they usually
    answer a nudge). Dropped counts are reported per source. The where history
    still sees button/hotkey records, as the live app does;
  - --skip-pickups HH:MM-HH:MM drops phone records in that local time range
    (repeatable), e.g. Shortcut setup testing;
  - a phone record less than config.PICKUP_MERGE_S after an episode's FIRST
    record joins that episode (no chaining; same rule as precursor). Episodes
    are timed at their first record. Labels and counts use episodes.
  - nudge records with staged: true (demo button, --demo mode) are ignored.
  - "caught" in subset tables counts only pickups inside that subset's scored
    range.
  - pickups precursor tags `dev_testing` are counted and get one sensitivity
    row (memory with their positives left out). Tag only, never a filter.
  - results/pickups.json records each log's size and last record time.

Post-hoc (added 2026-11-08, PROTOCOL.md deviation 7-8):
  - "pickup memory (post-hoc, leave-future-out)": precursor.match(records
    before t, t)["score"]. It sees the same filtered phone records as the
    labels (no test pings, no --skip-pickups ranges), and only those before t.
  - moments within 3 min after a real (not staged) nudge are counted and also
    left out in a separate table, since a nudge can change behaviour.
  - "BehaviorGPT pull (where_gap)": where_score.cached_signal(history before
    t, t)["gap"], from the caches only (never the API). Coverage is reported.

Usage:
  python evaluation/eval_pickups.py --events data/events-*.jsonl [--max-calls 60] [--offline]
                                    [--skip-pickups 13:34-13:37]
"""
import argparse
import bisect
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from loophole import config, events, nudge, pace, precursor, when_score, where_score  # noqa: E402
from evaluation.eval_titles import BudgetClient, auc, bootstrap_ci, fmt, make_client  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
CACHE = RESULTS / "where_pickup_cache.json"
LIVE_CACHE = where_score.CACHE_FILE  # read-only here: answers the live app already got

STEP = 30            # seconds between scored moments
WARMUP = 10 * 60     # skip the first 10 min (no baseline typing rate yet)
LEAD_MIN = 30        # target window starts 30 s after t ...
LEAD_MAX = 180       # ... and ends 3 min after t
BLOCK = 600          # bootstrap block size (10 min)
FALLBACK_RATE = 0.05 # alert rate if the nudge rule almost never fires
SMALL_N = 20         # below this many positives, say plainly that n is tiny
BURST_WINDOW = 60    # >= BURST_APPS different apps within this many s ...
BURST_APPS = 3       # ... looks like a test burst, not a real pickup
MEMORY_ROW = "pickup memory (post-hoc, leave-future-out)"
MEMORY_RULE_ROW = "nudge rule [live: memory text or pace, post-hoc]"
PACE_ROW = "pickups in last 10 min (pace, post-hoc)"
OLD_RULE_ROW = "nudge rule [old when+where, rank<=%d]" % config.NUDGE_RANK_THRESHOLD
GAP_ROW = "BehaviorGPT pull (where_gap, cache only)"


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


# ---------------- pickup labels: test pings, skipped ranges, episodes ----------------

def _clock(s):
    parts = [int(x) for x in s.strip().split(":")]
    if not 2 <= len(parts) <= 3:
        raise ValueError(s)
    h, m, sec = (parts + [0])[:3]
    if not (0 <= h <= 24 and 0 <= m < 60 and 0 <= sec < 60):
        raise ValueError(s)
    return h * 3600 + m * 60 + sec


def parse_skip(spec):
    """'13:34-13:37' or '13:34:49-13:36:50' -> (start, end) in seconds after
    local midnight. The range is [start, end). Applies to every day in the files."""
    try:
        a, b = spec.split("-")
        start, end = _clock(a), _clock(b)
    except ValueError:
        raise ValueError(f"bad --skip-pickups range {spec!r}; use HH:MM-HH:MM (local time)")
    if end <= start:
        raise ValueError(f"bad --skip-pickups range {spec!r}: end must be after start")
    return start, end


def in_skip(ts, ranges):
    """Is ts (unix seconds) inside one of the local time-of-day ranges?"""
    if not ranges:
        return False
    d = dt.datetime.fromtimestamp(ts)
    tod = d.hour * 3600 + d.minute * 60 + d.second + d.microsecond / 1e6
    return any(a <= tod < b for a, b in ranges)


def phone_app(rec):
    """Normalised phone app name ('app=TikTok' -> 'tiktok')."""
    app = (rec.get("app") or "").strip()
    if app.lower().startswith("app="):  # records saved before phone.py parsed Form bodies
        app = app[4:]
    return app.strip().lower()


def pickup_records(recs, skip=()):
    """Phone records that count as real pickups, plus counts of what was dropped.
    Drops every source in precursor.NOT_EPISODE_SOURCES (test pings, "Grabbed
    phone" button and hotkey self-reports: they usually answer a nudge, so as
    labels they would make a nudge look like a correct prediction) and records
    inside a --skip-pickups range. Returns (kept, {"dropped_source": {source: n},
    "skipped_range": n})."""
    kept, by_source, n_skip = [], {}, 0
    for r in recs:
        if r.get("kind") != "phone":
            continue
        src = r.get("source")
        if src in precursor.NOT_EPISODE_SOURCES:
            by_source[src] = by_source.get(src, 0) + 1
        elif in_skip(r["ts"], skip):
            n_skip += 1
        else:
            kept.append(r)
    kept.sort(key=lambda r: r["ts"])
    return kept, {"dropped_source": by_source, "skipped_range": n_skip}


def where_phone_records(recs, skip=()):
    """Phone records the "where" history may see: as the live where_score,
    everything but test pings (button/hotkey presses stay in the history),
    minus --skip-pickups ranges. Sorted by ts."""
    return sorted((r for r in recs if r.get("kind") == "phone" and r.get("source") != "test"
                   and not in_skip(r["ts"], skip)), key=lambda r: r["ts"])


def merge_episodes(phone_recs, gap=None):
    """Pickup episodes, same rule as precursor._episode_list: a record joins
    the current episode only if it is less than `gap` s (default
    config.PICKUP_MERGE_S) after the episode's FIRST record; otherwise it
    starts a new one (no chaining). The episode's time is its first record.
    Returns [{"ts", "end", "n", "events": [(ts, app), ...]}]."""
    gap = config.PICKUP_MERGE_S if gap is None else gap
    eps = []
    for r in sorted(phone_recs, key=lambda r: r["ts"]):
        if eps and r["ts"] - eps[-1]["ts"] < gap:
            eps[-1]["end"] = r["ts"]
            eps[-1]["n"] += 1
            eps[-1]["events"].append((r["ts"], phone_app(r)))
        else:
            eps.append({"ts": r["ts"], "end": r["ts"], "n": 1, "events": [(r["ts"], phone_app(r))]})
    return eps


def looks_like_test_burst(ep, window=BURST_WINDOW, min_apps=BURST_APPS):
    """True if >= min_apps different apps were opened within `window` s.
    Real pickups rarely do that; setting up the Shortcut does."""
    ev = ep["events"]
    for i, (t0, _) in enumerate(ev):
        apps = {a for t, a in ev[i:] if t - t0 <= window}
        if len(apps) >= min_apps:
            return True
    return False


def real_nudges(recs):
    """Nudge records, minus demo stagings (staged: true)."""
    return [r for r in recs if r.get("kind") == "nudge" and not r.get("staged")]


def shown_nudge_times(recs):
    """Sorted times of real (not staged) nudges that were shown."""
    return sorted(r["ts"] for r in real_nudges(recs) if r.get("action") == "shown")


def after_nudge(t, nudge_ts, window=LEAD_MAX):
    """True if a real nudge was shown in [t - window, t). nudge_ts must be sorted.
    Only nudges before t count, so this never looks ahead."""
    i = bisect.bisect_left(nudge_ts, t)  # nudges strictly before t
    return i > 0 and nudge_ts[i - 1] >= t - window


def memory_records(recs, phones):
    """Records the pickup memory may see: everything except phone records, plus
    only the phone records that count as pickups (`phones` from pickup_records,
    so test pings and --skip-pickups ranges are gone). Sorted by ts."""
    return sorted([r for r in recs if r.get("kind") != "phone"] + list(phones),
                  key=lambda r: r["ts"])


def add_memory(rows, mem_srcs):
    """Post-hoc pickup-memory score per moment. Uses only records with ts < t.
    Sets r["memory"] (None if precursor can't be imported or gives no score).
    Returns an error string, or None."""
    try:
        from loophole import precursor
    except Exception as e:  # noqa: BLE001
        for r in rows:
            r["memory"] = None
        return f"precursor import failed: {e}"
    ts_cache = {p: [x["ts"] for x in src] for p, src in mem_srcs.items()}
    for r in rows:
        src = mem_srcs[r["person"]]
        past = src[: bisect.bisect_left(ts_cache[r["person"]], r["t"])]  # ts < t only
        m = precursor.match(past, r["t"])
        r["memory"] = m.get("score")
        r["memory_ready"] = bool(m.get("ready"))
        r["memory_n_episodes"] = m.get("n_episodes")
        r["memory_text"] = m.get("text")   # the live nudge fires on this
        # Pickup pace (deviation 14): same past records, same filtered pickups.
        r["pace"] = pace.pace(past, r["t"])
    return None


def add_where_gap(rows, where_srcs, caches=None):
    """BehaviorGPT's lean to distraction (where_gap) per moment, from the cache
    files only (eval cache, then the live app's cache). Never calls the API.
    Uses the same history as the where rank: records before t, real pickups only.
    Sets r["where_gap"] (None if not cached). Returns (n with a gap, error or None)."""
    cached_signal = getattr(where_score, "cached_signal", None)
    load_cache = getattr(where_score, "load_cache", None)
    if cached_signal is None or load_cache is None:
        for r in rows:
            r["where_gap"] = None
        return 0, "where_score has no cached_signal/load_cache yet"
    loaded = []
    for path in (caches if caches is not None else (CACHE, LIVE_CACHE)):
        try:
            c = load_cache(path)
        except Exception:  # noqa: BLE001
            c = {}
        if c:
            loaded.append(c)
    n = 0
    for r in rows:
        src = where_srcs[r["person"]][: r["where_n"]]  # ts < t only
        gap = None
        for c in loaded:
            try:
                sig = cached_signal(src, r["t"], cache=c)
            except Exception:  # noqa: BLE001
                sig = None
            if sig and sig.get("gap") is not None:
                gap = sig["gap"]
                break
        r["where_gap"] = gap
        n += gap is not None
    return n, None


# ---------------- build the table of scored moments ----------------

def load(path):
    recs = list(events.read(path))   # skips cut-off lines instead of crashing
    recs.sort(key=lambda r: r["ts"])
    synthetic = any(r.get("synthetic") for r in recs if r["kind"] == "session")
    return recs, synthetic


def moments_for(person, recs, skip=()):
    """One row per eligible moment for one person. No API calls here; where comes later.
    Returns (rows, pickup episode times, where source records)."""
    feats_src = [r for r in recs if r["kind"] in when_score.ALLOWED_KINDS]  # no phone, ever
    feats_ts = [r["ts"] for r in feats_src]
    phones, _ = pickup_records(recs, skip)
    phone_ts = [e["ts"] for e in merge_episodes(phones)]  # episode starts, sorted
    # where sees app records and phone records as the live app does (test pings
    # and skipped ranges are not history; button/hotkey presses are).
    where_src = sorted([r for r in recs if r["kind"] == "app"] + where_phone_records(recs, skip),
                       key=lambda r: r["ts"])
    where_ts = [r["ts"] for r in where_src]
    inputs = [(r["ts"], float(r["idle"]["any"])) for r in recs if r["kind"] == "input"]
    nudge_ts = shown_nudge_times(recs)
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
            idle_s = f.get("idle_s", 0.0) or 0.0
            rows.append({"person": person, "t": t, "y": label_at(t, phone_ts),
                         "features": f, "stall": when_score.stall_score(f),
                         "idle_s": idle_s, "since_pickup": since,
                         # time-trend nulls (post-hoc, deviation 10): pickups got
                         # more frequent over the session, and the memory only
                         # scores > 0 once a pickup exists
                         "elapsed_s": t - start, "pickups_so_far": j,
                         # tuned (live) definition, includes "just returned"
                         "active_recent": bool(f.get("active_recent", True)),
                         # pre-registered definition: input within ACTIVE_WITHIN s
                         "active_prereg": idle_s <= config.ACTIVE_WITHIN,
                         "where_n": bisect.bisect_left(where_ts, t),
                         "after_nudge": after_nudge(t, nudge_ts)})
        t += STEP
    # pickups whose warning window lies inside the scored time range
    # (the denominator for "caught"; includes pickups with no active moment before them)
    last_grid = t - STEP
    pickups = [p for p in phone_ts if start + WARMUP + LEAD_MIN < p <= last_grid + LEAD_MAX]
    return rows, pickups, where_src


class _CacheOnly:
    """A client that never calls the API: score() then reads the cache or returns rank None."""

    def complete(self, *a, **kw):
        raise RuntimeError("cache only")


def where_ranks(src, t, client, caches=(CACHE, LIVE_CACHE)):
    """(pre-registered rank, tuned rank, source). Reads the eval cache, then the
    live app's cache (read-only), then asks the API through `client` (budgeted).
    Both ranks come from the same raw ids, so the second lookup is a cache hit.
    Missing answers give (None, None, "missing"). Never raises."""
    no_api = _CacheOnly()
    for path in caches:
        if not Path(path).exists():
            continue
        w = where_score.score(src, t, client=no_api, cache_path=path, skip_sticky=False)
        if w["rank"] is not None:
            tuned = where_score.score(src, t, client=no_api, cache_path=path, skip_sticky=True)
            return w["rank"], tuned["rank"], ("eval cache" if path == CACHE else "live cache")
    w = where_score.score(src, t, client=client, cache_path=CACHE, skip_sticky=False)
    if w["rank"] is None:
        return None, None, ("no history" if not w["items_sent"] else "missing")
    tuned = where_score.score(src, t, client=no_api, cache_path=CACHE, skip_sticky=True)
    return w["rank"], tuned["rank"], "api"


def add_where(rows, where_srcs, client, seed=0):
    """Where ranks for each moment. Moments are visited in random order so that,
    if the live-call budget runs out, the moments with a rank are a random
    subset (not just the morning). Cached answers are free.
    Returns a count of where each answer came from."""
    counts = {}
    order = np.random.default_rng(seed).permutation(len(rows))
    for i in order:
        r = rows[i]
        src = where_srcs[r["person"]][: r["where_n"]]  # ts < t only
        pre, tuned, how = where_ranks(src, r["t"], client)
        r["rank_prereg"], r["rank_tuned"], r["where_source"] = pre, tuned, how
        counts[how] = counts.get(how, 0) + 1
    return counts


def nudge_prereg(r):
    """The pre-registered nudge rule (PLAN.md): stall high, a distraction in the
    top RANK_THRESHOLD (first d_* item, no sticky skip), input within 10 s."""
    return bool(r["active_prereg"] and r["stall"] >= config.STALL_THRESHOLD
                and r["rank_prereg"] is not None and r["rank_prereg"] <= config.RANK_THRESHOLD)


def nudge_old(r):
    """The previous live rule (stall AND BehaviorGPT rank), as
    nudge.decide_when_where runs it (no cooldown: we judge each moment)."""
    rule = getattr(nudge, "decide_when_where", None) or nudge.decide
    return bool(rule(r["stall"], r["rank_tuned"], r["active_recent"], None, r["t"])[0])


def nudge_memory(r):
    """The live rule since 2026-11-08: nudge.decide with the pickup memory's
    answer and the pickup pace at t (records before t only). No cooldown: we
    judge each moment."""
    memory = {"text": r.get("memory_text"), "ready": r.get("memory_ready"),
              "n_episodes": r.get("memory_n_episodes")}
    return bool(nudge.decide(r["stall"], r.get("rank_tuned"), r["active_recent"], None, r["t"],
                             memory=memory, pace=r.get("pace"))[0])


nudge_tuned = nudge_memory   # "tuned live" now means the live (memory) rule


# ---------------- metrics ----------------

def evaluate_method(name, rows, score, flags, pickups_by_person, n_moments_total=None):
    y = np.array([r["y"] for r in rows], int)
    flags = np.asarray(flags, bool)
    times = np.array([r["t"] for r in rows])
    groups = [f"{r['person']}-{int(r['t'] // BLOCK)}" for r in rows]
    s = np.asarray(score, float)
    a = auc(y, s) if len(y) else None
    ci = bootstrap_ci(lambda i: auc(y[i], s[i]), len(y), groups=groups) if len(y) else (None, None)
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


def pickups_in_rows(rows, pickups_by_person):
    """The "caught" denominator for a subset of moments: per person, only the
    pickups inside that subset's scored range, i.e. first moment + 30 s < p <=
    last moment + 180 s (the same rule moments_for uses for all moments).
    E.g. the first pickup leaves the "after first pickup" table. People with no
    moments in the subset get no pickups."""
    out = {}
    for person, pickups in pickups_by_person.items():
        ts = [r["t"] for r in rows if r["person"] == person]
        out[person] = ([p for p in pickups if min(ts) + LEAD_MIN < p <= max(ts) + LEAD_MAX]
                       if ts else [])
    return out


def run_methods(rows, pickups_by_person):
    """All methods on one set of moments. Returns (results, matched alert rate, rate source)."""
    pre_rows = [r for r in rows if r["rank_prereg"] is not None]
    tun_rows = [r for r in rows if r["rank_tuned"] is not None]
    mem_rows = [r for r in rows if r.get("memory") is not None]
    fire_pre = np.array([nudge_prereg(r) for r in pre_rows], bool)
    fire_old = np.array([nudge_old(r) for r in tun_rows], bool)
    fire_mem = np.array([nudge_memory(r) for r in mem_rows], bool)
    # Matched rate = the PRE-REGISTERED nudge rule's rate (protocol), or 5%.
    if len(fire_pre) and fire_pre.mean() >= 0.01:
        rate, rate_src = float(fire_pre.mean()), "pre-registered nudge rule rate"
    else:
        rate, rate_src = FALLBACK_RATE, "fallback 5%"

    methods = [
        ("idle seconds (baseline)", rows, [r["idle_s"] for r in rows]),
        ("time since pickup (baseline)", rows, [-r["since_pickup"] for r in rows]),
        # Post-hoc nulls (PROTOCOL.md deviation 10): a method that cannot beat
        # the clock is not predicting pickups.
        ("elapsed time (baseline)", rows, [r.get("elapsed_s", 0.0) for r in rows]),
        ("pickups so far (baseline)", rows, [r.get("pickups_so_far", 0) for r in rows]),
        ("stall score (when)", rows, [r["stall"] for r in rows]),
        ("where rank [pre-registered]", pre_rows, [-r["rank_prereg"] for r in pre_rows]),
        ("where rank [tuned: sticky skip]", tun_rows, [-r["rank_tuned"] for r in tun_rows]),
    ]
    # Post-hoc row (PROTOCOL.md deviation 7). Added after the pre-registered
    # rows; it does not change their moments or the matched rate.
    methods.append((MEMORY_ROW, mem_rows, [r["memory"] for r in mem_rows]))
    # Post-hoc (deviation 9): BehaviorGPT's lean to distraction, cached moments only.
    gap_rows = [r for r in rows if r.get("where_gap") is not None]
    methods.append((GAP_ROW, gap_rows, [r["where_gap"] for r in gap_rows]))
    # Post-hoc (deviation 14): pickups in the last 10 min, the pace nudge's count.
    pace_rows = [r for r in rows if r.get("pace") is not None]
    methods.append((PACE_ROW, pace_rows, [r["pace"]["recent"] for r in pace_rows]))
    results = []
    for name, rr, sc in methods:
        if not rr:
            results.append({"method": name, "n_moments": 0, "skipped": "no moments with a score"})
            continue
        flipped = False
        if "baseline" in name:
            # Be generous to baselines: if the guessed direction is worse than
            # chance, flip it. This can only make our methods look worse.
            a = auc([r["y"] for r in rr], sc)
            if a is not None and a < 0.5:
                sc, flipped = [-x for x in sc], True
                name += " (flipped)"
        results.append(evaluate_method(name, rr, sc, flag_top(sc, rate), pickups_by_person))
        results[-1]["flipped"] = flipped
    for name, rr, fire, why in (
            ("nudge rule [pre-registered, rank<=%d]" % config.RANK_THRESHOLD, pre_rows, fire_pre,
             "no moments with a where rank"),
            (OLD_RULE_ROW, tun_rows, fire_old, "no moments with a where rank"),
            (MEMORY_RULE_ROW, mem_rows, fire_mem, "no pickup-memory answers")):
        if not rr:
            results.append({"method": name, "n_moments": 0, "skipped": why})
            continue
        results.append(evaluate_method(name, rr, fire.astype(float), fire, pickups_by_person))
    return results, rate, rate_src


DEV_ROW = MEMORY_ROW + " [dev_testing positives left out, tag]"


def dev_testing_times(mem_recs):
    """Episode times precursor tags `dev_testing` (us testing the pipeline),
    from precursor.episodes() on the records the memory sees. Tag only."""
    return sorted(e["ts"] for e in precursor.episodes(mem_recs) if e.get("dev_testing"))


def dev_testing_row(rows, pickups_by_person, dev_by_person, rate):
    """Sensitivity row (PROTOCOL.md deviation 10/12): the pickup memory with the
    dev_testing pickups' positives left out. A moment is dropped if it is
    positive only because of dev_testing pickups; those pickups also leave the
    "caught" denominator. Never used for the headline."""
    keep = []
    for r in rows:
        if r.get("memory") is None:
            continue
        if r["y"]:
            pickups = pickups_by_person.get(r["person"], [])
            dev = set(dev_by_person.get(r["person"], []))
            ahead = [p for p in pickups if r["t"] + LEAD_MIN < p <= r["t"] + LEAD_MAX]
            if ahead and all(p in dev for p in ahead):
                continue
        keep.append(r)
    if not keep:
        return {"method": DEV_ROW, "n_moments": 0, "skipped": "no moments left"}
    pick = {p: [x for x in v if x not in set(dev_by_person.get(p, []))]
            for p, v in pickups_by_person.items()}
    sc = [r["memory"] for r in keep]
    res = evaluate_method(DEV_ROW, keep, sc, flag_top(sc, rate), pick)
    res["flipped"] = False
    return res


def _rank_auc(pos, neg):
    """P(a positive scores higher than a negative), ties half. None if a side is empty."""
    if not pos or not neg:
        return None
    return sum((p > n) + 0.5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))


def memory_signal_check(mem_recs):
    """Post-hoc diagnostic, evaluation only, never feeds the live rule
    (PROTOCOL.md deviation 15): is there anything in laptop state before a
    pickup for the pickup memory to find? Uses the memory's own pieces
    (precursor._Log, match_vector, _scaled, _spreads, _distance) over the whole
    log at once, so it is a look-back, not leave-future-out. Never calls
    engine.score_now(). Returns a dict (also printed)."""
    P = precursor
    if not mem_recs:
        return {"skipped": "no records"}
    T = max(r["ts"] for r in mem_recs) + 1
    log = P._Log(mem_recs, T)
    if not log.inputs or not log.episodes:
        return {"skipped": "no inputs or no pickups"}
    lead = config.PRECURSOR_LEAD_S

    # pickup breakdown: why each pickup is (not) a template
    unusable = {"app_off": 0, "idle": 0, "little_input": 0}
    for e in log.episodes:
        if e["usable"]:
            continue
        s = e["ts"] - lead
        i = bisect.bisect_left(log.input_ts, s) - 1
        if i < 0 or s - log.input_ts[i] > P.MAX_STEP_S:
            unusable["app_off"] += 1          # no input sample right before: tracker not running
        elif (log.idle_at(s) or 0) >= config.SETTLED_MAX_IDLE_S:
            unusable["idle"] += 1
        else:
            unusable["little_input"] += 1
    n_dev = sum(1 for e in log.templates if e["dev_testing"])

    # grid and base rate, same rules as precursor.match
    lo, hi = config.PRECURSOR_TARGET_S
    grid, m = [], log.input_ts[0] + config.PRECURSOR_LOOKBACK_S
    while m < T:
        idle = log.idle_at(m)
        if idle is not None and idle < config.SETTLED_MAX_IDLE_S:
            grid.append(m)
        m += config.PRECURSOR_GRID_S
    gv = {g: P._scaled(log.match_vector(g)) for g in grid}
    spread = P._spreads(list(gv.values()))
    ep_ts = [e["ts"] for e in log.episodes]

    def followed(g, a=lo, b=hi):
        return bisect.bisect_right(ep_ts, g + b) > bisect.bisect_right(ep_ts, g + a)
    known = [g for g in grid if g + hi < T]
    fl = {g: followed(g) for g in known}
    base = sum(fl.values()) / len(known) if known else None
    snaps = [(e, P._scaled(log.match_vector(e["ts"] - lead))) for e in log.templates]

    def near_dist(g, only=None):
        ds = [P._distance(gv[g], v, spread) for e, v in snaps
              if abs(e["ts"] - lead - g) >= config.PRECURSOR_LOOKBACK_S and (only is None or only(e))]
        return min(ds) if ds else float("inf")

    cut = config.PRECURSOR_MATCH_DIST
    nd = {g: near_dist(g) for g in known}
    share_any = sum(1 for g in known if nd[g] <= cut) / len(known) if known else None
    per_tmpl = []
    for e, v in snaps:
        s = e["ts"] - lead
        mm = [g for g in known if abs(s - g) >= config.PRECURSOR_LOOKBACK_S
              and P._distance(gv[g], v, spread) <= cut]
        per_tmpl.append(len(mm) / len(known) if known else 0.0)

    def dist_auc(dmap):     # closer to a template = higher score
        fin = [(d, fl[g]) for g, d in dmap.items() if d != float("inf")]
        return _rank_auc([-d for d, y in fin if y], [-d for d, y in fin if not y])
    auc_all = dist_auc(nd)
    auc_nodev = dist_auc({g: near_dist(g, only=lambda e: not e["dev_testing"]) for g in known})

    bands = []
    for a, b in [(0, .5), (.5, .75), (.75, 1.0), (1.0, float("inf"))]:
        sel = [fl[g] for g in known if a <= nd[g] < b]
        rate = sum(sel) / len(sel) if sel else None
        bands.append({"band": [a, b], "n": len(sel), "followed_rate": rate,
                      "lift": (rate / base) if (rate is not None and base) else None})
    feat_auc = {}
    for k in P.MATCH_FEATURES:
        vals = [(gv[g][k], fl[g]) for g in known if gv[g].get(k) is not None]
        feat_auc[k] = _rank_auc([x for x, y in vals if y], [x for x, y in vals if not y])
    wide = {}
    for a, b in [(30, 180), (30, 300), (30, 600)]:
        kk = [g for g in grid if g + b < T]
        r = sum(followed(g, a, b) for g in kk) / len(kk) if kk else None
        wide[f"{a}-{b}"] = {"base_rate": r, "max_lift": (1 / r) if r else None}

    def f2(x):
        return "n/a" if x is None else f"{x:.3f}"
    print("\n== memory signal check (post-hoc diagnostic, evaluation only, never feeds the live rule) ==")
    print(f"pickups: {len(log.episodes)} episodes, {len(log.templates)} usable ({n_dev} of them "
          f"dev_testing); unusable: {unusable['app_off']} app off at the snapshot, "
          f"{unusable['idle']} laptop idle, {unusable['little_input']} too little input")
    if base:
        print(f"grid base rate (pickup in {lo}-{hi} s): {sum(fl.values())}/{len(known)} = {base:.3f}; "
              f"highest possible lift {1 / base:.2f}")
    print(f"share of grid moments within {cut:g} of some template: {f2(share_any)}; per template: "
          f"min {min(per_tmpl):.2f}, median {float(np.median(per_tmpl)):.2f}, max {max(per_tmpl):.2f}")
    print(f"AUC of nearest-template distance (templates within {config.PRECURSOR_LOOKBACK_S} s left "
          f"out): {f2(auc_all)}")
    print("  by distance band: " + "; ".join(
        f"[{b['band'][0]:g},{b['band'][1]:g}) n={b['n']} rate={f2(b['followed_rate'])} "
        f"lift={f2(b['lift'])}" for b in bands))
    print("  single-feature AUC (higher value -> pickup): "
          + ", ".join(f"{k} {f2(v)}" for k, v in feat_auc.items()))
    print(f"  sensitivity, dev_testing=false templates only (deviation 12; a row, not a filter): "
          f"AUC {f2(auc_nodev)}")
    print("  base rate by target window: " + ", ".join(
        f"{k} s {f2(v['base_rate'])} (max lift {f2(v['max_lift'])})" for k, v in wide.items()))
    return {"post_hoc": True, "n_episodes": len(log.episodes), "n_usable": len(log.templates),
            "n_usable_dev_testing": n_dev, "unusable": unusable, "n_known": len(known),
            "n_followed": int(sum(fl.values())), "base_rate": base,
            "share_matched_any": share_any, "share_matched_per_template": per_tmpl,
            "auc_nearest": auc_all, "auc_nearest_no_dev_testing": auc_nodev,
            "distance_bands": bands, "feature_auc": feat_auc, "target_windows": wide}


def print_table(title, rows, results, rate, rate_src):
    n_pos = sum(r["y"] for r in rows)
    print(f"\n== {title} ==")
    print(f"moments: {len(rows)} ({len(rows) * STEP / 3600:.1f} active hours)  positive moments: {n_pos}")
    print(f"Matched alert rate: {rate:.1%} of moments ({rate_src})")
    print(f"{'method':<44}{'n':>5}{'pos':>5}  {'AUC [95% CI]':<20}{'FA/hour':>8}{'caught>=30s':>12}"
          f"{'median lead':>12}")
    for r in results:
        if r.get("skipped"):
            print(f"{r['method']:<44}{0:>5}{'':>5}  (skipped: {r['skipped']})")
            continue
        lead = f"{r['median_lead_s']:.0f}s" if r["median_lead_s"] is not None else "n/a"
        caught = f"{r['caught_30s_ahead']:.0%}" if r["caught_30s_ahead"] is not None else "n/a"
        fa = f"{r['false_alarms_per_hour']:.1f}" if r["false_alarms_per_hour"] is not None else "n/a"
        print(f"{r['method']:<44}{r['n_moments']:>5}{r['n_positive']:>5}  {fmt(r['auc'], r['auc_ci']):<20}"
              f"{fa:>8}{caught:>12}{lead:>12}")


# ---------------- main ----------------

def _hm(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--events", nargs="+", required=True, help="one JSONL file per person")
    ap.add_argument("--max-calls", type=int, default=60, help="cap on live BehaviorGPT calls")
    ap.add_argument("--offline", action="store_true", help="cached where scores only")
    ap.add_argument("--skip-pickups", action="append", default=[], metavar="HH:MM-HH:MM",
                    help="drop phone records in this LOCAL time range (e.g. Shortcut setup "
                         "testing); repeatable. Say so when you report results.")
    args = ap.parse_args(argv)
    try:
        skip = [parse_skip(s) for s in args.skip_pickups]
    except ValueError as e:
        ap.error(str(e))

    rows, pickups_by_person, where_srcs, mem_srcs, synthetic = [], {}, {}, {}, False
    label_info, dev_by_person, source_logs = {}, {}, []
    nudge_info = {"real_shown": 0, "staged": 0, "real_followed_by_pickup": 0}
    for path in args.events:
        recs, syn = load(path)
        synthetic |= syn
        person = Path(path).stem
        last_ts = max((x["ts"] for x in recs), default=None)
        source_logs.append({"file": str(path), "bytes": Path(path).stat().st_size,
                            "n_records": len(recs), "last_record_ts": last_ts,
                            "last_record_local": (dt.datetime.fromtimestamp(last_ts).isoformat(
                                timespec="seconds") if last_ts is not None else None)})
        r, pickups, wsrc = moments_for(person, recs, skip)
        rows += r
        pickups_by_person[person] = pickups
        where_srcs[person] = wsrc

        phones, dropped = pickup_records(recs, skip)
        mem_srcs[person] = memory_records(recs, phones)  # same pickups as the labels
        eps = merge_episodes(phones)
        try:
            dev_by_person[person] = dev_testing_times(mem_srcs[person])
        except Exception as e:  # noqa: BLE001
            print(f"dev_testing tag failed for {person}: {e!r}")
            dev_by_person[person] = []
        label_info[person] = {"phone_records": sum(1 for x in recs if x["kind"] == "phone"),
                              **dropped, "episodes": len(eps), "episodes_in_scored_range": len(pickups),
                              "episodes_dev_testing": len(dev_by_person[person]),
                              "episodes_dev_testing_in_scored_range":
                                  len(set(dev_by_person[person]) & set(pickups))}
        for e in eps:
            if looks_like_test_burst(e):
                apps = ", ".join(a for _, a in e["events"])
                print(f"WARNING {person}: pickup episode {_hm(e['ts'])}-{_hm(e['end'])} has "
                      f">= {BURST_APPS} different apps within {BURST_WINDOW} s ({apps}): looks like a "
                      f"test burst. If it was, rerun with --skip-pickups "
                      f"{_hm(e['ts'])[:5]}-{_hm(e['end'] + 60)[:5]} and report that you did.")
        ep_ts = [e["ts"] for e in eps]
        for nrec in (x for x in recs if x.get("kind") == "nudge" and x.get("action") == "shown"):
            if nrec.get("staged"):
                nudge_info["staged"] += 1
                continue
            nudge_info["real_shown"] += 1
            if any(nrec["ts"] < p <= nrec["ts"] + LEAD_MAX for p in ep_ts):
                nudge_info["real_followed_by_pickup"] += 1  # pickup within 3 min after the nudge
    if not rows:
        print("No eligible moments found.")
        return

    budget = {"left": 0 if args.offline else args.max_calls, "used": 0}
    client = BudgetClient(None if args.offline else make_client(), budget)
    RESULTS.mkdir(parents=True, exist_ok=True)
    where_counts = add_where(rows, where_srcs, client)
    memory_error = add_memory(rows, mem_srcs)
    n_gap, gap_error = add_where_gap(rows, where_srcs)

    files = ", ".join(Path(p).name for p in args.events)
    tag = "SYNTHETIC DATA - NOT A RESULT. " if synthetic else ""
    n_eps = sum(map(len, pickups_by_person.values()))
    n_pos = sum(r["y"] for r in rows)
    print(f"\n{tag}Stall-before-pickup: SUGGESTIVE evidence only.")
    for person, li in label_info.items():
        by_src = ", ".join(f"{n} {s}" for s, n in sorted(li["dropped_source"].items())) or "none"
        print(f"{person}: {li['phone_records']} phone records; dropped by source: {by_src} "
              f"(not pickups: {sorted(precursor.NOT_EPISODE_SOURCES)}); "
              f"{li['skipped_range']} in --skip-pickups ranges; {li['episodes']} pickup episodes "
              f"(records < {config.PICKUP_MERGE_S} s after an episode's first record merged), "
              f"{li['episodes_in_scored_range']} inside the scored range; "
              f"{li['episodes_dev_testing']} tagged dev_testing "
              f"({li['episodes_dev_testing_in_scored_range']} in the scored range; tag only)")
    if skip:
        print(f"Skipped pickup ranges (local time): {', '.join(args.skip_pickups)}")
    print(f"Live nudges shown: {nudge_info['real_shown']} (ignored {nudge_info['staged']} staged demo "
          f"nudges); followed by a pickup within 3 min: {nudge_info['real_followed_by_pickup']}")
    missing = where_counts.get("missing", 0) + where_counts.get("no history", 0)
    print(f"Where rank sources: {where_counts}  live API calls this run: {budget['used']}")
    if missing:
        print(f"  {missing} of {len(rows)} moments have NO where rank (not cached"
              f"{', offline' if args.offline else ' and over the call budget'}). "
              "Where and nudge-rule rows use only moments that have one.")
    n_not_recent = sum(1 for r in rows if not r["active_recent"])
    print(f"{n_not_recent} of {len(rows)} moments pass the pre-registered active check (input "
          f"within {config.ACTIVE_WITHIN} s) but fail the live 'active_recent' rule (just back "
          "from a pause); the live nudge can never fire there.")
    print("Note: eval moments are a fixed 30 s grid from the first input; live `score` records "
          "have other timestamps. That is expected.")
    if n_pos < SMALL_N or n_eps < SMALL_N:
        print(f"n is tiny ({n_pos} positive moments from {n_eps} pickup episodes), suggestive only.")

    if memory_error:
        print(f"Pickup memory row skipped: {memory_error}")
    else:
        n_ready = sum(1 for r in rows if r.get("memory_ready"))
        n_text = sum(1 for r in rows if r.get("memory_text"))
        print(f"Pickup memory (post-hoc): {n_ready} of {len(rows)} moments had enough past pickups "
              f"(>= {config.PRECURSOR_MIN_EPISODES}) to be 'ready'; the score row scores every moment. "
              f"Memory text (the live nudge trigger) was set at {n_text} moments.")
        n_pace = sum(1 for r in rows if (r.get("pace") or {}).get("high"))
        cds = [r["pace"]["cooldown_s"] for r in rows if (r.get("pace") or {}).get("usual_gap_s")]
        print(f"Pickup pace (post-hoc, deviation 14): pace was high (the other live nudge trigger) "
              f"at {n_pace} of {len(rows)} moments; personal cooldown known at {len(cds)} moments"
              + (f", median {np.median(cds) / 60:.1f} min." if cds else "."))
    signal_check = {}
    for person, src in mem_srcs.items():
        print(f"{person}:", end="")
        try:
            signal_check[person] = memory_signal_check(src)
        except Exception as e:  # noqa: BLE001
            signal_check[person] = {"error": repr(e)}
            print(f" memory signal check failed: {e!r}")
    print(f"BehaviorGPT pull (where_gap): {n_gap} of {len(rows)} moments have a cached answer with "
          f"scores (coverage {n_gap / len(rows):.0%}); the row uses only those."
          + (f" ({gap_error})" if gap_error else ""))
    after = [r for r in rows if r["after_nudge"]]
    print(f"{len(after)} of {len(rows)} moments are within {LEAD_MAX // 60} min after a real nudge "
          f"({sum(r['y'] for r in after)} positive). A nudge can change behaviour; see the table "
          "that leaves them out.")

    res_all, rate_all, src_all = run_methods(rows, pickups_by_person)
    print_table("All pre-registered moments (input within 10 s)", rows, res_all, rate_all, src_all)
    res_dev = (dev_testing_row(rows, pickups_by_person, dev_by_person, rate_all) if not memory_error
               else {"method": DEV_ROW, "n_moments": 0, "skipped": memory_error})
    print("Sensitivity (tag, not a filter; the row above is the headline):")
    print_table("Pickup memory without dev_testing pickups' positives", rows, [res_dev],
                rate_all, src_all)
    # Subset tables: "caught" counts only pickups inside the subset's scored
    # range (pickups_in_rows), e.g. the first pickup can't be caught "after
    # the first pickup".
    def subset(rr):
        return (run_methods(rr, pickups_in_rows(rr, pickups_by_person)) if rr
                else ([], FALLBACK_RATE, "none"))
    fire_rows = [r for r in rows if r["active_recent"]]
    res_fire, rate_fire, src_fire = subset(fire_rows)
    print_table("Only moments where the live nudge could fire (active_recent)", fire_rows,
                res_fire, rate_fire, src_fire)
    calm_rows = [r for r in rows if not r["after_nudge"]]
    res_calm, rate_calm, src_calm = subset(calm_rows)
    print_table(f"Leaving out moments within {LEAD_MAX // 60} min after a real nudge", calm_rows,
                res_calm, rate_calm, src_calm)
    first_rows = [r for r in rows if r.get("pickups_so_far", 0) > 0]
    res_first, rate_first, src_first = subset(first_rows)
    print_table("Only moments after the person's first pickup (memory has something to compare; "
                "post-hoc, deviation 10)", first_rows, res_first, rate_first, src_first)
    lr = logistic(rows)
    for k, v in lr.items():
        print(f"{'logistic regression, ' + k.replace('_', ' '):<44}{v['n']:>5}{'':>5}  {fmt(v['auc'], v['auc_ci']):<20}")

    print("\n[pre-registered] = rules in PROTOCOL.md: first d_* item, rank <= "
          f"{config.RANK_THRESHOLD}, input within {config.ACTIVE_WITHIN} s.")
    print(f"[tuned] / [old when+where] = the previous live rule (sticky skip, rank <= "
          f"{config.NUDGE_RANK_THRESHOLD}, just-returned rule), tuned in-sample on {files}. "
          "Do not read them as out-of-sample.")
    print(f"'{MEMORY_RULE_ROW}' = the live rule since 2026-11-08 (PROTOCOL.md deviation 9): nudge only")
    print("when the pickup memory has text at t, or the pickup pace is high (deviation 14), from records\n"
          "before t. Changed post-hoc (user decision).")
    print(f"'{GAP_ROW}' is post-hoc too; it uses only moments with a cached BehaviorGPT answer.")
    print("CIs resample 10-minute blocks, because moments 30 s apart are not independent.")
    print("Baselines marked (flipped) were scored in their better direction (generous to them).")
    print("FA/hour and caught use the matched alert rate for every scored method; the nudge rules")
    print("fire at their own rate.")
    print(f"'{MEMORY_ROW}' is POST-HOC (PROTOCOL.md deviation 7): the idea came after looking at")
    print("this data. At each moment it knows only pickups before t. PRECURSOR_* thresholds were set")
    print("before it was first run.")

    out = {"synthetic": synthetic, "verdict": "suggestive", "files": args.events,
           # what this file was built from, so a stale pickups.json is visible
           "built_at": dt.datetime.now().isoformat(timespec="seconds"),
           "source_logs": source_logs,
           "skip_pickups": args.skip_pickups, "labels": label_info, "nudges": nudge_info,
           "n_moments": len(rows), "n_positive": int(n_pos), "n_pickup_episodes": n_eps,
           "n_not_active_recent": n_not_recent, "where_sources": where_counts,
           "live_calls": budget["used"],
           "all_moments": {"matched_alert_rate": rate_all, "methods": res_all},
           "could_fire_moments": {"n": len(fire_rows), "matched_alert_rate": rate_fire,
                                  "methods": res_fire},
           "after_nudge": {"n_moments": len(after), "n_positive": int(sum(r["y"] for r in after)),
                           "window_s": LEAD_MAX},
           "without_after_nudge_moments": {"n": len(calm_rows), "matched_alert_rate": rate_calm,
                                           "methods": res_calm},
           "after_first_pickup_moments": {"post_hoc": True, "n": len(first_rows),
                                          "n_positive": int(sum(r["y"] for r in first_rows)),
                                          "matched_alert_rate": rate_first,
                                          "methods": res_first},
           "dev_testing": {"tag_only": True,
                           "n_episodes": sum(len(v) for v in dev_by_person.values()),
                           "memory_without_dev_positives": res_dev},
           "pickup_memory": {"post_hoc": True, "error": memory_error,
                             "n_ready": sum(1 for r in rows if r.get("memory_ready")),
                             "n_text": sum(1 for r in rows if r.get("memory_text")),
                             "live_rule": "memory text or pickup pace (nudge.decide), since 2026-11-08",
                             "n_pace_high": sum(1 for r in rows if (r.get("pace") or {}).get("high"))},
           "memory_signal_check": signal_check,
           "where_gap": {"post_hoc": True, "cache_only": True, "n_with_gap": n_gap,
                         "coverage": n_gap / len(rows), "error": gap_error},
           "logistic": lr,
           "tuned_in_sample_on": args.events,
           "rules": {"step_s": STEP, "target_window_s": [LEAD_MIN, LEAD_MAX],
                     "active_within_s": config.ACTIVE_WITHIN, "warmup_s": WARMUP,
                     "pickup_merge_s": config.PICKUP_MERGE_S,
                     "rank_threshold_prereg": config.RANK_THRESHOLD,
                     "rank_threshold_tuned": config.NUDGE_RANK_THRESHOLD,
                     "sticky_distractions": sorted(config.STICKY_DISTRACTIONS)}}
    (RESULTS / "pickups.json").write_text(json.dumps(out, indent=1))
    print(f"wrote {RESULTS / 'pickups.json'}")


if __name__ == "__main__":
    main()
