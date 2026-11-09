"""Tests for the 2026-11-08 signal fixes in when_score, where_score and tracker.
Synthetic records only; no API calls, no AppleScript."""
from unittest import mock

import pytest

from loophole import events, tracker, when_score, where_score
from loophole.config import HISTORY_MAX_AGE

from test_scoring import T0, app, inp, phone, steady_typing


# 1. A tracker gap step adds neither keys nor active time.

def test_rate_ignores_keys_in_a_long_step():
    # 30 min of keys land in the first step after a gap (session restart).
    recs = [inp(T0, 0), inp(T0 + 1800, 50_000)]
    recs += [inp(T0 + 1800 + i * 2, 50_000 + i * 10) for i in range(1, 301)]   # 300 keys/min
    t = T0 + 1800 + 601
    f = when_score.features(recs, t)
    assert f["key_rate_base"] == pytest.approx(300, rel=0.05)
    assert f["key_drop"] < 0.1


def test_scroll_in_a_long_step_is_not_scroll_no_type():
    recs = steady_typing(T0, 3)
    last = recs[-1]["counts"]["key"]
    recs.append(inp(T0 + 180 + 60, last, scroll=500))   # one 60 s step
    f = when_score.features(recs, T0 + 241)
    assert f["scroll_no_type"] == 0


# 2. A gap counts as a break only with no input across it (or idle after it).

def test_gap_with_typing_is_not_a_break():
    recs = steady_typing(T0, 30)
    last = recs[-1]["counts"]["key"]
    # 323 s gap, 69 keys typed across it, idle small after it.
    recs.append(inp(T0 + 1800 + 323, last + 69, idle=0.4))
    recs += [inp(T0 + 2123 + i * 2, last + 69 + i * 10) for i in range(1, 30)]
    f = when_score.features(recs, T0 + 2123 + 60)
    assert f["minutes_since_break"] > 30


def test_gap_with_no_input_is_a_break():
    recs = steady_typing(T0, 30)
    last = recs[-1]["counts"]["key"]
    # Lid closed: counters frozen, idle reads low after wake (new input).
    recs.append(inp(T0 + 1800 + 600, last, idle=0.5))
    recs += [inp(T0 + 2400 + i * 2, last + i * 10) for i in range(1, 30)]
    f = when_score.features(recs, T0 + 2400 + 60)
    assert f["minutes_since_break"] < 2


def test_gap_with_long_idle_after_is_a_break():
    recs = steady_typing(T0, 30)
    last = recs[-1]["counts"]["key"]
    recs.append(inp(T0 + 1800 + 600, last + 5, idle=300))
    recs += [inp(T0 + 2400 + i * 2, last + 5 + i * 10) for i in range(1, 30)]
    f = when_score.features(recs, T0 + 2400 + 60)
    assert f["minutes_since_break"] < 2


# 3. A failed tab read is not a switch; noise apps are skipped.

def test_failed_tab_read_is_not_a_switch():
    recs = steady_typing(T0, 5)
    recs += [app(T0 + 10, "Google Chrome", domain="github.com"),
             app(T0 + 100, "Google Chrome"),                      # read failed
             app(T0 + 102, "Google Chrome", domain="github.com")]
    f = when_score.features(recs, T0 + 200)
    assert f["dwell_s"] == 190.0
    assert when_score._visits([r for r in recs if r["kind"] == "app"]) == [
        (T0 + 10, ("google chrome", "github.com"))]


def test_switch_into_browser_with_failed_read_is_one_switch():
    apps = [app(T0, "Code"), app(T0 + 50, "Google Chrome"),
            app(T0 + 52, "Google Chrome", domain="youtube.com")]
    assert when_score._visits(apps) == [(T0, ("code", "")),
                                        (T0 + 50, ("google chrome", "youtube.com"))]


def test_ignored_apps_do_not_reset_dwell():
    apps = [app(T0, "Code"), app(T0 + 30, "UserNotificationCenter"), app(T0 + 31, "Finder"),
            app(T0 + 40, "Code")]
    assert when_score._visits(apps) == [(T0, ("code", ""))]


def test_tracker_writes_nothing_on_failed_read_in_same_browser(tmp_path):
    path = tmp_path / "ev.jsonl"
    t = tracker.Tracker(path=path, read_browser=True)
    reads = iter([("https://github.com/a", "Repo"), (None, None), ("https://github.com/a", "Repo")])
    fake_input = {"idle": {}, "counts": {}}
    with mock.patch.object(tracker, "front_app", return_value=("Google Chrome", "com.google.Chrome")), \
         mock.patch.object(tracker, "BROWSERS", {"com.google.Chrome": "x"}), \
         mock.patch.object(tracker, "browser_tab", side_effect=lambda b: next(reads)), \
         mock.patch.object(tracker, "read_input", return_value=fake_input):
        t.sample(); t.sample(); t.sample()
    assert [r["domain"] for r in events.read(path, kinds={"app"})] == ["github.com"]


# 4. The newest kept record is the newest laptop app that maps to an item.

def test_old_app_kept_when_newest_record_is_a_phone_pickup():
    recs = [app(T0, "Code"), phone(T0 + HISTORY_MAX_AGE + 100, "Instagram")]
    t = T0 + HISTORY_MAX_AGE + 110
    assert where_score._specs(recs, t) == [("id", "f_vscode"), ("id", "d_instagram")]
    assert where_score._specs(where_score.app_records(recs), t) == [("id", "f_vscode")]


def test_old_app_kept_when_newest_record_is_finder():
    recs = [app(T0, "Code"), app(T0 + 10, "Finder")]
    t = T0 + 3 * HISTORY_MAX_AGE
    assert where_score._specs(recs, t) == [("id", "f_vscode")]
    assert where_score._specs(where_score.app_records(recs), t) == [("id", "f_vscode")]


# 5. Switches are counted over the same span they are divided by.

def test_switches_counted_since_session_start():
    recs = [{"ts": T0 + 180, "kind": "session", "action": "start"}]
    recs += [app(T0 + i * 10, f"App{i}") for i in range(12)]           # before the session
    recs += [app(T0 + 200, "Code"), app(T0 + 230, "Slack")]
    f = when_score.features(recs, T0 + 300)
    # span = 120 s; only the 2 visits inside it count -> 1 per min.
    assert f["switches_per_min"] == 1.0


# 6. share skips sticky distractions like rank does.

def test_share_skips_sticky_only_with_flag():
    ids = ["d_hulu", "d_spotify_pod", "d_pinterest", "d_instagram"] + ["f_slack"] * 6
    assert where_score._summarise(ids)["share"] == 0.1
    assert where_score._summarise(ids, skip_sticky=False)["share"] == 0.4
