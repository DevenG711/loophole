"""The nudge rule: nudge when the pickup memory speaks up, or when you are
picking up your phone much faster than your usual pace (loophole/pace.py).

The pickup memory (precursor.match) compares right now with the minutes
before your past real pickups, using the "when" stall score, BehaviorGPT's
"where" pull and how you are moving. It only has something to say when this
kind of moment was followed by a pickup clearly more often than usual
(PRECURSOR_MIN_LIFT) and there are enough past pickups (PRECURSOR_MIN_EPISODES).
The pace trigger and the personal cooldown (time between nudges = your usual
gap between pickups) were added 2026-11-08 (PROTOCOL.md deviation 14).

decide(...)         -> (nudge?, reason in plain English)   the live rule
decide_when_where() -> the previous rule (stall AND BehaviorGPT rank), kept
                       for the evals' comparison rows
"""
from .config import (ACTIVE_WITHIN, NUDGE_COOLDOWN, NUDGE_RANK_THRESHOLD,
                     PRECURSOR_MIN_EPISODES, STALL_THRESHOLD)

# When BehaviorGPT is down we only have "when", so we ask for a stronger stall.
API_DOWN_FACTOR = 1.5


def _blocked(active_recent: bool, last_nudge_ts: float | None, now: float,
             cooldown: float = NUDGE_COOLDOWN) -> str | None:
    if last_nudge_ts is not None and now - last_nudge_ts < cooldown:
        left = (cooldown - (now - last_nudge_ts)) / 60
        return f"Recently nudged; waiting {left:.0f} more min."
    if not active_recent:
        return (f"No input in the last {ACTIVE_WITHIN} s, or you just came back "
                "from a pause, so you are away or settling back in.")
    return None


def decide(when: float, where_rank: int | None, active_recent: bool,
           last_nudge_ts: float | None, now: float, *,
           memory: dict | None = None, pace: dict | None = None) -> tuple[bool, str]:
    """Nudge when the pickup memory has a match worth showing, or when the
    pickup pace is high. `pace` (pace.pace) also sets the cooldown.

    `when` and `where_rank` are kept in the signature (callers pass them
    positionally) but no longer decide on their own: they are part of what the
    memory compares.
    """
    cooldown = (pace or {}).get("cooldown_s") or NUDGE_COOLDOWN
    blocked = _blocked(active_recent, last_nudge_ts, now, cooldown)
    if blocked:
        return False, blocked
    if memory and memory.get("text"):
        if memory.get("early"):
            return True, ("Early guess (less accurate, few pickups so far): this moment looks "
                          "like the ones right before your past phone pickups.")
        return True, "This moment looks like the ones right before your past phone pickups."
    if pace and pace.get("high"):
        return True, pace.get("nudge_text") or "You are picking up your phone more than usual."
    if not memory or not memory.get("ready"):
        mem = memory or {}
        n = mem.get("n_usable", mem.get("n_episodes")) or 0
        return False, (f"Still learning: {n} of {PRECURSOR_MIN_EPISODES} pickups "
                       "(with the laptop in use before them) so far.")
    return False, "Nothing here looks like the moments before your past pickups."


def decide_when_where(when: float, where_rank: int | None, active_recent: bool,
                      last_nudge_ts: float | None, now: float) -> tuple[bool, str]:
    """The previous rule: stall AND a distraction near the top of BehaviorGPT's
    ranking. Not used live; kept so the evals can compare against it."""
    blocked = _blocked(active_recent, last_nudge_ts, now)
    if blocked:
        return False, blocked
    when_high = when >= STALL_THRESHOLD
    if where_rank is None:
        if when >= API_DOWN_FACTOR * STALL_THRESHOLD:
            return True, "You look very stalled (drift model offline, so using the stall signal only)."
        return False, "Drift model offline; stall not strong enough to nudge on its own."
    where_high = where_rank <= NUDGE_RANK_THRESHOLD
    if when_high and where_high:
        return True, f"You look stalled and a distraction is near the top (rank {where_rank})."
    if when_high:
        return False, f"You look stalled, but your apps still point to work (rank {where_rank})."
    if where_high:
        return False, f"Distractions are close (rank {where_rank}), but you are still working steadily."
    return False, "You are in the flow."
