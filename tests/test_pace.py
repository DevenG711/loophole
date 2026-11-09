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


HIGH = {"high": True, "nudge_text": "4 pickups in the last 10 min, but ...", "cooldown_s": 4 * MIN}


def test_decide_fires_on_pace_and_uses_personal_cooldown():
    ok, reason = nudge.decide(0.0, None, True, None, T0, memory=None, pace=HIGH)
    assert ok and reason.startswith("4 pickups")
    assert nudge.decide(0.0, None, True, T0 - 3 * MIN, T0, pace=HIGH)[0] is False
    assert nudge.decide(0.0, None, True, T0 - 5 * MIN, T0, pace=HIGH)[0] is True
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
