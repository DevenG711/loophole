"""Pickup pace: how often you pick up your phone, and whether you are doing it
more than usual right now. Personal counts, not a prediction.

pace(records, now) -> dict with
  recent        pickup episodes in the last PACE_WINDOW_S
  usual_gap_s   median time between your pickups, from up to
                PACE_HISTORY_EPISODES episodes that started BEFORE the window
                (so a burst does not set its own bar). Gaps of PACE_MAX_GAP_S
                or more are time away and are skipped. None until there are
                PACE_MIN_GAPS gaps.
  usual_count   pickups your usual gap predicts for one window
  high          recent >= max(PACE_HIGH_MIN_COUNT, PACE_HIGH_FACTOR * usual_count)
  cooldown_s    time between nudges: usual_gap_s kept in
                [NUDGE_COOLDOWN_MIN, NUDGE_COOLDOWN_MAX], or NUDGE_COOLDOWN
                while usual_gap_s is unknown
  text          one line for the menu
  nudge_text    the nudge sentence when `high`, else None
  pending       a high-pace "spell" happened in the last PACE_PENDING_S and
                this session: the nudge can still be shown (PROTOCOL.md
                deviation 16). A spell is named by pending_ts, so the nudge
                rule shows at most one nudge per spell.
  pending_ts    latest pickup episode start in [now - PACE_PENDING_S, now]
                at which the pace was high, or None
  pending_count pickups in the window at pending_ts
  pending_text  the nudge sentence for the spell, or None

Pace is high right after a burst of pickups, which is when you are on your
phone, not at the laptop. "pending" keeps the spell alive so the nudge can
show when you come back.

Episodes are the same as the pickup memory's (precursor._episode_list): test
pings and "Grabbed phone"/hotkey self-reports are not pickups, and records
less than PICKUP_MERGE_S after an episode's first record join it. Reads only
records with ts < now (the latest `session action=start` too). Phone records
never reach when_score from here.
"""
import datetime as dt
import math
import statistics

from .config import (NUDGE_COOLDOWN, NUDGE_COOLDOWN_MAX, NUDGE_COOLDOWN_MIN,
                     PACE_HIGH_FACTOR, PACE_HIGH_MIN_COUNT, PACE_HISTORY_EPISODES,
                     PACE_MAX_GAP_S, PACE_MIN_GAPS, PACE_PENDING_S, PACE_WINDOW_S)
from .precursor import _episode_list   # same episode rule as the pickup memory


def _minutes(seconds: float) -> str:
    m = max(1, round(seconds / 60))
    return f"{m} min"


def _usual_gap(starts: list[float], window_start: float) -> tuple[float | None, int]:
    before = [t for t in starts if t < window_start][-PACE_HISTORY_EPISODES:]
    gaps = [b - a for a, b in zip(before, before[1:]) if b - a < PACE_MAX_GAP_S]
    return (statistics.median(gaps) if len(gaps) >= PACE_MIN_GAPS else None), len(gaps)


def _high_at(starts: list[float], t: float):
    """Was the pace high at time t? Uses only episode starts <= t.
    Returns (high, recent, need, usual_gap_s); need is None while learning."""
    starts = [s for s in starts if s <= t]
    window_start = t - PACE_WINDOW_S
    recent = sum(1 for s in starts if s >= window_start)
    usual_gap, _ = _usual_gap(starts, window_start)
    if usual_gap is None:
        return False, recent, None, None
    need = max(PACE_HIGH_MIN_COUNT, math.ceil(PACE_HIGH_FACTOR * PACE_WINDOW_S / usual_gap))
    return recent >= need, recent, need, usual_gap


def _session_start(records, now: float) -> float | None:
    starts = [r["ts"] for r in records if r.get("kind") == "session"
              and r.get("action") == "start" and r["ts"] < now]
    return max(starts) if starts else None


def _clock(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%H:%M")


def pace(records, now: float) -> dict:
    records = [r for r in records if r["ts"] < now]
    starts = [e["ts"] for e in _episode_list(records, before=now)]
    window_start = now - PACE_WINDOW_S
    high, recent, _need, usual_gap = _high_at(starts, now)
    _, n_gaps = _usual_gap(starts, window_start)

    out = {"recent": recent, "n_gaps": n_gaps, "usual_gap_s": usual_gap,
           "usual_count": None, "high": False, "cooldown_s": NUDGE_COOLDOWN,
           "nudge_text": None, "pending": False, "pending_ts": None,
           "pending_count": None, "pending_text": None}
    window = _minutes(PACE_WINDOW_S)
    count = f"{recent} pickup{'s' if recent != 1 else ''} in the last {window}"
    if usual_gap is None:
        out["text"] = (f"Pace: {count} (learning your usual pace: "
                       f"{n_gaps} of {PACE_MIN_GAPS} gaps)")
    else:
        out["usual_count"] = round(PACE_WINDOW_S / usual_gap, 2)
        out["high"] = high
        out["cooldown_s"] = min(NUDGE_COOLDOWN_MAX, max(NUDGE_COOLDOWN_MIN, usual_gap))
        usual = f"you usually pick up about once every {_minutes(usual_gap)}"
        out["text"] = f"Pace: {count} ({usual})"
        if high:
            out["nudge_text"] = f"{count[0].upper()}{count[1:]}, but {usual}."

    # Pending spell: the latest pickup in the last PACE_PENDING_S (and this
    # session) at which the pace was high. Named by that pickup's time, not
    # by `now`, so one spell stays one spell while it is high.
    session = _session_start(records, now)
    floor = max(now - PACE_PENDING_S, session if session is not None else -math.inf)
    cands = [s for s in starts if floor <= s <= now]
    pending = None
    for s in reversed(cands):
        hit = _high_at(starts, s)
        if hit[0]:
            pending = (s, hit[1], hit[3])
            break
    if pending is None and high and cands:
        # High now, but no single pickup crossed the bar on its own (the
        # usual gap moved): the spell is the latest pickup.
        pending = (cands[-1], recent, usual_gap)
    if pending:
        ts, n, gap = pending
        out.update(pending=True, pending_ts=ts, pending_count=n,
                   pending_text=(f"You picked up your phone {n} times in {window} "
                                 f"(by {_clock(ts)}), but you usually pick up about "
                                 f"once every {_minutes(gap)}."))
    return out
