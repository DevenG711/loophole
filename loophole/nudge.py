"""The nudge rule: nudge only when the pickup memory speaks up.

The pickup memory (precursor.match) compares right now with the minutes
before your past real pickups, using the "when" stall score, BehaviorGPT's
"where" pull and how you are moving. It only has something to say when this
kind of moment was followed by a pickup clearly more often than usual
(PRECURSOR_MIN_LIFT) and there are enough past pickups (PRECURSOR_MIN_EPISODES).

decide(...)         -> (nudge?, reason in plain English)   the live rule
decide_when_where() -> the previous rule (stall AND BehaviorGPT rank), kept
                       for the evals' comparison rows
"""
from .config import ACTIVE_WITHIN, NUDGE_COOLDOWN, NUDGE_RANK_THRESHOLD, STALL_THRESHOLD

# When BehaviorGPT is down we only have "when", so we ask for a stronger stall.
API_DOWN_FACTOR = 1.5


def _blocked(active_recent: bool, last_nudge_ts: float | None, now: float) -> str | None:
    if last_nudge_ts is not None and now - last_nudge_ts < NUDGE_COOLDOWN:
        left = (NUDGE_COOLDOWN - (now - last_nudge_ts)) / 60
        return f"Recently nudged; waiting {left:.0f} more min."
    if not active_recent:
        return (f"No input in the last {ACTIVE_WITHIN} s, or you just came back "
                "from a pause, so you are away or settling back in.")
    return None


def decide(when: float, where_rank: int | None, active_recent: bool,
           last_nudge_ts: float | None, now: float, *,
           memory: dict | None = None) -> tuple[bool, str]:
    """Nudge only when the pickup memory has a match worth showing.

    `when` and `where_rank` are kept in the signature (callers pass them
    positionally) but no longer decide on their own: they are part of what the
    memory compares.
    """
    blocked = _blocked(active_recent, last_nudge_ts, now)
    if blocked:
        return False, blocked
    if memory and memory.get("text"):
        return True, "This moment looks like the ones right before your past phone pickups."
    if not memory or not memory.get("ready"):
        n = (memory or {}).get("n_episodes") or 0
        return False, f"Still learning: {n} real pickups so far."
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
