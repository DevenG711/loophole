"""The nudge rule: nudge when the pickup memory speaks up, or when you are
picking up your phone much faster than your usual pace (loophole/pace.py).

The pickup memory (precursor.match) compares right now with the minutes
before your past real pickups, using the "when" stall score, BehaviorGPT's
"where" pull and how you are moving. It only has something to say when this
kind of moment was followed by a pickup clearly more often than usual
(PRECURSOR_MIN_LIFT) and there are enough past pickups (PRECURSOR_MIN_EPISODES).
The pace trigger and the personal cooldown (time between nudges = your usual
gap between pickups) were added 2026-11-08 (PROTOCOL.md deviation 14).

Pace is high right after a burst of pickups, when you are on the phone and
the laptop is idle. So the pace trigger works on a "pending" spell (pace.py)
and shows it once you are back: idle <= ACTIVE_WITHIN, without the
just-returned grace the memory needs. At most one nudge per spell
(deviation 16). The cooldown applies to both triggers.

decide(...)         -> (nudge?, reason in plain English)   the live rule
decide_when_where() -> the previous rule (stall AND BehaviorGPT rank), kept
                       for the evals' comparison rows
"""
from .config import (ACTIVE_WITHIN, NUDGE_COOLDOWN, NUDGE_RANK_THRESHOLD,
                     PRECURSOR_MIN_EPISODES, STALL_THRESHOLD)

# When BehaviorGPT is down we only have "when", so we ask for a stronger stall.
API_DOWN_FACTOR = 1.5


def _cooldown_block(last_nudge_ts: float | None, now: float,
                    cooldown: float = NUDGE_COOLDOWN) -> str | None:
    if last_nudge_ts is not None and now - last_nudge_ts < cooldown:
        left = (cooldown - (now - last_nudge_ts)) / 60
        return f"Recently nudged; waiting {left:.0f} more min."
    return None


def _activity_block(active_recent: bool) -> str | None:
    if not active_recent:
        return (f"No input in the last {ACTIVE_WITHIN} s, or you just came back "
                "from a pause, so you are away or settling back in.")
    return None


def _blocked(active_recent: bool, last_nudge_ts: float | None, now: float,
             cooldown: float = NUDGE_COOLDOWN) -> str | None:
    return _cooldown_block(last_nudge_ts, now, cooldown) or _activity_block(active_recent)


def decide(when: float, where_rank: int | None, active_recent: bool,
           last_nudge_ts: float | None, now: float, *,
           memory: dict | None = None, pace: dict | None = None,
           idle_s: float | None = None) -> tuple[bool, str]:
    """Nudge when the pickup memory has a match worth showing (and you are
    active, not just back), or when a high-pace spell is pending and you are
    back at the laptop. `pace` (pace.pace) also sets the cooldown.

    `idle_s` is seconds since the last input, or None when there is no input
    data; then the pace trigger uses `active_recent` instead.

    `when` and `where_rank` are kept in the signature (callers pass them
    positionally) but no longer decide on their own: they are part of what the
    memory compares.
    """
    cooldown = (pace or {}).get("cooldown_s") or NUDGE_COOLDOWN
    blocked = _cooldown_block(last_nudge_ts, now, cooldown)
    if blocked:
        return False, blocked
    away = _activity_block(active_recent)
    if memory and memory.get("text") and not away:
        if memory.get("early"):
            return True, ("Early guess (less accurate, few pickups so far): this moment looks "
                          "like the ones right before your past phone pickups.")
        return True, "This moment looks like the ones right before your past phone pickups."
    if pace and pace.get("pending"):
        spell = pace.get("pending_ts")
        if last_nudge_ts is not None and (spell is None or spell <= last_nudge_ts):
            return False, "Already nudged about this burst of pickups."
        back = idle_s <= ACTIVE_WITHIN if idle_s is not None else active_recent
        if back:
            return True, (pace.get("pending_text") or pace.get("nudge_text")
                          or "You are picking up your phone more than usual.")
        return False, ("Your pickup pace was high; the nudge will show when you are "
                       "back at the laptop.")
    if away:
        return False, away
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
