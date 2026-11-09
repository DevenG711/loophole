"""Pickup memory wiring: engine -> nudge -> menu bar. No GUI, tmp logs only."""
import sys
import types

import loophole
from loophole import config, engine, events, nudge, where_score
from loophole.config import STALL_THRESHOLD
from loophole.menubar import island_view, memory_status, with_memory

T0 = 1_700_000_000.0
HI = STALL_THRESHOLD * 1.2   # stalled, but not "very stalled"

MEMORY = {"n_episodes": 20, "ready": True, "matches": [{"ts": T0 - 900, "app": "Instagram",
                                                       "distance": 0.4}],
          "matched_moments": 10, "matched_followed": 6, "base_rate": 0.1, "lift": 6.0,
          "likely_app": "Instagram", "score": 2.0,
          "text": "Last time you worked like this, you picked up Instagram."}


# ---------------------------------------------------------------- nudge.decide
# (the rule itself is tested in test_scoring.py)

def test_decide_memory_is_the_trigger():
    assert nudge.decide(HI, 10, True, None, T0)[0] is False
    assert nudge.decide(HI, 10, True, None, T0, memory=MEMORY)[0] is True
    assert nudge.decide(0.1, 30, True, None, T0, memory=MEMORY)[0] is True   # no stall needed
    assert nudge.decide(HI, 10, True, T0 - 60, T0, memory=MEMORY)[0] is False
    assert nudge.decide(HI, 10, False, None, T0, memory=MEMORY)[0] is False


# ---------------------------------------------------------------- menu bar

BASE = dict(in_session=True, on_break=False, colour="green", nudge_active=False,
            back_prompt=False, focused_s=600, session_min=50, remaining_s=2400,
            break_left_s=0, bars=[1, 2, 3], likely=["YouTube"], reason="In the flow.",
            work_app="Code", momentum="")


def test_island_shows_memory_while_green():
    v = island_view(**BASE, memory_text=MEMORY["text"])
    assert "Heads-up: " + MEMORY["text"] in v["details"]
    assert not any(d.startswith("Likely next") for d in v["details"])   # still hidden


def test_island_without_memory_is_unchanged():
    assert island_view(**BASE) == island_view(**BASE, memory_text="")
    assert not any("Heads-up" in d for d in island_view(**BASE)["details"])


def test_memory_status_and_nudge_message():
    assert memory_status(None) == "Pickup memory: -"
    assert memory_status(MEMORY) == "Pickup memory: learned from 20 pickups"
    assert memory_status({"n_episodes": 2, "ready": False}) == \
        f"Pickup memory: 2 of {config.PRECURSOR_MIN_EPISODES} pickups needed"
    assert with_memory("Take a break?", "") == "Take a break?"
    assert MEMORY["text"] in with_memory("Take a break?", MEMORY["text"])


# ---------------------------------------------------------------- engine

def _log(tmp_path, monkeypatch):
    monkeypatch.setattr(where_score, "CACHE_FILE", tmp_path / "cache.json")
    path = tmp_path / "events.jsonl"
    total = 0
    for i in range(150):
        total += 10
        events.write("input", path=path, ts=T0 + i * 2,
                     counts={"key": total, "click": 0, "scroll": 0},
                     idle_s=0.5, key_idle_s=0.5)
    events.write("app", path=path, ts=T0, app="Code", bundle=None, url=None,
                 domain=None, title=None)
    return path


class FailClient:
    def complete(self, history, **kw):
        raise ConnectionError("API down")


def _fake_precursor(monkeypatch, match):
    fake = types.ModuleType("loophole.precursor")
    fake.match = match
    monkeypatch.setitem(sys.modules, "loophole.precursor", fake)
    monkeypatch.setattr(loophole, "precursor", fake, raising=False)


def test_engine_adds_memory_to_result_and_log(tmp_path, monkeypatch):
    path = _log(tmp_path, monkeypatch)
    seen = []

    def match(records, t):
        seen.append(max(r["ts"] for r in records) < t)
        return MEMORY
    _fake_precursor(monkeypatch, match)
    now = T0 + 301
    out = engine.score_now(path=path, now=now, client=FailClient())
    assert out["memory"] == MEMORY and seen == [True]
    rec = [r for r in events.read(path) if r["kind"] == "score"][-1]
    assert rec["memory"] == {"n_episodes": 20, "n_usable": None, "n_dev_testing": None,
                             "followed_episodes": None, "ready": True, "early": None, "lift": 6.0,
                             "likely_app": "Instagram", "text": MEMORY["text"],
                             "score": 2.0, "match_ts": [T0 - 900]}


def test_engine_survives_precursor_crash(tmp_path, monkeypatch):
    path = _log(tmp_path, monkeypatch)

    def boom(records, t):
        raise ValueError("broken")
    _fake_precursor(monkeypatch, boom)
    out = engine.score_now(path=path, now=T0 + 301, client=FailClient())
    assert out["memory"] is None and out["colour"] in ("green", "amber", "red")
    rec = [r for r in events.read(path) if r["kind"] == "score"][-1]
    assert rec["memory"] is None


def test_engine_with_real_precursor_on_sparse_log(tmp_path, monkeypatch):
    path = _log(tmp_path, monkeypatch)
    out = engine.score_now(path=path, now=T0 + 301, client=FailClient())
    mem = out["memory"]
    assert mem is not None and mem["ready"] is False and mem["text"] is None
