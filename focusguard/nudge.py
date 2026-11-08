"""The nudge rule: nudge only when BOTH "when" and "where" are high.

decide(...) -> (nudge?, reason in plain English)
"""
from .config import ACTIVE_WITHIN, NUDGE_COOLDOWN, RANK_THRESHOLD, STALL_THRESHOLD

# When BehaviorGPT is down we only have "when", so we ask for a stronger stall.
API_DOWN_FACTOR = 1.5


def decide(when: float, where_rank: int | None, active_recent: bool,
           last_nudge_ts: float | None, now: float) -> tuple[bool, str]:
    if last_nudge_ts is not None and now - last_nudge_ts < NUDGE_COOLDOWN:
        left = (NUDGE_COOLDOWN - (now - last_nudge_ts)) / 60
        return False, f"Recently nudged; waiting {left:.0f} more min."

    if not active_recent:
        return False, f"No input in the last {ACTIVE_WITHIN} s, so you are away or on a break."

    when_high = when >= STALL_THRESHOLD

    if where_rank is None:
        strong = when >= API_DOWN_FACTOR * STALL_THRESHOLD
        if strong:
            return True, "You look very stalled (drift model offline, so using the stall signal only)."
        return False, "Drift model offline; stall not strong enough to nudge on its own."

    where_high = where_rank <= RANK_THRESHOLD
    if when_high and where_high:
        return True, f"You look stalled and a distraction is near the top (rank {where_rank})."
    if when_high:
        return False, f"You look stalled, but your apps still point to work (rank {where_rank})."
    if where_high:
        return False, f"Distractions are close (rank {where_rank}), but you are still working steadily."
    return False, "You are in the flow."
