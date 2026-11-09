"""Notch island: the pure state -> view mapping (no GUI)."""
from loophole import config
from loophole.menubar import island_view

BASE = dict(in_session=True, on_break=False, colour="green", nudge_active=False,
            back_prompt=False, focused_s=600, session_min=50, remaining_s=2400,
            break_left_s=0, bars=[1, 2, 3], likely=["YouTube"], reason="You are in the flow.",
            work_app="Code", momentum="Momentum ▁▂▃  steady (100% of your last 10 min)")


def ids(view):
    return [b[0] for b in view["buttons"]]


def test_idle_offers_session_lengths():
    v = island_view(**{**BASE, "in_session": False})
    assert ids(v) == ["start:25", "start:50", "start:90"]
    assert not v.get("pinned")


def test_flow_shows_countdown_and_progress():
    v = island_view(**BASE)
    assert v["value"] == "40:00"
    assert v["progress"] == 600 / 3000
    assert v["colour"] == "green" and not v["pinned"]


def test_nudge_pins_open_with_three_choices():
    v = island_view(**{**BASE, "nudge_active": True})
    assert v["colour"] == "red" and v["pinned"] and v["pulse"]
    assert ids(v) == ["break", "fine", "phone"]
    assert "YouTube" in v["sub"]


def test_break_counts_down():
    v = island_view(**{**BASE, "on_break": True, "break_left_s": 252})
    assert v["value"] == "4:12"
    assert 0 < v["progress"] < 1
    assert config.BREAK_MINUTES * 60 > 252


def test_back_prompt_beats_everything():
    v = island_view(**{**BASE, "back_prompt": True, "nudge_active": True})
    assert v["pinned"] and ids(v) == ["back", "not_yet"]
    assert "Code" in v["headline"]


def test_distraction_pins_open_with_im_back():
    v = island_view(**{**BASE, "distraction": "youtube.com"})
    assert v["pinned"] and v["colour"] == "red"
    assert v["headline"] == "Distraction detected"
    assert "youtube.com" in v["sub"] and "walk" in v["sub"]
    assert ids(v) == ["im_back"]


def test_distraction_ignored_outside_session_or_on_break():
    assert island_view(**{**BASE, "distraction": "x.com", "in_session": False})["headline"] != "Distraction detected"
    assert island_view(**{**BASE, "distraction": "x.com", "on_break": True,
                          "break_left_s": 100})["headline"] == "On a break"


def test_distraction_name():
    from loophole.menubar import distraction_name
    assert distraction_name({"app": "Google Chrome", "domain": "www.youtube.com"}) == "youtube.com"
    assert distraction_name({"app": "Messages", "domain": None}) == "Messages"
    # The single all-apps iPhone automation sends "Phone"
    assert distraction_name({"kind": "phone", "app": "Phone"}) == "your phone"
    assert distraction_name({"kind": "phone", "app": "tiktok"}) == "TikTok on your phone"


def test_phone_pickup_triggers_distraction():
    from loophole.menubar import distraction_name, phone_triggers_distraction
    now = 1000.0
    rec = {"ts": 990.0, "kind": "phone", "app": "tiktok", "source": "shortcut"}
    assert phone_triggers_distraction(rec, now)
    assert distraction_name(rec) == "TikTok on your phone"
    assert not phone_triggers_distraction({**rec, "source": "test"}, now)
    assert not phone_triggers_distraction({**rec, "source": "button"}, now)
    assert not phone_triggers_distraction({**rec, "ts": now - 3600}, now)   # backfilled
