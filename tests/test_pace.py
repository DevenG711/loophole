"""Pickup pace (loophole/pace.py) and how it reaches the nudge rule."""
from loophole import config, engine, nudge, pace
from loophole import menubar as mb

T0 = 1_800_000_000.0
MIN = 60.0


def phone(ts, source="shortcut"):
    return {"ts": ts, "kind": "phone", "app": "instagram", "source": source}


def steady(n, gap_min, end):
    """n pickups gap_min apart, the last one at `end`."""
    return [phone(end - (n - 1 - i) * gap_min * MIN) for i in range(n)]


def test_learning_until_enough_gaps():
    p = pace.pace(steady(4, 6, T0 - 30 * MIN), T0)   # 3 gaps < PACE_MIN_GAPS
    assert p["usual_gap_s"] is None and p["high"] is False
    assert p["cooldown_s"] == config.NUDGE_COOLDOWN
    assert "learning your usual pace" in p["text"]


def test_usual_gap_sets_cooldown():
    p = pace.pace(steady(8, 6, T0 - 30 * MIN), T0)
    assert p["usual_gap_s"] == 6 * MIN
    assert p["cooldown_s"] == 6 * MIN
    assert p["recent"] == 0 and not p["high"]
    assert "once every 6 min" in p["text"]


def test_cooldown_is_clamped():
    fast = pace.pace(steady(8, 2.5, T0 - 30 * MIN), T0)
    assert fast["cooldown_s"] == config.NUDGE_COOLDOWN_MIN
    slow = pace.pace(steady(8, 40, T0 - 30 * MIN), T0)
    assert slow["cooldown_s"] == config.NUDGE_COOLDOWN_MAX


def test_long_gaps_are_time_away():
    recs = steady(6, 6, T0 - 5 * 3600) + steady(6, 6, T0 - 30 * MIN)
    assert pace.pace(recs, T0)["usual_gap_s"] == 6 * MIN


def test_burst_is_high_and_does_not_set_its_own_bar():
    base = steady(8, 6, T0 - 30 * MIN)
    burst = [phone(T0 - m * MIN) for m in (9, 6, 3, 0.5)]
    p = pace.pace(base + burst, T0)
    assert p["usual_gap_s"] == 6 * MIN          # burst is inside the window
    assert p["recent"] == 4 and p["high"]
    assert p["nudge_text"].startswith("4 pickups in the last 10 min")
    assert pace.pace(base + burst[:2], T0)["high"] is False


def test_not_pickups_and_future_ignored():
    base = steady(8, 6, T0 - 30 * MIN)
    extra = [phone(T0 - 2 * MIN, "test"), phone(T0 - 3 * MIN, "button"),
             phone(T0 - 4 * MIN, "hotkey"), phone(T0 + 1)]
    assert pace.pace(base + extra, T0)["recent"] == 0


def test_records_close_together_are_one_pickup():
    base = steady(8, 6, T0 - 30 * MIN)
    burst = [phone(T0 - 5 * MIN), phone(T0 - 5 * MIN + 30), phone(T0 - 5 * MIN + 90)]
    assert pace.pace(base + burst, T0)["recent"] == 1


HIGH = {"high": True, "nudge_text": "4 pickups in the last 10 min, but ...", "cooldown_s": 4 * MIN,
        "pending": True, "pending_ts": T0 - 30, "pending_count": 4,
        "pending_text": "4 pickups in the last 10 min, but ..."}


def test_decide_fires_on_pace_and_uses_personal_cooldown():
    ok, reason = nudge.decide(0.0, None, True, None, T0, memory=None, pace=HIGH)
    assert ok and reason.startswith("4 pickups")
    assert nudge.decide(0.0, None, True, T0 - 3 * MIN, T0, pace=HIGH)[0] is False
    later = {**HIGH, "pending_ts": T0 - 1}   # a new spell after the last nudge
    assert nudge.decide(0.0, None, True, T0 - 5 * MIN, T0, pace=later)[0] is True
    assert nudge.decide(0.0, None, False, None, T0, pace=HIGH)[0] is False   # away


def test_memory_wins_over_pace():
    mem = {"text": "This looks like 14:16...", "ready": True}
    assert engine.nudge_kind(True, mem, HIGH) == "memory"
    assert engine.nudge_kind(True, None, HIGH) == "pace"
    assert engine.nudge_kind(False, None, HIGH) is None
    assert engine.colour_for(False, None, HIGH) == "amber"


def test_messages():
    assert mb.nudge_message([], "4 pickups in the last 10 min.").startswith("4 pickups")
    assert "past phone pickups" in mb.nudge_message([])


# ---- pending spells (deviation 16) ------------------------------------------

def session(ts):
    return {"ts": ts, "kind": "session", "action": "start", "minutes": 25}


BASE7 = steady(8, 7, T0 - 60 * MIN)      # usual gap 7 min -> need 3


def test_spell_stays_pending_after_pace_drops():
    burst = [phone(T0 - m * MIN) for m in (12, 9, 5)]   # 3rd pickup at T0-5 min
    p = pace.pace(BASE7 + burst, T0)
    assert p["recent"] == 2 and not p["high"]
    assert p["pending"] and p["pending_ts"] == T0 - 5 * MIN and p["pending_count"] == 3
    assert p["pending_text"].startswith("You picked up your phone 3 times in 10 min (by ")
    assert "once every 7 min" in p["pending_text"]


def test_spell_expires_after_pending_window():
    burst = [phone(T0 - m * MIN) for m in (12, 9, 5)]
    later = T0 + config.PACE_PENDING_S - 5 * MIN + 1
    assert pace.pace(BASE7 + burst, later)["pending"] is False


def test_spell_before_session_start_is_ignored():
    burst = [phone(T0 - m * MIN) for m in (12, 9, 5)]
    assert pace.pace(BASE7 + burst + [session(T0 - 6 * MIN)], T0)["pending"]
    p = pace.pace(BASE7 + burst + [session(T0 - 4 * MIN)], T0)
    assert p["pending"] is False and p["pending_ts"] is None


def test_pending_ignores_records_at_or_after_now():
    burst = [phone(T0 - m * MIN) for m in (8, 5)]
    p = pace.pace(BASE7 + burst + [phone(T0), phone(T0 + 30)], T0)
    assert p["pending"] is False
    assert pace.pace(BASE7 + burst + [session(T0)] + [phone(T0 - 1 * MIN)], T0)["pending"]


PENDING = {"high": False, "pending": True, "pending_ts": T0 - 5 * MIN, "pending_count": 3,
           "pending_text": "You picked up your phone 3 times in 10 min (by 14:53), but ...",
           "cooldown_s": 4 * MIN}


def test_decide_pace_fires_when_back_even_if_just_returned():
    ok, reason = nudge.decide(0.0, None, False, None, T0, pace=PENDING, idle_s=2)
    assert ok and reason.startswith("You picked up your phone 3 times")


def test_decide_pace_waits_while_away():
    ok, reason = nudge.decide(0.0, None, False, None, T0, pace=PENDING, idle_s=40)
    assert not ok and "when you are back at the laptop" in reason


def test_decide_pace_once_per_spell():
    shown = T0 - 4 * MIN            # nudged after the spell started
    later = T0 + 30 * MIN           # long after any cooldown
    ok, reason = nudge.decide(0.0, None, True, shown, later, pace=PENDING, idle_s=1)
    assert not ok and reason == "Already nudged about this burst of pickups."
    new = {**PENDING, "pending_ts": later - 60}
    assert nudge.decide(0.0, None, True, shown, later, pace=new, idle_s=1)[0] is True


def test_decide_pace_respects_cooldown():
    new = {**PENDING, "pending_ts": T0 - 30}
    ok, reason = nudge.decide(0.0, None, True, T0 - 2 * MIN, T0, pace=new, idle_s=1)
    assert not ok and reason.startswith("Recently nudged")


def test_decide_memory_still_needs_active_recent():
    mem = {"text": "This looks like 14:16...", "ready": True}
    assert nudge.decide(0.0, None, False, None, T0, memory=mem, idle_s=2)[0] is False
    assert nudge.decide(0.0, None, True, None, T0, memory=mem, idle_s=2)[0] is True


def test_decide_without_idle_uses_active_recent():
    assert nudge.decide(0.0, None, False, None, T0, pace=PENDING)[0] is False
    assert nudge.decide(0.0, None, True, None, T0, pace=PENDING)[0] is True


def test_engine_amber_while_pending():
    assert engine.colour_for(False, None, PENDING) == "amber"
    assert engine.nudge_kind(True, None, PENDING) == "pace"
    # memory text present but just back (not active_recent): only pace can fire
    assert engine.nudge_kind(True, {"text": "x"}, PENDING, active_recent=False) == "pace"
    assert engine.colour_for(False, None, {**PENDING, "pending": False}) == "green"


def test_engine_passes_no_idle_without_inputs(tmp_path, monkeypatch):
    from loophole import events, where_score
    path = tmp_path / "ev.jsonl"
    for r in BASE7 + [phone(T0 - m * MIN) for m in (8, 5, 1)]:
        events.write("phone", path, **{k: v for k, v in r.items() if k != "kind"})
    monkeypatch.setattr(where_score, "score", lambda *a, **k: {
        "rank": None, "share": None, "top": [], "items_sent": 0, "source": "none",
        "error": None})
    monkeypatch.setattr(engine, "_fill_app_only_where", lambda *a, **k: None)
    monkeypatch.setattr(engine, "_memory", lambda *a, **k: None)
    seen = {}
    real = nudge.decide

    def spy(*a, **k):
        seen.update(k)
        return real(*a, **k)

    monkeypatch.setattr(nudge, "decide", spy)
    out = engine.score_now(path=path, now=T0)
    assert seen["idle_s"] is None
    assert out["nudge"] is False and out["colour"] == "amber"
    assert out["pace"]["pending"] and out["pace"]["pending_count"] == 3
    rec = [r for r in events.read(path) if r["kind"] == "score"][-1]
    assert rec["pace"]["pending_ts"] == out["pace"]["pending_ts"]
    assert rec["pace"]["pending_text"]
    events.write("input", path, ts=T0 + 10, keys=5, clicks=0, scrolls=0,
                 idle={"any": 1, "key": 1, "click": 1})
    engine.score_now(path=path, now=T0 + 12)
    assert seen["idle_s"] == 3.0
