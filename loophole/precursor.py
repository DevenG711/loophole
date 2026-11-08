"""Pickup memory: what the few minutes before each real phone pickup looked
like on the Mac, and whether right now looks like one of those moments.

This is memory, not a trained model. Each real pickup adds one snapshot (taken
PRECURSOR_LEAD_S before it); "learning" is that list growing. There are no
weights and nothing is tuned on the labels: the distance cutoff and the
minimum lift are fixed in config.py.

Rules (see CLAUDE.md):
  - Only records with ts < t are read.
  - Past phone records are read here (time since the last pickup is useful to
    show), but they are NOT used for similarity: pickups cluster, so "you
    picked up 4 min ago" would match everything right after a pickup. They
    also never reach when_score, which stays phone-free, or the BehaviorGPT
    history behind where_gap (built from laptop `app` records only: any phone
    app in the history goes to #1, which would smuggle "you just picked up"
    back in).
  - Only Shortcut pickups count as episodes. "Grabbed phone" button presses
    (source "button") are left out: they usually answer a nudge, so counting
    them would let the memory confirm its own guesses.
  - Before saying anything we check the base rate: of past moments that
    looked like this, how many were followed by a pickup, compared with all
    past moments. Being in Cursor precedes every pickup and proves nothing.

Public functions:
  window_features(records, t)  -> dict of SNAPSHOT_FEATURES at t
  episodes(records, before)    -> real pickup episodes before `before`, each
                                  with the snapshot from just before it
  match(records, t)            -> does now look like the moments before past
                                  pickups? (see match() for the keys)
"""
import bisect
import datetime
import math
from collections import Counter

from . import when_score, where_score
from .config import (ACTIVE_STEP_IDLE_S, MIN_ACTIVE_S, PICKUP_MERGE_S, PRECURSOR_GRID_S,
                     PRECURSOR_LEAD_S, PRECURSOR_LOOKBACK_S, PRECURSOR_MATCH_DIST,
                     PRECURSOR_MIN_EPISODES, PRECURSOR_MIN_LIFT, PRECURSOR_TARGET_S,
                     SETTLED_MAX_IDLE_S)

# Everything recorded about the window before a pickup (shown to the user and
# kept for later analysis).
SNAPSHOT_FEATURES = [
    "idle_s", "dwell_s", "settled", "key_rate_5m", "key_trend", "scroll_rate_5m",
    "scroll_trend", "mouse_rate_5m", "switches_5m", "distinct_5m", "newtab_5m",
    "micro_gaps_5m", "minutes_since_break", "stall", "where_rank",
    "min_since_pickup", "pickups_1h", "local_hour", "where_gap", "pull_app", "pull_z",
]

# The few features used to decide "this looks like before a pickup". Chosen
# before looking at the data: where you are (how long on one window, how much
# hopping), how your speed is changing, the "when" stall score, and
# BehaviorGPT's "where": how strongly its ranking of your next move leans to
# distraction (where_gap, from a laptop-only history, never phone records).
# With a handful of pickups, more features would make
# the nearest neighbours close to random.
MATCH_FEATURES = ["dwell_s", "key_trend", "scroll_trend", "switches_5m", "stall", "where_gap"]
MIN_SHARED = 4   # compare two moments on at least this many features (where_gap can be missing)

# Smallest spread used when scaling each match feature, so one quiet session
# doesn't make tiny differences look huge. dwell_s is compared as log(1+s).
MIN_SPREAD = {"dwell_s": 0.5, "key_trend": 10.0, "scroll_trend": 10.0, "switches_5m": 1.0,
              "stall": 0.2, "where_gap": 0.15}

MAX_STEP_S = 10          # cap per input step (tracker gaps), as in when_score
MICRO_GAP = (3.0, 10.0)  # a pause this long (s) while working = hesitation
HALF = PRECURSOR_LOOKBACK_S / 2

_vec_cache: dict = {}    # match vectors of past moments; the log only grows


# ---------------------------------------------------------------------------
# Pickup episodes
# ---------------------------------------------------------------------------

def _phone_name(rec: dict) -> str:
    app = (rec.get("app") or "").strip()
    if app.lower().startswith("app="):   # records from before phone.py parsed forms
        app = app[4:].strip()
    return app[:1].upper() + app[1:] if app else "phone"


# Phone sources that are not episodes: test pings, and button presses (a
# self-report, usually right after a nudge: see the module docstring).
NOT_EPISODE_SOURCES = {"test", "button"}


def _episode_list(records, before: float) -> list[dict]:
    """Real pickups before `before`, merged when closer than PICKUP_MERGE_S.
    Test pings and button presses are not episodes. Time = the first record."""
    phones = sorted((r for r in records if r.get("kind") == "phone"
                     and r.get("source") not in NOT_EPISODE_SOURCES and r["ts"] < before),
                    key=lambda r: r["ts"])
    eps, last_ts = [], None
    for r in phones:
        if last_ts is not None and r["ts"] - last_ts < PICKUP_MERGE_S:
            last_ts = r["ts"]          # chained: same episode
            continue
        eps.append({"ts": r["ts"], "app": _phone_name(r)})
        last_ts = r["ts"]
    return eps


# ---------------------------------------------------------------------------
# A prepared view of the log, so many windows can be measured quickly
# ---------------------------------------------------------------------------

class _Log:
    def __init__(self, records, t: float):
        recs = [r for r in records if r["ts"] < t]
        self.inputs = sorted((r for r in recs if r.get("kind") == "input"), key=lambda r: r["ts"])
        self.input_ts = [r["ts"] for r in self.inputs]
        apps = sorted((r for r in recs if r.get("kind") == "app"), key=lambda r: r["ts"])
        self.apps = apps
        self.app_ts = [r["ts"] for r in apps]
        self.visits = when_score._visits(apps)          # title changes merged
        self.visit_ts = [v[0] for v in self.visits]
        scores = sorted((r for r in recs if r.get("kind") == "score"), key=lambda r: r["ts"])
        self.scores = scores
        self.score_ts = [r["ts"] for r in scores]
        self.episodes = _episode_list(recs, t)
        self.records = recs
        # BehaviorGPT histories: laptop apps only (phone records would leak
        # "you just picked up" into where_gap; see the module docstring).
        self.moves = where_score.app_records(recs)
        self.session_recs = [r for r in recs if r.get("kind") == "session"]
        self._where_cache = None

    def where_cache(self) -> dict:
        if self._where_cache is None:
            try:
                self._where_cache = where_score.load_cache()
            except Exception:
                self._where_cache = {}
        return self._where_cache

    def stall_at(self, t: float) -> float:
        """The "when" stall score at t (from the last hour of records + sessions)."""
        lo = bisect.bisect_left(self.input_ts, t - 3600)
        recent = (self.inputs[lo:bisect.bisect_left(self.input_ts, t)]
                  + self.apps[bisect.bisect_left(self.app_ts, t - 3600):bisect.bisect_left(self.app_ts, t)]
                  + self.session_recs)
        return when_score.stall_score(when_score.features(recent, t))

    def where_at(self, t: float) -> dict | None:
        """BehaviorGPT's gap/pull for the history at t, from the cache only
        (never an API call: past moments are replayed, not re-asked)."""
        try:
            specs = where_score._specs(self.moves, t)
            return where_score.signal_from_specs(specs, self.where_cache()) if specs else None
        except Exception:
            return None

    def inputs_between(self, start: float, end: float) -> list[dict]:
        """Input samples with start - one step < ts < end (the step before
        `start` is included so the first step in the window has a start)."""
        i = max(0, bisect.bisect_right(self.input_ts, start) - 1)
        j = bisect.bisect_left(self.input_ts, end)
        return self.inputs[i:j]

    def rate(self, start: float, end: float, key: str) -> tuple[float, float]:
        """Count per minute of `key` over ACTIVE time in (start, end), and the
        active seconds. Steps whose end sample saw no input for
        ACTIVE_STEP_IDLE_S are left out (same rule as when_score)."""
        ins = self.inputs_between(start, end)
        total, active = 0, 0.0
        for prev, cur in zip(ins, ins[1:]):
            if cur["ts"] <= start or cur.get("idle", {}).get("any", 0) >= ACTIVE_STEP_IDLE_S:
                continue
            a = prev.get("counts", {}).get(key, 0)
            b = cur.get("counts", {}).get(key, 0)
            total += b - a if b >= a else b
            active += min(cur["ts"] - max(prev["ts"], start), MAX_STEP_S)
        return (total / (active / 60) if active > 0 else 0.0), active

    def trend(self, t: float, key: str) -> float:
        """Rate in the last half of the window minus the first half (per min).
        0 when either half has too little active time to say."""
        late, late_s = self.rate(t - HALF, t, key)
        early, early_s = self.rate(t - 2 * HALF, t - HALF, key)
        if late_s < MIN_ACTIVE_S or early_s < MIN_ACTIVE_S:
            return 0.0
        return late - early

    def idle_at(self, t: float) -> float | None:
        i = bisect.bisect_left(self.input_ts, t) - 1
        if i < 0:
            return None
        last = self.inputs[i]
        return last.get("idle", {}).get("any", 0) + (t - last["ts"])

    def dwell_at(self, t: float) -> float:
        i = bisect.bisect_left(self.visit_ts, t) - 1
        return t - self.visit_ts[i] if i >= 0 else 0.0

    def switches(self, t: float) -> int:
        return bisect.bisect_left(self.visit_ts, t) - bisect.bisect_right(self.visit_ts, t - PRECURSOR_LOOKBACK_S)

    def match_vector(self, t: float) -> dict:
        """The MATCH_FEATURES at t. Cheap; cached for past moments."""
        # Key on the data itself (not just t), so two logs never share entries.
        i0 = max(0, bisect.bisect_left(self.input_ts, t - PRECURSOR_LOOKBACK_S) - 1)
        i1 = bisect.bisect_left(self.input_ts, t)
        edge = tuple((r["ts"], tuple(sorted(r.get("counts", {}).items())))
                     for r in (self.inputs[i0:i1][:1] + self.inputs[i0:i1][-1:]))
        v = bisect.bisect_left(self.visit_ts, t) - 1
        last_visit = self.visits[v] if v >= 0 else None
        key = (round(t, 3), i1 - i0, edge, last_visit, self.switches(t))
        hit = _vec_cache.get(key)
        if hit is not None:
            return hit
        where = self.where_at(t)
        vec = {
            "dwell_s": self.dwell_at(t),
            "key_trend": self.trend(t, "key"),
            "scroll_trend": self.trend(t, "scroll"),
            "switches_5m": float(self.switches(t)),
            "stall": self.stall_at(t),
            "where_gap": where.get("gap") if where else None,
        }
        if vec["where_gap"] is None:
            return vec   # don't cache: the BehaviorGPT entry may arrive later
        if len(_vec_cache) > 50_000:
            _vec_cache.clear()
        _vec_cache[key] = vec
        return vec


# ---------------------------------------------------------------------------
# Features of one window
# ---------------------------------------------------------------------------

def _features(log: _Log, t: float) -> dict:
    start = t - PRECURSOR_LOOKBACK_S
    f = when_score.features(log.records, t)          # phone-free by design
    vec = log.match_vector(t)

    # distinct places: the one you were in at the window start, plus every new one
    i0 = max(0, bisect.bisect_right(log.visit_ts, start) - 1)
    i1 = bisect.bisect_left(log.visit_ts, t)
    distinct = len({v[1] for v in log.visits[i0:i1]})

    a0, a1 = bisect.bisect_right(log.app_ts, start), bisect.bisect_left(log.app_ts, t)
    newtab = sum(1 for r in log.apps[a0:a1] if (r.get("domain") or "").lower() == "newtab")

    # micro-gaps: pauses of 3-10 s that ended (you came back within 10 s)
    gaps, peak = 0, 0.0
    for r in log.inputs_between(start, t):
        idle = r.get("idle", {}).get("any", 0)
        if idle >= MICRO_GAP[0]:
            peak = max(peak, idle)
        elif peak:
            gaps += peak < MICRO_GAP[1]
            peak = 0.0

    s = bisect.bisect_left(log.score_ts, t) - 1
    where_rank = log.scores[s].get("where_rank") if s >= 0 else None

    past = [e["ts"] for e in log.episodes if e["ts"] < t]
    return {
        "idle_s": f["idle_s"],
        "dwell_s": round(vec["dwell_s"], 1),
        "settled": f.get("settled", False),
        "key_rate_5m": round(log.rate(start, t, "key")[0], 1),
        "key_trend": round(vec["key_trend"], 1),
        "scroll_rate_5m": round(log.rate(start, t, "scroll")[0], 1),
        "scroll_trend": round(vec["scroll_trend"], 1),
        "mouse_rate_5m": round(log.rate(start, t, "mouse")[0], 1),
        "switches_5m": int(vec["switches_5m"]),
        "distinct_5m": distinct,
        "newtab_5m": newtab,
        "micro_gaps_5m": gaps,
        "minutes_since_break": f["minutes_since_break"],
        "stall": when_score.stall_score(f),
        "where_rank": where_rank,
        "min_since_pickup": round((t - past[-1]) / 60, 1) if past else None,
        "pickups_1h": sum(1 for p in past if p > t - 3600),
        "local_hour": datetime.datetime.fromtimestamp(t).hour,
        "where_gap": None if vec["where_gap"] is None else round(vec["where_gap"], 3),
        "pull_app": (log.where_at(t) or {}).get("pull_name"),
        "pull_z": (log.where_at(t) or {}).get("pull_z"),
    }


def window_features(records, t: float) -> dict:
    """SNAPSHOT_FEATURES at time t, from records with ts < t only."""
    return _features(_Log(records, t), t)


def episodes(records, before: float = float("inf")) -> list[dict]:
    """Real pickup episodes before `before`, each with its snapshot
    (window_features at PRECURSOR_LEAD_S before the pickup)."""
    log = _Log(records, before)
    out = []
    for e in log.episodes:
        snap_t = e["ts"] - PRECURSOR_LEAD_S
        out.append({**e, "features": _features(_Log(log.records, snap_t), snap_t)})
    return out


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _scaled(vec: dict) -> dict:
    out = dict(vec)
    out["dwell_s"] = math.log1p(max(0.0, vec["dwell_s"]))
    return out


def _spreads(vectors: list[dict]) -> dict:
    """Robust spread (IQR / 1.349) of each match feature over past moments,
    never below MIN_SPREAD. Uses no labels."""
    out = {}
    for k in MATCH_FEATURES:
        vals = sorted(v[k] for v in vectors if v.get(k) is not None)
        if len(vals) >= 8:
            q1, q3 = vals[len(vals) // 4], vals[(3 * len(vals)) // 4]
            out[k] = max((q3 - q1) / 1.349, MIN_SPREAD[k])
        else:
            out[k] = MIN_SPREAD[k]
    return out


def _distance(a: dict, b: dict, spread: dict) -> float:
    """RMS distance in robust z units over the features both moments have.
    Infinite if they share fewer than MIN_SHARED (can't say they're alike)."""
    keys = [k for k in MATCH_FEATURES if a.get(k) is not None and b.get(k) is not None]
    if len(keys) < MIN_SHARED:
        return math.inf
    return math.sqrt(sum(((a[k] - b[k]) / spread[k]) ** 2 for k in keys) / len(keys))


def _describe(f: dict) -> str:
    """Plain words for the current window, e.g. "On one window 3 min, typing up 20/min"."""
    parts = []
    dwell = f["dwell_s"]
    parts.append(f"on one window {dwell / 60:.0f} min" if dwell >= 90 else f"on one window {dwell:.0f} s")
    kt = f["key_trend"]
    if abs(kt) >= 10:
        parts.append(f"typing {'up' if kt > 0 else 'down'} {abs(kt):.0f}/min")
    st = f["scroll_trend"]
    if abs(st) >= 10:
        parts.append(f"scrolling {'up' if st > 0 else 'down'} {abs(st):.0f}/min")
    parts.append(f"{int(f['switches_5m'])} switches in 5 min")
    return ", ".join(parts)


def _clock(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M")


def match(records, t: float) -> dict:
    """Does the window before t look like the windows before past pickups?

    Keys:
      n_episodes        real pickups before t (the memory size)
      ready             n_episodes >= PRECURSOR_MIN_EPISODES
      matches           up to 2 nearest past snapshots within the cutoff:
                        [{"ts", "app", "distance"}]
      matched_moments   past moments (grid every PRECURSOR_GRID_S, outcome
                        already known before t) that looked like a pickup
                        snapshot, not counting the pickup right after them
      matched_followed  how many of those were followed by a pickup in
                        PRECURSOR_TARGET_S
      base_rate         share of all those past moments followed by a pickup
      lift              (matched_followed / matched_moments) / base_rate
      likely_app        most common app among the matches
      pull_app, pull_z  BehaviorGPT's "app pulling at you" now (None if weak)
      where_gap         BehaviorGPT's lean to distraction now (None if unknown)
      text              a sentence to show, or None (needs ready, a match and
                        lift >= PRECURSOR_MIN_LIFT)
      score             1 / (1 + nearest distance); 0 with no pickups yet.
                        Higher = more like past pickups (for the eval).
    """
    out = {"n_episodes": 0, "ready": False, "matches": [], "matched_moments": 0,
           "matched_followed": 0, "base_rate": None, "lift": None,
           "likely_app": None, "text": None, "match_text": None, "score": 0.0}
    log = _Log(records, t)
    eps = log.episodes
    out["n_episodes"] = len(eps)
    out["ready"] = len(eps) >= PRECURSOR_MIN_EPISODES
    if not eps or not log.inputs:
        return out

    # Past moments where you were at the computer (for scaling and base rate).
    lo, hi = PRECURSOR_TARGET_S
    first = log.input_ts[0] + PRECURSOR_LOOKBACK_S
    grid = []
    m = first
    while m < t:
        idle = log.idle_at(m)
        if idle is not None and idle < SETTLED_MAX_IDLE_S:
            grid.append(m)
        m += PRECURSOR_GRID_S
    grid_vecs = {m: _scaled(log.match_vector(m)) for m in grid}
    spread = _spreads(list(grid_vecs.values()))

    snaps = [(e, _scaled(log.match_vector(e["ts"] - PRECURSOR_LEAD_S))) for e in eps]
    now = _scaled(log.match_vector(t))

    # Nearest past pickups to right now.
    dists = sorted(((_distance(now, v, spread), e) for e, v in snaps), key=lambda x: x[0])
    out["score"] = 0.0 if math.isinf(dists[0][0]) else round(1 / (1 + dists[0][0]), 4)
    close = [(d, e) for d, e in dists if d <= PRECURSOR_MATCH_DIST][:2]
    out["matches"] = [{"ts": e["ts"], "app": e["app"], "distance": round(d, 3)} for d, e in close]

    # Base rate: past moments whose outcome is already known before t.
    ep_ts = [e["ts"] for e in eps]
    known, followed_all, matched, matched_followed = 0, 0, 0, 0
    for m in grid:
        if m + hi >= t:
            break
        followed = any(m + lo < p <= m + hi for p in ep_ts)
        known += 1
        followed_all += followed
        # Compare with every snapshot except ones from right around m (that
        # would be m matching itself).
        near = [v for e, v in snaps
                if abs((e["ts"] - PRECURSOR_LEAD_S) - m) >= PRECURSOR_LOOKBACK_S]
        if near and min(_distance(grid_vecs[m], v, spread) for v in near) <= PRECURSOR_MATCH_DIST:
            matched += 1
            matched_followed += followed
    out["matched_moments"], out["matched_followed"] = matched, matched_followed
    if known and followed_all:
        base = followed_all / known
        out["base_rate"] = round(base, 3)
        if matched >= 3:
            out["lift"] = round((matched_followed / matched) / base, 2)

    if close:
        out["likely_app"] = Counter(e["app"] for _, e in close).most_common(1)[0][0]
    where_now = log.where_at(t) or {}
    out["pull_app"], out["pull_z"] = where_now.get("pull_name"), where_now.get("pull_z")
    out["where_gap"] = where_now.get("gap")
    if close:   # what a match looks like, without the base-rate claim (demo mode)
        when = " and ".join(_clock(e["ts"]) for _, e in close)
        out["match_text"] = f"This looks like {when}, right before you opened {out['likely_app']}."
    if out["ready"] and close and out["lift"] is not None and out["lift"] >= PRECURSOR_MIN_LIFT:
        when = " and ".join(_clock(e["ts"]) for _, e in close)
        cur = log.match_vector(t)
        # Plain words for the user. The numbers (lift, matched moments, z)
        # stay in this dict and the score record, for the dashboard.
        pull = (f" BehaviorGPT also ranks {out['pull_app']} high right now."
                if out["pull_app"] else "")
        out["text"] = (f"This looks like {when}, right before you opened {out['likely_app']}: "
                       f"{_describe(cur)}.{pull} (Pattern learned from {len(eps)} pickups.)")
    return out
