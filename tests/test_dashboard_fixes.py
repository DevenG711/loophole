"""Tests for the dashboard fixes: synthetic labels, phone names, sessions,
test/staged exclusion, BehaviorGPT names. Pure helpers only, no Streamlit."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dashboard():
    spec = importlib.util.spec_from_file_location("fg_dashboard_fixes", ROOT / "dashboard" / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dash = load_dashboard()
T0 = 1_700_000_000.0


def test_synthetic_detection():
    assert dash.is_synthetic("events-fake-fakeA.jsonl")
    assert dash.is_synthetic("Sample data (dashboard/sample_events.jsonl)")
    assert dash.is_synthetic("upload.jsonl", [{"ts": 1, "kind": "session", "synthetic": True}])
    assert not dash.is_synthetic("events-deven.jsonl", [{"ts": 1, "kind": "app"}])
    assert not dash.is_synthetic(None)


def test_clean_phone_app():
    assert dash.clean_phone_app("app=snapchat") == "Snapchat"
    assert dash.clean_phone_app("APP= tiktok ") == "Tiktok"
    assert dash.clean_phone_app("Instagram") == "Instagram"
    assert dash.clean_phone_app(None) == "?"
    assert dash.clean_phone_app("app=") == "?"


def test_neutral_apps_match_menubar():
    from loophole import menubar
    assert dash.NEUTRAL_APPS == set(menubar.NEUTRAL_APPS)
    assert dash.app_kind({"app": "System Settings"}) == "neutral"


def test_sessions_without_stop_end_at_next_start_or_last_record():
    recs = [
        {"ts": T0, "kind": "session", "action": "start", "minutes": 50},
        {"ts": T0 + 10, "kind": "app", "app": "Code"},
        {"ts": T0 + 100, "kind": "session", "action": "start", "minutes": 90},
        {"ts": T0 + 110, "kind": "app", "app": "Safari"},
        {"ts": T0 + 200, "kind": "session", "action": "stop", "minutes": 90},
        {"ts": T0 + 250, "kind": "app", "app": "Stray"},
        {"ts": T0 + 300, "kind": "session", "action": "start", "minutes": 25},
        {"ts": T0 + 400, "kind": "app", "app": "Code"},
    ]
    s = dash.split_sessions(recs)
    assert [len(x) for x in s] == [2, 3, 2]
    assert s[0][-1]["ts"] == T0 + 10           # cut before the next start
    assert dash.session_end_reason(s[0]) == "no stop record"
    assert dash.session_end_reason(s[1]) == "stop"
    assert s[2][-1]["ts"] == T0 + 400          # runs to the last record
    assert dash.session_summary(s[1])["length_min"] == round(100 / 60, 1)


def test_test_pings_and_staged_nudges_excluded():
    recs = [
        {"ts": T0, "kind": "session", "action": "start", "minutes": 25},
        {"ts": T0 + 1, "kind": "app", "app": "Code"},
        {"ts": T0 + 5, "kind": "phone", "app": "app=instagram", "source": "shortcut"},
        {"ts": T0 + 6, "kind": "phone", "app": "Test", "source": "test"},
        {"ts": T0 + 7, "kind": "nudge", "action": "shown"},
        {"ts": T0 + 8, "kind": "nudge", "action": "shown", "staged": True},
        {"ts": T0 + 9, "kind": "nudge", "action": "break", "staged": True},
    ]
    f = dash.build_frames(recs)
    assert f["phones"]["app"].tolist() == ["Instagram"]
    assert f["nudges"]["staged"].tolist() == [False, True, True]
    s = dash.session_summary(recs)
    assert s["phone_pickups"] == 1
    assert s["nudges"] == 1
    assert s["breaks_taken"] == 0
    assert s["staged_nudges"] == 1


def test_score_top_names_and_extra_features():
    recs = [{"ts": T0, "kind": "score", "when": 0.5,
             "features": {"idle_s": 1.0, "reading": True},  # new keys must not break
             "where_rank": 2, "top": ["f_gdocs", "d_unknown_xyz"], "colour": "amber"}]
    scores = dash.build_frames(recs)["scores"]
    assert scores["top_ids"].iloc[0] == ["f_gdocs", "d_unknown_xyz"]
    names = scores["top"].iloc[0]
    assert "d_unknown_xyz" in names            # unknown id falls back to the raw id
    assert "f_gdocs" not in names              # known id gets a friendly name
    assert dash.top_names(None) == []


def test_timeline_chart_builds_with_staged_and_synthetic():
    recs = [
        {"ts": T0, "kind": "app", "app": "Code"},
        {"ts": T0 + 30, "kind": "score", "when": 1.2, "where_rank": 3, "top": ["f_gdocs"]},
        {"ts": T0 + 40, "kind": "nudge", "action": "shown", "staged": True},
        {"ts": T0 + 60, "kind": "app", "app": "Safari"},
    ]
    chart = dash.timeline_chart(dash.build_frames(recs), synthetic=True)
    assert "(synthetic)" in str(chart.to_dict())


def test_old_score_records_render_without_memory_or_pull():
    recs = [{"ts": T0, "kind": "app", "app": "Code"},
            {"ts": T0 + 30, "kind": "score", "when": 1.2, "where_rank": 3, "top": ["f_gdocs"]}]
    f = dash.build_frames(recs)
    row = f["scores"].iloc[0]
    assert row["where_gap"] is None or row["where_gap"] != row["where_gap"]
    assert not row["heads_up"]
    assert dash.memory_status(f["scores"])["has_memory"] is False
    assert dash.memory_chart(f) is None and dash.pull_chart(f) is None
    assert dash.timeline_chart(f).to_dict()


def test_memory_status_and_charts_with_new_fields():
    mem = {"n_episodes": 6, "ready": True, "lift": 2.5, "likely_app": "TikTok", "score": 0.7,
           "match_ts": [T0 - 600]}
    recs = [{"ts": T0, "kind": "app", "app": "Code"},
            {"ts": T0 + 30, "kind": "score", "when": 0.4, "where_gap": 0.3, "pull_app": None,
             "pull_z": 0.4, "colour": "green", "nudge": False, "memory": {**mem, "text": None}},
            {"ts": T0 + 60, "kind": "score", "when": 0.4, "where_gap": 0.9, "pull_app": "d_unknown_q",
             "pull_z": 1.8, "colour": "red", "nudge": True, "memory": {**mem, "text": "Looks like 14:02"}},
            {"ts": T0 + 90, "kind": "score", "when": 0.4, "where_gap": 0.8, "colour": "amber",
             "nudge": False, "memory": {**mem, "text": "Looks like 14:02"}},
            {"ts": T0 + 100, "kind": "phone", "app": "TikTok"}]
    f = dash.build_frames(recs)
    s = dash.memory_status(f["scores"])
    assert s == {"has_memory": True, "n_episodes": 6, "ready": True, "heads_up": 2,
                 "nudged": 1, "blocked": 1}
    assert f["scores"]["pull_app"].tolist()[1] == "d_unknown_q"
    assert dash.memory_chart(f).to_dict() and dash.pull_chart(f).to_dict()


def test_memory_field_not_a_dict_is_ignored():
    f = dash.build_frames([{"ts": T0, "kind": "score", "when": 0.1, "memory": "broken"}])
    assert dash.memory_status(f["scores"])["has_memory"] is False


def test_describe_snapshot_shows_pull():
    s = dash.describe_snapshot({"stall": 0.2, "pull_app": "YouTube Shorts", "pull_z": 1.9})
    assert "BehaviorGPT pull: YouTube Shorts (+1.9σ)" in s
