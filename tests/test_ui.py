"""Tests for the pure helpers in the menu bar and the dashboard. No GUI."""
import importlib.util
import json
from pathlib import Path

import pytest

from focusguard import menubar as mb

ROOT = Path(__file__).resolve().parent.parent


def load_dashboard():
    spec = importlib.util.spec_from_file_location("fg_dashboard", ROOT / "dashboard" / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # does not draw the page (main() is behind __main__)
    return mod


dash = load_dashboard()


# ---- menu bar: formatting ---------------------------------------------------

def test_format_minutes():
    assert mb.format_minutes(0) == "0 min"
    assert mb.format_minutes(12.9 * 60) == "12 min"
    assert mb.format_minutes(75 * 60) == "1 h 15 min"
    assert mb.format_minutes(-5) == "0 min"


def test_format_countdown():
    assert mb.format_countdown(299) == "4:59"
    assert mb.format_countdown(5) == "0:05"
    assert mb.format_countdown(-3) == "0:00"


def test_last_break_text():
    assert mb.last_break_text(None, 100) == "Last break: none yet"
    assert mb.last_break_text(0, 600) == "Last break: 10 min ago"


# ---- menu bar: colour + nudge logic -----------------------------------------

def test_colour_uses_engine_colour():
    assert mb.colour_for({"colour": "amber"}) == "amber"
    assert mb.colour_for(None) == "green"


def test_colour_fallback():
    assert mb.colour_for({"nudge": True}) == "red"
    assert mb.colour_for({"when": 0.6, "where_rank": 50}, stall=1.0, rank=10) == "amber"
    assert mb.colour_for({"when": 0.0, "where_rank": 15}, stall=1.0, rank=10) == "amber"
    assert mb.colour_for({"when": 0.1, "where_rank": None}, stall=1.0, rank=10) == "green"


def test_wants_nudge_demo_is_looser():
    mild = {"when": 0.5, "where_rank": 20, "nudge": False}
    assert not mb.wants_nudge(mild, demo=False)
    assert mb.wants_nudge(mild, demo=True)
    assert mb.wants_nudge({"nudge": True})
    assert not mb.wants_nudge(None, demo=True)


def test_cooldown():
    assert mb.cooldown_over(None, 100, 1200)
    assert not mb.cooldown_over(100, 200, 1200)
    assert mb.cooldown_over(100, 1300, 1200)


# ---- menu bar: momentum ----------------------------------------------------

def test_activity_per_minute_uses_differences():
    now = 1000.0
    recs = [
        {"ts": now - 300, "counts": {"key": 100, "click": 0}},   # baseline
        {"ts": now - 250, "counts": {"key": 130, "click": 0}},   # +30, 4 min ago
        {"ts": now - 10, "counts": {"key": 140, "click": 5}},    # +15, this minute
        {"ts": now - 5, "counts": {"key": 2, "click": 0}},       # reboot -> 0
    ]
    b = mb.activity_per_minute(recs, now, minutes=10)
    assert len(b) == 10
    assert b[-1] == 15
    assert b[-5] == 30
    assert sum(b) == 45


def test_sparkline_and_momentum_text():
    assert mb.sparkline([0, 0]) == "▁▁"
    assert mb.sparkline([0, 7]) == "▁█"
    assert mb.momentum_text([0] * 10) == "Momentum: no typing yet"
    assert "stalled" in mb.momentum_text([10] * 8 + [0, 0])
    assert "steady" in mb.momentum_text([10] * 10)
    assert "picking up" in mb.momentum_text([5] * 8 + [20, 20])


# ---- menu bar: names, work app ----------------------------------------------

def test_friendly_names_filters_and_falls_back():
    top = ["f_vscode", "d_twitter", "d_youtube", "d_reddit"]
    assert mb.friendly_names(top) == ["X", "YouTube"]
    assert mb.friendly_names(top, explain=lambda ids: ["Twitter", "YouTube"]) == ["Twitter", "YouTube"]
    # A broken explain() must not break the menu
    assert mb.friendly_names(top, explain=lambda ids: 1 / 0) == ["X", "YouTube"]
    assert mb.friendly_names(["f_vscode"]) == []


def test_nudge_message():
    assert "Instagram looks like your next move" in mb.nudge_message(["Instagram"])
    assert "5-minute break" in mb.nudge_message([])


def test_last_work_app_skips_distractions():
    apps = [
        {"app": "Code"},
        {"app": "Google Chrome", "domain": "github.com"},
        {"app": "Google Chrome", "domain": "youtube.com"},
        {"app": "Finder"},
    ]
    assert mb.last_work_app(apps) == "Google Chrome"
    assert mb.last_work_app([{"app": "Finder"}]) is None


def test_app_kind_matches_whole_domains():
    assert mb.app_kind({"app": "Chrome", "domain": "www.youtube.com"}) == "distraction"
    assert mb.app_kind({"app": "Chrome", "domain": "m.youtube.com"}) == "distraction"
    assert mb.app_kind({"app": "Chrome", "domain": "console.aws.amazon.com"}) == "work"
    assert mb.app_kind({"app": "Chrome", "domain": "dropbox.com"}) == "work"
    assert mb.app_kind({"app": "Messages"}) == "distraction"
    assert dash.app_kind({"app": "Chrome", "domain": "x.com"}) == "distraction"


# ---- dashboard --------------------------------------------------------------

def make_records(t0=1_700_000_000.0):
    return [
        {"ts": t0, "kind": "session", "action": "start", "minutes": 25},
        {"ts": t0 + 1, "kind": "app", "app": "Code", "bundle": "x", "url": None,
         "domain": None, "title": None},
        {"ts": t0 + 61, "kind": "app", "app": "Safari", "bundle": "y",
         "url": "https://youtube.com", "domain": "youtube.com", "title": "cats"},
        {"ts": t0 + 30, "kind": "score", "when": 0.4, "where_rank": 1, "where_share": 0.3,
         "top": ["d_youtube"], "nudge": False, "features": {}},
        {"ts": t0 + 90, "kind": "score", "when": 1.4, "where_rank": None, "where_share": 0.0,
         "top": [], "nudge": True, "features": {}},
        {"ts": t0 + 91, "kind": "nudge", "action": "shown"},
        {"ts": t0 + 95, "kind": "nudge", "action": "break"},
        {"ts": t0 + 100, "kind": "phone", "app": "Instagram", "source": "shortcut"},
        {"ts": t0 + 121, "kind": "session", "action": "stop", "minutes": 2},
    ]


def test_parse_jsonl_sorts_and_skips_junk():
    recs = make_records()
    text = "\n".join(json.dumps(r) for r in reversed(recs)) + "\nnot json\n\n{\"no\": 1}\n"
    out = dash.parse_jsonl(text)
    assert len(out) == len(recs)
    assert [r["ts"] for r in out] == sorted(r["ts"] for r in recs)


def test_closeness():
    assert dash.closeness(1) == 1.0
    assert dash.closeness(None) == 0.0
    assert dash.closeness(float("nan")) == 0.0
    assert dash.closeness(10) > dash.closeness(30) > 0


def test_split_sessions():
    recs = make_records()
    second = make_records(t0=1_700_010_000.0)
    stray = {"ts": 1_700_005_000.0, "kind": "app", "app": "Code"}
    assert len(dash.split_sessions(recs + [stray] + second)) == 2
    no_session = [r for r in recs if r["kind"] != "session"]
    assert len(dash.split_sessions(no_session)) == 1
    assert dash.split_sessions([]) == []


def test_build_frames():
    f = dash.build_frames(dash.parse_jsonl("\n".join(json.dumps(r) for r in make_records())))
    apps = f["apps"]
    assert list(apps["kind"]) == ["work", "distraction"]
    assert apps["seconds"].tolist() == [60, 60]
    assert f["scores"]["closeness"].tolist() == [1.0, 0.0]
    assert f["nudges"]["action"].tolist() == ["shown", "break"]
    assert f["phones"]["app"].tolist() == ["Instagram"]


def test_build_frames_empty():
    f = dash.build_frames([])
    assert all(df.empty for df in f.values())


def test_session_summary():
    s = dash.session_summary(dash.parse_jsonl("\n".join(json.dumps(r) for r in make_records())))
    assert s["planned_min"] == 25
    assert s["work_share"] == 0.5
    assert s["nudges"] == 1 and s["breaks_taken"] == 1
    assert s["phone_pickups"] == 1
    assert s["max_stall"] == pytest.approx(1.4)


def test_timeline_chart_builds():
    f = dash.build_frames(dash.parse_jsonl("\n".join(json.dumps(r) for r in make_records())))
    chart = dash.timeline_chart(f)
    assert chart.to_dict()  # valid Vega-Lite spec
