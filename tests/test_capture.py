"""Tests for the data-capture layer: tracker.py and phone.py.

These never call AppleScript and never touch the network.
"""
import json
from unittest import mock

import pytest

from loophole import events, phone, tracker


# --- URL -> domain ----------------------------------------------------------

@pytest.mark.parametrize("url, domain", [
    ("https://www.youtube.com/watch?v=abc", "youtube.com"),
    ("https://old.reddit.com/r/python", "old.reddit.com"),
    ("http://GitHub.com:8080/a/b", "github.com"),
    ("https://user:pw@www.example.org/", "example.org"),
    ("https://wwwx.com/", "wwwx.com"),
    ("about:blank", None),
    ("", None),
    (None, None),
])
def test_domain_of(url, domain):
    assert tracker.domain_of(url) == domain


def test_parse_tab_output_keeps_commas_in_title():
    url, title = tracker.parse_tab_output("https://x.com/home\nHome / X, the app\n")
    assert url == "https://x.com/home"
    assert title == "Home / X, the app"


def test_parse_tab_output_empty():
    assert tracker.parse_tab_output("\n") == (None, None)


def test_browser_tab_ignores_non_browsers():
    with mock.patch("subprocess.run") as run:
        assert tracker.browser_tab("com.microsoft.VSCode") == (None, None)
        run.assert_not_called()


def test_browser_tab_failure_is_quiet():
    fail = mock.Mock(returncode=1, stdout="", stderr="not allowed")
    with mock.patch("subprocess.run", return_value=fail):
        assert tracker.browser_tab("com.google.Chrome") == (None, None)


# --- Change detection and record shapes -------------------------------------

def test_app_record_only_on_change(tmp_path):
    path = tmp_path / "ev.jsonl"
    t = tracker.Tracker(path=path, read_browser=False)
    assert t.app_record("Code", "com.microsoft.VSCode", None, None)
    assert t.app_record("Code", "com.microsoft.VSCode", None, None) is None
    assert t.app_record("Google Chrome", "com.google.Chrome",
                        "https://www.youtube.com/", "YouTube")
    # same app, new tab title -> new record
    assert t.app_record("Google Chrome", "com.google.Chrome",
                        "https://www.youtube.com/", "Cats - YouTube")
    recs = list(events.read(path))
    assert [r["kind"] for r in recs] == ["app", "app", "app"]
    last = recs[-1]
    assert set(last) == {"ts", "kind", "app", "bundle", "url", "domain", "title"}
    assert last["domain"] == "youtube.com"


def test_sample_writes_app_once_and_input_every_tick(tmp_path):
    path = tmp_path / "ev.jsonl"
    t = tracker.Tracker(path=path, read_browser=False)
    fake_input = {"idle": {"any": 1.0, "key": 2.0, "mouse": 1.0, "click": 5.0, "scroll": 9.0},
                  "counts": {"key": 10, "click": 3, "scroll": 4, "mouse": 100}}
    with mock.patch.object(tracker, "front_app", return_value=("Code", "com.microsoft.VSCode")), \
         mock.patch.object(tracker, "read_input", return_value=fake_input):
        t.sample()
        t.sample()
    kinds = [r["kind"] for r in events.read(path)]
    assert kinds == ["app", "input", "input"]
    inp = list(events.read(path, kinds={"input"}))[0]
    assert set(inp) == {"ts", "kind", "idle", "counts"}
    assert set(inp["idle"]) == {"any", "key", "mouse", "click", "scroll"}
    assert set(inp["counts"]) == {"key", "click", "scroll", "mouse"}


def test_read_input_shape():
    # Real CoreGraphics call; it needs no permissions.
    data = tracker.read_input()
    assert set(data["idle"]) == {"any", "key", "mouse", "click", "scroll"}
    assert set(data["counts"]) == {"key", "click", "scroll", "mouse"}
    assert all(isinstance(v, int) for v in data["counts"].values())


# --- ntfy parsing and dedupe ------------------------------------------------

def _line(id, msg, t=1700000000, event="message"):
    return json.dumps({"id": id, "time": t, "event": event, "topic": "x", "message": msg})


def test_parse_line():
    assert phone.parse_line(_line("a1", "  Instagram\n")) == \
        {"id": "a1", "time": 1700000000.0, "app": "Instagram"}
    assert phone.parse_line(_line("a2", "", event="keepalive")) is None
    assert phone.parse_line("") is None
    assert phone.parse_line("not json") is None
    assert phone.parse_line(_line("a3", "   "))["app"] == "Unknown"


@pytest.fixture(autouse=True)
def clear_seen():
    phone._seen.clear()
    yield
    phone._seen.clear()


def _resp(lines):
    r = mock.Mock(text="\n".join(lines) + "\n")
    r.raise_for_status = mock.Mock()
    return r


def test_poll_once_writes_new_messages_once(tmp_path):
    path = tmp_path / "ev.jsonl"
    body = [_line("m1", "Instagram", 100), _line("m2", "TikTok", 105),
            json.dumps({"id": "k", "time": 106, "event": "keepalive", "topic": "x"})]
    with mock.patch("requests.get", return_value=_resp(body)) as get:
        first = phone.poll_once(99.7, topic="t", path=path)
        second = phone.poll_once(99.7, topic="t", path=path)
    assert get.call_args[0][0] == "https://ntfy.sh/t/json?poll=1&since=99"
    assert [r["app"] for r in first] == ["Instagram", "TikTok"]
    assert second == []
    recs = list(events.read(path))
    assert len(recs) == 2
    assert recs[0] == {"ts": 100.0, "kind": "phone", "app": "Instagram", "source": "shortcut"}


def test_poll_once_without_topic_does_nothing(tmp_path):
    with mock.patch("requests.get") as get:
        assert phone.poll_once(0, topic="", path=tmp_path / "ev.jsonl") == []
        get.assert_not_called()


def test_record_manual(tmp_path):
    path = tmp_path / "ev.jsonl"
    rec = phone.record_manual(path=path)
    assert rec["kind"] == "phone" and rec["source"] == "button"
    assert set(rec) == {"ts", "kind", "app", "source"}
    assert list(events.read(path)) == [rec]


def test_send_test_handles_network_error():
    import requests
    with mock.patch("requests.post", side_effect=requests.ConnectionError("down")):
        assert phone.send_test(topic="t") is False
