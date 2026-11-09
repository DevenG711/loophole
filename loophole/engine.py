"""Ties it together: read the log, score "when" (local stall) and "where"
(BehaviorGPT), ask the pickup memory whether this moment looks like the ones
before past pickups (it uses both), decide, write.

score_now(...) -> dict for the UI (colour is "green", "amber" or "red")
run(stop, on_score) -> loops every SCORE_EVERY seconds until stop is set
"""
import threading
import time
import traceback
from pathlib import Path

from . import events, nudge, when_score, where_score
from .config import EVENTS_FILE, SCORE_EVERY


def _last_nudge_ts(records: list[dict]) -> float | None:
    """Time of the last nudge actually shown to the user. The menu bar writes
    `nudge action=shown` only when it displays one (the engine only decides).
    Staged nudges (demo button, --demo mode) don't start the cooldown."""
    shown = [r["ts"] for r in records if r["kind"] == "nudge" and r.get("action") == "shown"
             and not r.get("staged")]
    return max(shown) if shown else None


def colour_for(do_nudge: bool, memory: dict | None, pace: dict | None = None) -> str:
    """Dot colour follows the pickup memory and pace. Red: nudge. Amber: the
    memory recognises this moment, or a high-pace spell is pending (pace.py),
    but it can't nudge now (cooldown, away, or already nudged about it).
    Green: otherwise."""
    if do_nudge:
        return "red"
    if memory and memory.get("text"):
        return "amber"
    if pace and pace.get("pending"):
        return "amber"
    return "green"


def _memory(records: list[dict], now: float) -> dict | None:
    """Pickup memory for this moment. Never lets a failure stop the engine."""
    try:
        from . import precursor
        return precursor.match(records, now)
    except Exception:
        traceback.print_exc()
        return None


def _pace(records: list[dict], now: float) -> dict | None:
    """Pickup pace for this moment (pace.pace). Never stops the engine."""
    try:
        from . import pace
        return pace.pace(records, now)
    except Exception:
        traceback.print_exc()
        return None


def nudge_kind(do_nudge: bool, memory: dict | None, pace: dict | None,
               active_recent: bool = True) -> str | None:
    """Which trigger fired: "memory" (checked first, as in nudge.decide) or "pace".
    As in nudge.decide, the memory only fires when active_recent; otherwise a
    nudge with memory text present came from the pace trigger."""
    if not do_nudge:
        return None
    if memory and memory.get("text") and active_recent:
        return "memory"
    if pace and pace.get("pending"):
        return "pace"
    return None


def _fill_app_only_where(records: list[dict], now: float, client) -> None:
    """The pickup memory reads BehaviorGPT from the cache only, for a history
    of laptop apps alone (no phone records). That history differs from the
    live one only within PHONE_HISTORY_MAX_AGE of a pickup; then ask for it
    too, so the cache has it. Same data as the live call, minus phone apps."""
    try:
        apps = where_score.app_records(records)
        if where_score._specs(apps, now) != where_score._specs(records, now):
            where_score.score(apps, now, client=client)
    except Exception:
        traceback.print_exc()


def _memory_log(memory: dict | None) -> dict | None:
    """Small version of the memory for the score record."""
    if not memory:
        return None
    return {"n_episodes": memory.get("n_episodes"), "n_usable": memory.get("n_usable"),
            "n_dev_testing": memory.get("n_dev_testing"),
            "followed_episodes": memory.get("followed_episodes"), "ready": memory.get("ready"),
            "early": memory.get("early"),
            "lift": memory.get("lift"), "likely_app": memory.get("likely_app"),
            "text": memory.get("text"), "score": memory.get("score"),
            "status": memory.get("status"), "match_share": memory.get("match_share"),
            "match_ts": [m.get("ts") for m in memory.get("matches") or []]}


def score_now(path: Path = EVENTS_FILE, now: float | None = None, client=None) -> dict:
    """Score the moment `now` (default: current time) and log the result."""
    now = time.time() if now is None else now
    records = list(events.read(path, before=now))   # no peeking past now

    f = when_score.features(records, now)
    parts = when_score.contributions(f)
    when = when_score.stall_score(f)
    where = where_score.score(records, now, client=client)
    _fill_app_only_where(records, now, client)

    memory = _memory(records, now)
    pace = _pace(records, now)

    # features() reports idle_s=0.0 when there are no input samples; the pace
    # trigger must not take that as "at the laptop", so pass None then.
    has_inputs = any(r["kind"] == "input" for r in records)
    do_nudge, reason = nudge.decide(when, where["rank"], f["active_recent"],
                                    _last_nudge_ts(records), now, memory=memory, pace=pace,
                                    idle_s=f["idle_s"] if has_inputs else None)
    kind = nudge_kind(do_nudge, memory, pace, f["active_recent"])

    colour = colour_for(do_nudge, memory, pace)

    events.write("score", path=path, ts=now, when=when, features=f,
                 where_rank=where["rank"], where_share=where["share"],
                 top=where["top"], where_gap=where.get("gap"), pull_app=where.get("pull_app"),
                 pull_z=where.get("pull_z"), nudge=do_nudge, reason=reason, colour=colour,
                 memory=_memory_log(memory), nudge_kind=kind,
                 pace=({k: v for k, v in pace.items() if k != "text"} if pace else None))
    # The decision lives in the score record (nudge=do_nudge). A `nudge
    # action=shown` record is written by the UI only when it really shows one
    # (it may not: break, a nudge or distraction screen already up, cooldown).

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
        "where_gap": where.get("gap"),    # BehaviorGPT's lean to distraction (z units)
        "pull_app": where.get("pull_app"),
        "pull_name": where.get("pull_name"),
        "pull_z": where.get("pull_z"),
        "nudge": do_nudge,
        "nudge_kind": kind,              # "memory", "pace" or None
        "reason": reason,
        "colour": colour,
        "memory": memory,                # pickup memory (precursor.match), or None
        "pace": pace,                    # pickup pace and personal cooldown (pace.pace), or None
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
