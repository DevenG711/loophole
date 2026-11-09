"""run() must move `since` past every ntfy message it has seen, not only past
messages it wrote, so a restart does not re-download the whole catch-up
window every poll. Network is mocked."""
import json
import threading
import time
from unittest import mock

import pytest
import requests

from loophole import events, phone


@pytest.fixture(autouse=True)
def clear_seen(monkeypatch):
    monkeypatch.setattr(phone, "_seen", set())
    monkeypatch.setattr(phone, "PHONE_POLL_EVERY", 0)


def _line(id, msg, t, tags=None):
    d = {"id": id, "time": t, "event": "message", "topic": "x", "message": msg}
    if tags:
        d["tags"] = tags
    return json.dumps(d)


def _resp(lines):
    r = mock.Mock(text="\n".join(lines) + ("\n" if lines else ""))
    r.raise_for_status = mock.Mock()
    return r


def _since(url):
    return int(url.split("since=")[1])


def _run(path, responses):
    """Run the poller over `responses` (Mock responses or exceptions), one per
    poll, then stop. Returns (request urls, records passed to on_record)."""
    stop = threading.Event()
    urls, got = [], []
    queue = list(responses)

    def fake_get(url, timeout=None):
        urls.append(url)
        item = queue.pop(0)
        if not queue:
            stop.set()
        if isinstance(item, Exception):
            raise item
        return item

    with mock.patch("requests.get", side_effect=fake_get):
        phone.run(stop, topic="t" * 30, path=path, on_record=got.append)
    return urls, got


def _seed(path, now):
    # One ntfy pickup already logged 1 h ago: the restart resumes from there.
    events.write("phone", path, ts=now - 3600, app="Instagram",
                 source="shortcut", ntfy_id="old1")


def test_already_logged_messages_move_since(tmp_path, monkeypatch):
    path = tmp_path / "ev.jsonl"
    t0 = int(time.time() - 3600)
    # Restart: resume from t0, and both messages ntfy returns (a pickup and a
    # test ping) are already in the log.
    logged = {phone._logged_key("old1", t0, "Instagram"),
              phone._logged_key("tp1", t0 + 60, "Test")}
    monkeypatch.setattr(phone, "resume_point", lambda p: (float(t0), logged))
    catch_up = [_line("old1", "Instagram", t0), _line("tp1", "Test", t0 + 60, ["loophole_test"])]
    urls, got = _run(path, [_resp(catch_up), _resp([])])
    assert got == []
    assert _since(urls[0]) == t0
    assert _since(urls[1]) == t0 + 60      # moved past the skipped messages


def test_new_messages_written_once_and_since_moves(tmp_path):
    path = tmp_path / "ev.jsonl"
    now = time.time()
    _seed(path, now)
    t0 = int(now - 3600)
    first = [_line("old1", "Instagram", t0), _line("n1", "TikTok", t0 + 100)]
    # ntfy's whole-second `since` returns n1 again, plus a new one.
    second = [_line("n1", "TikTok", t0 + 100), _line("n2", "Snapchat", t0 + 200)]
    urls, got = _run(path, [_resp(first), _resp(second), _resp([])])
    assert [r["ntfy_id"] for r in got] == ["n1", "n2"]
    assert [_since(u) for u in urls] == [t0, t0 + 100, t0 + 200]
    ids = [r.get("ntfy_id") for r in events.read(path, kinds={"phone"})]
    assert ids.count("n1") == 1 and ids.count("n2") == 1


def test_request_error_leaves_since_unchanged(tmp_path):
    path = tmp_path / "ev.jsonl"
    now = time.time()
    _seed(path, now)
    t0 = int(now - 3600)
    err = requests.ConnectionError("down")
    bad = mock.Mock(text=_line("x9", "TikTok", t0 + 500) + "\n")
    bad.raise_for_status = mock.Mock(side_effect=requests.HTTPError("500"))
    urls, got = _run(path, [err, bad, _resp([])])
    assert got == []
    assert [_since(u) for u in urls] == [t0, t0, t0]


def test_poll_once_cursor_is_optional_and_reports_newest(tmp_path):
    path = tmp_path / "ev.jsonl"
    body = [_line("a", "TikTok", 100), _line("b", "Instagram", 90)]
    with mock.patch("requests.get", return_value=_resp(body)):
        assert len(phone.poll_once(50, topic="t", path=path)) == 2   # old call shape
    cursor = {}
    with mock.patch("requests.get", return_value=_resp(body)):
        assert phone.poll_once(50, topic="t", path=path, cursor=cursor) == []   # all _seen
    assert cursor["newest"] == 100
    cursor = {}
    with mock.patch("requests.get", return_value=_resp([])):
        phone.poll_once(50, topic="t", path=path, cursor=cursor)
    assert "newest" not in cursor
