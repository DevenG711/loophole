"""Pure-logic tests for the menu-bar and phone fixes. No GUI, no network."""
import json
from unittest import mock

import pytest

from loophole import config, engine, events, phone
from loophole import menubar as mb


@pytest.fixture(autouse=True)
def clear_seen():
    phone._seen.clear()
    yield
    phone._seen.clear()


# ---- colour rule -------------------------------------------------------------

def test_engine_colour_follows_memory():
    assert engine.colour_for(True, None) == "red"
    assert engine.colour_for(False, {"text": "This looks like 14:16..."}) == "amber"
    assert engine.colour_for(False, {"text": None, "ready": True}) == "green"
    assert engine.colour_for(False, None) == "green"


def test_active_nudge_forces_red():
    assert mb.colour_for({"colour": "green"}, nudge_active=True) == "red"
    assert mb.colour_for(None, nudge_active=True) == "red"
    assert mb.colour_for({"colour": "green"}) == "green"


def test_demo_cooldown_is_at_least_3_min():
    assert mb.DEMO_COOLDOWN >= 180
    assert not mb.cooldown_over(1000, 1000 + 60, mb.DEMO_COOLDOWN)


# ---- nudge popup and staged flag ----------------------------------------------

def test_nudge_choice_maps_alert_buttons():
    assert mb.nudge_choice(1) == "break"
    assert mb.nudge_choice(0) == "dismiss"
    assert mb.nudge_choice(-1) == "phone"
    assert mb.nudge_choice(None) == "dismiss"


def test_nudge_record_staged_flag():
    assert mb.nudge_record("shown") == {"action": "shown"}
    assert mb.nudge_record("shown", staged=True) == {"action": "shown", "staged": True}


# ---- phone: test ping, resume, dedupe ----------------------------------------

def _line(id, msg, t, tags=None):
    d = {"id": id, "time": t, "event": "message", "topic": "x", "message": msg}
    if tags:
        d["tags"] = tags
    return json.dumps(d)


def _resp(lines):
    r = mock.Mock(text="\n".join(lines) + "\n")
    r.raise_for_status = mock.Mock()
    return r


def test_send_test_tags_message():
    with mock.patch("requests.post", return_value=mock.Mock(ok=True)) as post:
        assert phone.send_test(topic="t")
    assert post.call_args.kwargs["headers"]["Tags"] == phone.TEST_TAG


def test_test_ping_logged_as_test(tmp_path):
    path = tmp_path / "ev.jsonl"
    body = [_line("m1", "Test", 100, tags=[phone.TEST_TAG]), _line("m2", "Instagram", 105)]
    with mock.patch("requests.get", return_value=_resp(body)):
        phone.poll_once(99, topic="t", path=path)
    recs = list(events.read(path))
    assert [(r["app"], r["source"]) for r in recs] == [("Test", "test"), ("Instagram", "shortcut")]


def test_resume_point_starts_from_last_ntfy_record(tmp_path):
    path = tmp_path / "ev.jsonl"
    events.write("phone", path, ts=500.0, app="Instagram", source="shortcut")
    events.write("phone", path, ts=900.0, app="unknown", source="button")  # not from ntfy
    since, logged = phone.resume_point(path, now=1000.0)
    assert since == 500.0
    assert logged == {(500.0, "Instagram")}


def test_resume_point_empty_log_and_max_age(tmp_path):
    path = tmp_path / "ev.jsonl"
    assert phone.resume_point(path, now=1000.0) == (1000.0, set())
    events.write("phone", path, ts=10.0, app="X", source="shortcut")
    now = 10.0 + phone.BACKFILL_MAX_S + 500
    assert phone.resume_point(path, now=now)[0] == now - phone.BACKFILL_MAX_S


def test_restart_does_not_duplicate_logged_pickups(tmp_path):
    path = tmp_path / "ev.jsonl"
    events.write("phone", path, ts=500.0, app="Instagram", source="shortcut")
    since, logged = phone.resume_point(path, now=1000.0)
    # After a restart _seen is empty, so ntfy sends the 500 s message again.
    body = [_line("m1", "Instagram", 500), _line("m2", "TikTok", 700)]
    with mock.patch("requests.get", return_value=_resp(body)) as get:
        written = phone.poll_once(since, topic="t", path=path, logged=logged)
    assert "since=500" in get.call_args[0][0]
    assert [r["app"] for r in written] == ["TikTok"]   # missed pickup recovered
    assert len(list(events.read(path))) == 2


def test_last_pickup_skips_test_pings(tmp_path):
    path = tmp_path / "ev.jsonl"
    assert phone.last_pickup(path) is None
    events.write("phone", path, ts=100.0, app="Instagram", source="shortcut")
    events.write("phone", path, ts=200.0, app="Test", source="test")
    assert phone.last_pickup(path)["app"] == "Instagram"


def test_last_phone_text():
    assert mb.last_phone_text(None, 0) == "Last phone: none yet"
    assert mb.last_phone_text({"ts": 0, "app": "Instagram", "source": "shortcut"}, 150) == \
        "Last phone: Instagram, 2 min ago"
    assert "(test ping)" in mb.last_phone_text({"ts": 0, "app": "Test", "source": "test"}, 0)
