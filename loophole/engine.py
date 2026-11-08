"""Ties it together: read the log, score "when" and "where", decide, write.

score_now(...) -> dict for the UI (colour is "green", "amber" or "red")
run(stop, on_score) -> loops every SCORE_EVERY seconds until stop is set
"""
import threading
import time
import traceback
from pathlib import Path

from . import events, nudge, when_score, where_score
from .config import EVENTS_FILE, RANK_THRESHOLD, SCORE_EVERY, STALL_THRESHOLD


def _last_nudge_ts(records: list[dict]) -> float | None:
    shown = [r["ts"] for r in records if r["kind"] == "nudge" and r.get("action") == "shown"]
    return max(shown) if shown else None


def score_now(path: Path = EVENTS_FILE, now: float | None = None, client=None) -> dict:
    """Score the moment `now` (default: current time) and log the result."""
    now = time.time() if now is None else now
    records = list(events.read(path, before=now))   # no peeking past now

    f = when_score.features(records, now)
    parts = when_score.contributions(f)
    when = when_score.stall_score(f)
    where = where_score.score(records, now, client=client)

    do_nudge, reason = nudge.decide(when, where["rank"], f["active_recent"],
                                    _last_nudge_ts(records), now)

    when_high = when >= STALL_THRESHOLD
    where_high = where["rank"] is not None and where["rank"] <= RANK_THRESHOLD
    if do_nudge:
        colour = "red"
    elif when_high or where_high:
        colour = "amber"
    else:
        colour = "green"

    events.write("score", path=path, ts=now, when=when, features=f,
                 where_rank=where["rank"], where_share=where["share"],
                 top=where["top"], nudge=do_nudge, reason=reason, colour=colour)
    if do_nudge:
        events.write("nudge", path=path, ts=now, action="shown")

    return {
        "ts": now,
        "when": when,
        "contributions": parts,          # per-feature share of the when score
        "why": when_score.why(f),        # plain-English reasons, biggest first
        "features": f,
        "where_rank": where["rank"],
        "where_share": where["share"],
        "top": where["top"],
        "top_names": where_score.explain(where["top"]),
        "items_sent": where["items_sent"],
        "where_source": where["source"],  # "api", "cache" or "none"
        "where_error": where["error"],
        "nudge": do_nudge,
        "reason": reason,
        "colour": colour,
    }


def run(stop: threading.Event, on_score=None, path: Path = EVENTS_FILE, client=None) -> None:
    """Score every SCORE_EVERY seconds until `stop` is set. Never dies on errors."""
    while not stop.is_set():
        try:
            result = score_now(path=path, client=client)
            if on_score:
                on_score(result)
        except Exception:
            traceback.print_exc()
        stop.wait(SCORE_EVERY)
