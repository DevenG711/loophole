"""The "when" score: local stall signals that say you are about to break.

Everything here is computed on this Mac from `input`, `app` and `session`
records. We NEVER read `phone` records: a phone pickup is the label we try to
predict, so reading it would be cheating.

Public functions:
  features(records, t)  -> dict of raw signals, using only records with ts < t
  stall_score(f)        -> float (a weighted sum; higher = more stalled)
  contributions(f)      -> dict of how much each signal added (for the UI "why")
  why(f)                -> short plain-English reasons, biggest first
"""
from .config import ACTIVE_WITHIN

# Kinds we are allowed to read. "phone" is deliberately missing.
ALLOWED_KINDS = {"input", "app", "session"}
COUNT_KEYS = ("key", "click", "scroll", "mouse")

# Windows (seconds)
NOW_WINDOW = 2 * 60      # "typing rate now"
BASE_WINDOW = 10 * 60    # "your own normal typing rate"
RECENT_WINDOW = 5 * 60   # burst/pause and app switches
BREAK_GAP_S = 4 * 60     # an idle gap this long counts as a break

# Burst-then-pause: a run of typing with at least BURST_KEYS keys, followed by
# at least PAUSE_S seconds with no keys.
BURST_KEYS = 40
PAUSE_S = 45

# Scroll-without-typing steps longer than this are capped (tracker gaps).
MAX_STEP_S = 10


def _keep(records, t):
    """Only records we may read: allowed kinds, strictly before t."""
    return [r for r in records if r.get("kind") in ALLOWED_KINDS and r["ts"] < t]


def _steps(inputs):
    """Turn running totals into per-step differences.

    Returns a list of (t0, t1, delta) where delta = {"key": n, ...}.
    If a counter goes down (tracker restart or reboot), we assume it restarted
    from zero, so the new value itself is the difference.
    """
    steps = []
    for prev, cur in zip(inputs, inputs[1:]):
        delta = {}
        for k in COUNT_KEYS:
            a = prev.get("counts", {}).get(k, 0)
            b = cur.get("counts", {}).get(k, 0)
            delta[k] = b - a if b >= a else b
        steps.append((prev["ts"], cur["ts"], delta))
    return steps


def _in_window(steps, t, window):
    return [s for s in steps if s[1] > t - window]


def _rate_per_min(steps, t, window, first_ts):
    """Keys per minute over the window (or over the data we have, if less)."""
    span = min(window, t - first_ts)
    if span <= 0:
        return 0.0
    keys = sum(d["key"] for _, _, d in _in_window(steps, t, window))
    return keys / (span / 60)


def _burst_pause_count(steps, t):
    """How many typing bursts were followed by a long pause, in the last 5 min."""
    runs = []  # each run is [start, end, keys]
    for t0, t1, d in _in_window(steps, t, RECENT_WINDOW):
        if d["key"] <= 0:
            continue
        if runs and t0 - runs[-1][1] < PAUSE_S:
            runs[-1][1] = t1
            runs[-1][2] += d["key"]
        else:
            runs.append([t0, t1, d["key"]])
    count = 0
    for i, (start, end, keys) in enumerate(runs):
        next_start = runs[i + 1][0] if i + 1 < len(runs) else t
        if keys >= BURST_KEYS and next_start - end >= PAUSE_S:
            count += 1
    return count


def features(records: list[dict], t: float) -> dict:
    """All "when" signals at time t. Uses only records with ts < t."""
    recs = _keep(records, t)
    inputs = sorted((r for r in recs if r["kind"] == "input"), key=lambda r: r["ts"])
    apps = sorted((r for r in recs if r["kind"] == "app"), key=lambda r: r["ts"])
    starts = [r["ts"] for r in recs if r["kind"] == "session" and r.get("action") == "start"]

    first_ts = min((r["ts"] for r in recs), default=t)
    session_start = max(starts) if starts else first_ts
    steps = _steps(inputs)

    # Idle: what the last sample said, plus the time since that sample.
    if inputs:
        last = inputs[-1]
        since = t - last["ts"]
        idle = last.get("idle", {})
        idle_s = idle.get("any", 0) + since
        # "No input" for dwell = no typing and no clicking (scrolling or
        # moving the mouse alone does not count as working).
        no_work_s = min(idle.get("key", 0), idle.get("click", 0)) + since
        active_recent = idle_s <= ACTIVE_WITHIN
    else:
        idle_s = 0.0          # no data yet: treat as unknown, not idle
        no_work_s = 0.0
        active_recent = False

    # Typing rate now vs your own last 10 minutes.
    first_input = inputs[0]["ts"] if inputs else t
    key_rate_now = _rate_per_min(steps, t, NOW_WINDOW, first_input)
    key_rate_base = _rate_per_min(steps, t, BASE_WINDOW, first_input)
    key_drop = 0.0
    if key_rate_base > 0:
        key_drop = min(1.0, max(0.0, 1 - key_rate_now / key_rate_base))

    # Seconds in the last 2 min with scrolling but no typing.
    scroll_no_type = 0.0
    for t0, t1, d in _in_window(steps, t, NOW_WINDOW):
        if d["scroll"] > 0 and d["key"] == 0:
            scroll_no_type += min(t1 - max(t0, t - NOW_WINDOW), MAX_STEP_S)

    # Time on the current window with no typing/clicking.
    dwell = t - apps[-1]["ts"] if apps else no_work_s
    dwell_no_input_s = min(dwell, no_work_s)

    # App/tab switches per minute over the last 5 min.
    switch_span = max(60.0, min(RECENT_WINDOW, t - session_start))
    switches = sum(1 for r in apps if r["ts"] > t - RECENT_WINDOW)
    switches_per_min = switches / (switch_span / 60)

    # Minutes since the last break (idle >= 4 min, a tracker gap >= 4 min,
    # or session start).
    last_break = session_start
    for t0, t1, _ in steps:
        if t1 - t0 >= BREAK_GAP_S:
            last_break = max(last_break, t1)
    for r in inputs:
        if r.get("idle", {}).get("any", 0) >= BREAK_GAP_S:
            last_break = max(last_break, r["ts"])
    if idle_s >= BREAK_GAP_S:
        last_break = t   # on a break right now
    minutes_since_break = max(0.0, (t - last_break) / 60)

    return {
        "idle_s": round(idle_s, 1),
        "key_rate_now": round(key_rate_now, 1),
        "key_rate_base": round(key_rate_base, 1),
        "key_drop": round(key_drop, 3),
        "scroll_no_type": round(scroll_no_type, 1),
        "burst_pause_count": _burst_pause_count(steps, t),
        "dwell_no_input_s": round(dwell_no_input_s, 1),
        "switches_per_min": round(switches_per_min, 2),
        "minutes_since_break": round(minutes_since_break, 1),
        "active_recent": active_recent,
    }


# ---------------------------------------------------------------------------
# Stall score (cold start: hand-set weights, no training needed)
#
# Each feature is first squashed to 0..1 with a simple ramp, then multiplied
# by its weight. The score is the sum. With all signals maxed out the score is
# about 3.0; config.STALL_THRESHOLD (1.0) means "two or three signals are
# clearly on".
#
# feature              ramp (0 at -> 1 at)          weight
# key_drop             0 -> 1 (already a fraction)   0.6
# scroll_no_type       0 -> 90 s                     0.4
# burst_pause_count    0 -> 3                        0.4
# dwell_no_input_s     30 -> 180 s                   0.4
# switches_per_min     1 -> 4 per min                0.5
# minutes_since_break  25 -> 50 min                  0.5
# idle_s               15 -> 90 s (0 if >= 4 min,    0.2
#                      that is a break, not a stall)
# ---------------------------------------------------------------------------
DEFAULT_WEIGHTS = {
    "key_drop": 0.6,
    "scroll_no_type": 0.4,
    "burst_pause_count": 0.4,
    "dwell_no_input_s": 0.4,
    "switches_per_min": 0.5,
    "minutes_since_break": 0.5,
    "idle_s": 0.2,
}

# (value where the ramp starts, value where it reaches 1)
RAMPS = {
    "key_drop": (0.0, 1.0),
    "scroll_no_type": (0.0, 90.0),
    "burst_pause_count": (0.0, 3.0),
    "dwell_no_input_s": (30.0, 180.0),
    "switches_per_min": (1.0, 4.0),
    "minutes_since_break": (25.0, 50.0),
    "idle_s": (15.0, 90.0),
}

REASONS = {
    "key_drop": "your typing slowed down a lot",
    "scroll_no_type": "you are scrolling without typing",
    "burst_pause_count": "you type in bursts, then stop",
    "dwell_no_input_s": "you have sat on one window without typing",
    "switches_per_min": "you are switching apps a lot",
    "minutes_since_break": "it has been a long time since your last break",
    "idle_s": "you have gone quiet",
}


def _ramp(value: float, lo: float, hi: float) -> float:
    return min(1.0, max(0.0, (value - lo) / (hi - lo)))


def contributions(f: dict, weights: dict | None = None) -> dict:
    """How much each feature adds to the stall score."""
    w = weights or DEFAULT_WEIGHTS
    out = {}
    for name, weight in w.items():
        value = f.get(name, 0) or 0
        if name == "idle_s" and value >= BREAK_GAP_S:
            value = 0  # a real break is good, not a stall
        out[name] = round(weight * _ramp(value, *RAMPS[name]), 3)
    return out


def stall_score(f: dict, weights: dict | None = None) -> float:
    """The "when" score: sum of contributions. See contributions() for the parts."""
    return round(sum(contributions(f, weights).values()), 3)


def why(f: dict, weights: dict | None = None, top: int = 3) -> list[str]:
    """Plain-English reasons for the score, biggest contribution first."""
    parts = sorted(contributions(f, weights).items(), key=lambda kv: -kv[1])
    return [REASONS[name] for name, value in parts[:top] if value > 0]
