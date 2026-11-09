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

Episodes are the same as the pickup memory's (precursor._episode_list): test
pings and "Grabbed phone"/hotkey self-reports are not pickups, and records
less than PICKUP_MERGE_S after an episode's first record join it. Reads only
records with ts < now. Phone records never reach when_score from here.
"""
import math
import statistics

from .config import (NUDGE_COOLDOWN, NUDGE_COOLDOWN_MAX, NUDGE_COOLDOWN_MIN,
                     PACE_HIGH_FACTOR, PACE_HIGH_MIN_COUNT, PACE_HISTORY_EPISODES,
                     PACE_MAX_GAP_S, PACE_MIN_GAPS, PACE_WINDOW_S)
from .precursor import _episode_list   # same episode rule as the pickup memory


def _minutes(seconds: float) -> str:
    m = max(1, round(seconds / 60))
    return f"{m} min"


def pace(records, now: float) -> dict:
    starts = [e["ts"] for e in _episode_list(records, before=now)]
    window_start = now - PACE_WINDOW_S
    recent = sum(1 for t in starts if t >= window_start)

    before = [t for t in starts if t < window_start][-PACE_HISTORY_EPISODES:]
    gaps = [b - a for a, b in zip(before, before[1:]) if b - a < PACE_MAX_GAP_S]
    usual_gap = statistics.median(gaps) if len(gaps) >= PACE_MIN_GAPS else None

    out = {"recent": recent, "n_gaps": len(gaps), "usual_gap_s": usual_gap,
           "usual_count": None, "high": False, "cooldown_s": NUDGE_COOLDOWN,
           "nudge_text": None}
    window = _minutes(PACE_WINDOW_S)
    count = f"{recent} pickup{'s' if recent != 1 else ''} in the last {window}"
    if usual_gap is None:
        out["text"] = (f"Pace: {count} (learning your usual pace: "
                       f"{len(gaps)} of {PACE_MIN_GAPS} gaps)")
        return out

    usual_count = PACE_WINDOW_S / usual_gap
    need = max(PACE_HIGH_MIN_COUNT, math.ceil(PACE_HIGH_FACTOR * usual_count))
    out["usual_count"] = round(usual_count, 2)
    out["high"] = recent >= need
    out["cooldown_s"] = min(NUDGE_COOLDOWN_MAX, max(NUDGE_COOLDOWN_MIN, usual_gap))
    usual = f"you usually pick up about once every {_minutes(usual_gap)}"
    out["text"] = f"Pace: {count} ({usual})"
    if out["high"]:
        out["nudge_text"] = f"{count[0].upper()}{count[1:]}, but {usual}."
    return out
