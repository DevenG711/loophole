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
from .config import (ACTIVE_STEP_IDLE_S, ACTIVE_WITHIN, MIN_ACTIVE_S, RETURN_GAP_S, RETURN_GRACE_S,
                     SETTLED_APPS, SETTLED_DOMAINS, SETTLED_DWELL_S, SETTLED_MAX_IDLE_S)
from .where_score import BROWSER_APPS, IGNORE_APPS

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

# A step (time between two input samples) longer than this is a tracker gap
# (restart, sleep, a stuck tick). Its counts can hold many minutes of input,
# so it adds neither counts nor time to any rate (typing, scrolling, bursts).
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
    """Steps ending in the window, minus tracker gaps (longer than MAX_STEP_S)."""
    return [s for s in steps if s[1] > t - window and s[1] - s[0] <= MAX_STEP_S]


def _active_rate_per_min(inputs, t, window):
    """Keys per minute over ACTIVE time only, plus the active seconds.

    A step counts as active if the sample at its end saw input (of any kind)
    in the last ACTIVE_STEP_IDLE_S seconds. Idle minutes are left out, so
    coming back from a pause does not look like "typing slowed down".
    A step longer than MAX_STEP_S (a tracker gap) adds neither keys nor time:
    its keys were typed over minutes we cannot see.
    """
    keys, active_s = 0, 0.0
    for prev, cur in zip(inputs, inputs[1:]):
        if cur["ts"] <= t - window:
            continue
        if cur["ts"] - prev["ts"] > MAX_STEP_S:
            continue
        if cur.get("idle", {}).get("any", 0) >= ACTIVE_STEP_IDLE_S:
            continue
        a = prev.get("counts", {}).get("key", 0)
        b = cur.get("counts", {}).get("key", 0)
        keys += b - a if b >= a else b
        active_s += cur["ts"] - max(prev["ts"], t - window)
    rate = keys / (active_s / 60) if active_s > 0 else 0.0
    return rate, active_s


def _place(rec):
    """Where you are, ignoring tab-title changes: (app, domain)."""
    return ((rec.get("app") or "").lower(), (rec.get("domain") or "").lower())


def _visits(apps):
    """App records with title-only changes merged: list of (start_ts, place).

    Noise apps (IGNORE_APPS: lock screen, Finder, notifications, ...) are
    skipped. A browser record with no domain (the tab read failed, or a
    private tab) is "no change" while you are already in that browser; if you
    just switched to it, it starts a visit whose domain is filled in by the
    next record that has one, without counting a second switch.
    """
    visits = []
    for r in apps:
        app, domain = place = _place(r)
        if app in IGNORE_APPS:
            continue
        last = visits[-1][1] if visits else None
        if app in BROWSER_APPS and last and last[0] == app:
            if not domain:
                continue                              # failed read: still there
            if not last[1]:
                visits[-1] = (visits[-1][0], place)   # domain finally known
                continue
        if last != place:
            visits.append((r["ts"], place))
    return visits


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
        # Just back from a pause? Then key_drop and dwell still count the
        # idle time, which looks like a stall but is not one. Uses only input
        # samples before t (never score records, which can be wrong after a
        # restart).
        just_returned = any(r.get("idle", {}).get("any", 0) >= RETURN_GAP_S
                            for r in inputs if r["ts"] > t - RETURN_GRACE_S)
        active_recent = idle_s <= ACTIVE_WITHIN and not just_returned
    else:
        just_returned = False
        idle_s = 0.0          # no data yet: treat as unknown, not idle
        no_work_s = 0.0
        active_recent = False

    # Typing rate now vs your own last 10 minutes, over active time only.
    key_rate_now, active_now_s = _active_rate_per_min(inputs, t, NOW_WINDOW)
    key_rate_base, active_base_s = _active_rate_per_min(inputs, t, BASE_WINDOW)
    key_drop = 0.0
    if key_rate_base > 0 and active_now_s >= MIN_ACTIVE_S:
        key_drop = min(1.0, max(0.0, 1 - key_rate_now / key_rate_base))

    # Seconds in the last 2 min with scrolling but no typing.
    scroll_no_type = 0.0
    for t0, t1, d in _in_window(steps, t, NOW_WINDOW):
        if d["scroll"] > 0 and d["key"] == 0:
            scroll_no_type += t1 - max(t0, t - NOW_WINDOW)

    # Time on the current app/site (a tab title changing, like "(2) Inbox",
    # is not a move), and time there with no typing/clicking.
    visits = _visits(apps)
    dwell_s = t - visits[-1][0] if visits else no_work_s
    dwell_no_input_s = min(dwell_s, no_work_s)

    # App/site switches per minute over the last 5 min (or since the session
    # started, at least 1 min). Count and divide over the same span.
    switch_span = max(60.0, min(RECENT_WINDOW, t - session_start))
    switches = sum(1 for ts, _ in visits if ts > t - switch_span)
    switches_per_min = switches / (switch_span / 60)

    # Settled: staying on one thing and still touching the computer (reading
    # an article, docs or an agent's output), or on a call. See contributions().
    app_name, domain = visits[-1][1] if visits else ("", "")
    on_call = app_name in SETTLED_APPS or any(
        domain == d or domain.endswith("." + d) for d in SETTLED_DOMAINS)
    settled = bool(visits) and dwell_s >= SETTLED_DWELL_S and (
        on_call or (inputs and idle_s < SETTLED_MAX_IDLE_S))

    # Minutes since the last break (idle >= 4 min, a tracker gap >= 4 min
    # with no input across it, or session start). A gap where keys, clicks or
    # scrolls went up was not a break: you worked while the tracker was quiet.
    # Lid closed / asleep: counters do not move, so that still counts.
    last_break = session_start
    for (t0, t1, d), cur in zip(steps, inputs[1:]):
        if t1 - t0 < BREAK_GAP_S:
            continue
        idle_after = cur.get("idle", {}).get("any", 0)
        if idle_after >= BREAK_GAP_S or d["key"] + d["click"] + d["scroll"] == 0:
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
        "dwell_s": round(dwell_s, 1),
        "settled": settled,
        "switches_per_min": round(switches_per_min, 2),
        "minutes_since_break": round(minutes_since_break, 1),
        "active_recent": active_recent,
        "just_returned": just_returned,
    }


# ---------------------------------------------------------------------------
# Stall score (cold start: hand-set weights, no training needed)
#
# Each feature is first squashed to 0..1 with a simple ramp, then multiplied
# by its weight. The score is the sum. With all signals maxed out the score is
# about 3.0; config.STALL_THRESHOLD (1.0) means "two or three signals are
# clearly on".
#
# While "settled" (45 s+ on one app/site with input in the last 90 s, or on a
# call), key_drop, scroll_no_type, burst_pause_count and dwell_no_input_s are
# set to 0: they describe reading too. Typing rates use active time only.
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

# Signals that also describe reading (or a call), so they do not count while
# you are settled on one app/site. Switching, long sessions and going quiet
# still count.
EXCUSED_WHEN_SETTLED = {"key_drop", "scroll_no_type", "burst_pause_count", "dwell_no_input_s"}

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
        if f.get("settled") and name in EXCUSED_WHEN_SETTLED:
            value = 0  # reading looks like this; it is not a stall
        out[name] = round(weight * _ramp(value, *RAMPS[name]), 3)
    return out


def stall_score(f: dict, weights: dict | None = None) -> float:
    """The "when" score: sum of contributions. See contributions() for the parts."""
    return round(sum(contributions(f, weights).values()), 3)


def why(f: dict, weights: dict | None = None, top: int = 3) -> list[str]:
    """Plain-English reasons for the score, biggest contribution first."""
    parts = sorted(contributions(f, weights).items(), key=lambda kv: -kv[1])
    return [REASONS[name] for name, value in parts[:top] if value > 0]
