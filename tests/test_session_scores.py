"""Results from an old session's threads must not reach a new session.

A score_now call can take ~10 s. If the user stops and starts a session in
that window, the old engine thread's on_score must be dropped. Same for the
old tracker and phone threads, and the demo "score now" button.
"""
import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from loophole import menubar as mb

NUDGE = {"nudge": True}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "_load", lambda name: None)   # no real threads
    a = mb.build_app(demo=False, events_path=tmp_path / "ev.jsonl", island=False)
    yield a
    a.stop_session()


def _tick(app):
    with mock.patch.object(mb, "notify"), mock.patch("rumps.alert", return_value=0), \
            mock.patch.object(app, "show_nudge") as shown:
        app.tick(None)
    return shown.called


def _restart(app):
    app.start_session(25)
    old = app._current_session
    app.stop_session()
    app.start_session(25)
    assert app._current_session != old
    return old, app._current_session


def test_old_session_score_is_dropped(app):
    old, _ = _restart(app)
    app.on_score(NUDGE, session=old)
    assert app._latest is None
    assert not _tick(app)
    assert app.score is None


def test_current_session_score_is_applied(app):
    _, new = _restart(app)
    app.on_score(NUDGE, session=new)
    assert _tick(app)
    assert app.score == NUDGE


def test_default_session_means_current(app):
    app.start_session(25)
    app.on_score(NUDGE)
    assert app._latest is not None


def test_score_after_stop_is_dropped(app):
    app.start_session(25)
    sid = app._current_session
    app.stop_session()
    assert app._current_session is None
    app.on_score(NUDGE, session=sid)
    assert app._latest is None


def test_stop_and_start_clear_pending_results(app):
    app.start_session(25)
    app.on_score(NUDGE, session=app._current_session)
    app.stop_session()
    assert app._latest is None and app._pending_distraction is None


def test_old_phone_record_updates_last_phone_but_not_distraction(app):
    old, new = _restart(app)
    rec = {"ts": time.time(), "kind": "phone", "app": "TikTok", "source": "shortcut"}
    app.on_phone_record(rec, session=old)
    assert app._last_phone is rec
    assert app._pending_distraction is None
    app.on_phone_record(rec, session=new)
    assert app._pending_distraction is rec


def test_old_tracker_tick_is_dropped(app):
    old, new = _restart(app)
    rec = {"ts": time.time(), "kind": "app", "app": "Safari", "domain": "youtube.com"}
    app.on_tracker_tick(SimpleNamespace(last_app=rec), session=old)
    assert app._pending_distraction is None
    app.on_tracker_tick(SimpleNamespace(last_app=rec), session=new)
    assert app._pending_distraction is rec


def test_threads_get_their_own_session_and_stop_event(app, monkeypatch):
    """start_session binds the id and the session's own stop event into each
    thread, so a slow engine thread from session 1 can't feed session 2."""
    calls, delivered = [], []
    release = threading.Event()

    def run(stop, on_score, path):
        calls.append(stop)
        release.wait(2)
        on_score({"nudge": True, "from_old": stop.is_set()})
        delivered.append(stop)

    fake_engine = SimpleNamespace(run=run)
    monkeypatch.setattr(mb, "_load", lambda name: fake_engine if name == "engine" else None)
    app.start_session(25)
    first_stop = app.stop_event
    app.stop_session()
    app.start_session(25)
    release.set()
    deadline = time.time() + 2
    while len(delivered) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert len(delivered) == 2
    assert calls[0] is first_stop and calls[0].is_set()
    assert calls[1] is app.stop_event and not calls[1].is_set()
    # Only the new session's score was kept; the old thread's was dropped.
    assert app._latest is not None and app._latest[0]["from_old"] is False


def test_guarded_uses_passed_event(app):
    app.stop_event = threading.Event()
    mine = threading.Event()
    seen = []
    app._guarded("x", seen.append, mine)
    assert seen == [mine]


def test_demo_score_captures_session_at_click(app, monkeypatch):
    gate = threading.Event()
    done = threading.Event()

    def score_now(path):
        gate.wait(2)
        return NUDGE

    real = app.on_score

    def on_score(score, session=mb.CURRENT):
        real(score, session=session)
        done.set()

    app.on_score = on_score
    monkeypatch.setattr(mb, "_load", lambda name: SimpleNamespace(score_now=score_now)
                        if name == "engine" else None)
    app.start_session(25)
    app.on_demo_score(None)
    app.stop_session()
    app.start_session(25)   # engine module has no run(): no threads start
    gate.set()
    assert done.wait(2)
    assert app._latest is None
