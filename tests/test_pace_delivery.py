"""A pending pace spell is shown on an open phone distraction screen
(PROTOCOL.md deviation 16), once per spell, with the cooldown and the break
respected. App/web distraction screens never carry it."""
import time
from unittest import mock

import pytest

from loophole import events
from loophole import menubar as mb

PACE_TEXT = "You picked up your phone 3 times in 10 min (by 14:53), but you usually ..."


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "_load", lambda name: None)   # no real threads
    monkeypatch.setattr(mb, "bring_to_front", lambda name: None)
    a = mb.build_app(demo=False, events_path=tmp_path / "ev.jsonl", island=False)
    a.start_session(25)
    yield a
    a.stop_session()


def phone_rec():
    return {"ts": time.time(), "kind": "phone", "app": "TikTok", "source": "shortcut"}


def score(spell_ts, pending=True):
    return {"nudge": False, "nudge_kind": None, "reason": "",
            "pace": {"pending": pending, "pending_ts": spell_ts, "pending_count": 3,
                     "pending_text": PACE_TEXT, "text": "Pace: ...", "cooldown_s": 300}}


def nudges(app):
    return [r for r in events.read(app.path) if r["kind"] == "nudge"]


def open_screen(app, rec):
    """Put a distraction screen up the way island mode leaves it (no modal)."""
    app.distraction = rec
    app.distraction_since = time.time()


def tick(app, sc=None):
    if sc is not None:
        app.on_score(sc)
    with mock.patch.object(mb, "notify"), mock.patch("rumps.alert", return_value=0), \
            mock.patch.object(app, "show_nudge") as shown:
        app.tick(None)
    assert not shown.called   # never a modal on top of the screen


def island(app):
    return mb.island_view(
        in_session=True, on_break=False, colour="green", nudge_active=False,
        back_prompt=False, focused_s=60, session_min=25, remaining_s=600,
        break_left_s=0, bars=[], likely=[], reason="", work_app="Code",
        distraction=mb.distraction_name(app.distraction) if app.distraction else None,
        distraction_pace_text=app.distraction_pace_text)


def test_phone_screen_shows_pace_once(app):
    open_screen(app, phone_rec())
    spell = time.time() - 60
    tick(app, score(spell))
    shown = nudges(app)
    assert len(shown) == 1
    assert shown[0]["action"] == "shown" and shown[0]["nudge_kind"] == "pace"
    assert shown[0]["via"] == "distraction" and not shown[0].get("staged")
    assert app.last_nudge is not None and app.distraction_nudge
    view = island(app)
    assert PACE_TEXT in view["sub"]
    assert ("break", "5-min break", False) in view["buttons"]
    tick(app, score(spell))          # same spell again: nothing new
    tick(app)
    assert len(nudges(app)) == 1


def test_no_pace_record_in_cooldown(app):
    app.last_nudge = time.time() - 60
    open_screen(app, phone_rec())
    tick(app, score(time.time() - 30))
    assert nudges(app) == [] and app.distraction_pace_text == ""


def test_no_pace_record_on_break(app):
    open_screen(app, phone_rec())
    app.break_end = time.time() + 300
    tick(app, score(time.time() - 30))
    assert nudges(app) == []


def test_old_spell_is_not_shown_again(app):
    app.last_nudge = time.time() - 3600
    open_screen(app, phone_rec())
    tick(app, score(app.last_nudge - 60))
    assert nudges(app) == []


def test_app_screen_never_carries_pace(app):
    open_screen(app, {"ts": time.time(), "kind": "app", "app": "Safari",
                      "domain": "youtube.com"})
    tick(app, score(time.time() - 30))
    assert nudges(app) == []
    assert PACE_TEXT not in island(app)["sub"]


def test_im_back_logs_one_dismiss_and_clears(app):
    open_screen(app, phone_rec())
    tick(app, score(time.time() - 30))
    app.on_im_back(None)
    recs = nudges(app)
    assert [r["action"] for r in recs] == ["shown", "dismiss"]
    assert recs[1]["via"] == "distraction"
    assert not app.distraction_nudge and app.distraction_pace_text == ""
    assert app.distraction is None


def test_break_button_logs_break_and_starts_break(app):
    open_screen(app, phone_rec())
    tick(app, score(time.time() - 30))
    app.on_island("break")
    assert [r["action"] for r in nudges(app)] == ["shown", "break"]
    assert app.distraction is None and app.break_end is not None
    assert not app.distraction_nudge


def test_stop_session_clears_fields(app):
    open_screen(app, phone_rec())
    tick(app, score(time.time() - 30))
    app.stop_session()
    assert not app.distraction_nudge and app.distraction_pace_text == ""


def test_island_without_pace_text_is_unchanged():
    kw = dict(in_session=True, on_break=False, colour="green", nudge_active=False,
              back_prompt=False, focused_s=60, session_min=25, remaining_s=600,
              break_left_s=0, bars=[], likely=[], reason="", work_app="Code",
              distraction="TikTok", can_mark_work=False)
    view = mb.island_view(**kw)
    assert view == mb.island_view(**kw, distraction_pace_text="")
    assert view["buttons"] == [("im_back", "I'm back", True)]
    assert view["sub"] == ("You opened TikTok. Put it away, take a short walk "
                           "or get some water, and press the button when you're back.")


def test_view_carries_pending_fields(app):
    app.on_score(score(123.0))
    view = app._latest[1]
    assert view["pace_pending_ts"] == 123.0 and view["pace_pending_text"] == PACE_TEXT
    assert view["pace_nudge_text"] == ""
    app.on_score({**score(123.0), "nudge_kind": "pace", "nudge": True})
    assert app._latest[1]["pace_nudge_text"] == PACE_TEXT
    app.on_score(score(123.0, pending=False))
    assert app._latest[1]["pace_pending_ts"] is None
