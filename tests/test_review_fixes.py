"""Fixes from the 2026-11-08 review: a cut-off log line, two copies running,
button pickups feeding the memory, phone records leaking into where_gap, and
copy that did not match the live rule."""
import json

from loophole import engine, events, precursor, where_score
from loophole import menubar as mb
from tests.test_precursor import T0, app, day, inp, phone
from tests.test_scoring import FakeClient, steady_typing, write_all


# ---------------------------------------------------------------- the log

def test_cut_off_line_is_skipped_and_counted(tmp_path):
    path = tmp_path / "events.jsonl"
    events.write("app", path=path, ts=T0, app="Code")
    with path.open("a") as f:
        f.write('{"ts": 1700000001.0, "kind": "inp')      # crash mid-write
    events.write("app", path=path, ts=T0 + 2, app="Slack")
    recs = list(events.read(path))
    assert [r["app"] for r in recs] == ["Code", "Slack"]   # the next write was not swallowed
    assert events.bad_lines[str(path)] == 1


def test_records_without_ts_or_kind_are_skipped(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join([json.dumps({"kind": "app"}), json.dumps([1, 2]),
                               json.dumps({"ts": T0, "kind": "app", "app": "Code"})]) + "\n")
    assert [r["app"] for r in events.read(path)] == ["Code"]
    assert events.bad_lines[str(path)] == 2


def test_engine_still_scores_after_a_cut_off_line(tmp_path, monkeypatch):
    monkeypatch.setattr(where_score, "CACHE_FILE", tmp_path / "cache.json")
    path = tmp_path / "events.jsonl"
    write_all(path, steady_typing(T0, 5) + [app(T0, "Code")])
    with path.open("a") as f:
        f.write('{"ts": 17000')
    out = engine.score_now(path=path, now=T0 + 300, client=FakeClient(fail=True))
    assert out["colour"] in ("green", "amber", "red")


def test_only_one_copy_can_hold_the_lock(tmp_path):
    log = tmp_path / "events-me.jsonl"
    first = mb.single_instance_lock(log)
    assert first is not None
    assert mb.single_instance_lock(log) is None
    first.close()                                   # released when the holder goes away
    again = mb.single_instance_lock(log)
    assert again is not None
    again.close()


# ---------------------------------------------------------------- the memory

def test_button_pickups_are_not_episodes():
    recs = [phone(T0 + 100, "tiktok"), phone(T0 + 1000, "instagram", source="button"),
            phone(T0 + 2000, "x", source="test")]
    eps = precursor._episode_list(recs, T0 + 10_000)
    assert [e["app"] for e in eps] == ["Tiktok"]


def test_where_history_for_the_memory_has_no_phone_records():
    recs = [app(T0, "Code"), inp(T0 + 1, 10), phone(T0 + 50, "tiktok"), app(T0 + 60, "Slack")]
    log = precursor._Log(recs, T0 + 100)
    assert all(r["kind"] == "app" for r in log.moves)
    with_phone = where_score._specs(recs, T0 + 100)
    assert ("id", "d_tiktok") in with_phone                     # the live history sees it
    assert ("id", "d_tiktok") not in where_score._specs(log.moves, T0 + 100)


def test_engine_caches_the_laptop_only_history_after_a_pickup(tmp_path, monkeypatch):
    monkeypatch.setattr(where_score, "CACHE_FILE", tmp_path / "cache.json")
    path = tmp_path / "events.jsonl"
    write_all(path, steady_typing(T0, 5) + [app(T0, "Code"), phone(T0 + 250, "tiktok")])
    client = FakeClient(["f_vscode", "d_tiktok"])
    engine.score_now(path=path, now=T0 + 300, client=client)
    assert client.calls == 2            # live history (with TikTok) + laptop-only history
    engine.score_now(path=path, now=T0 + 330, client=client)
    assert client.calls == 2            # both cached now


def test_no_extra_call_without_a_recent_pickup(tmp_path, monkeypatch):
    monkeypatch.setattr(where_score, "CACHE_FILE", tmp_path / "cache.json")
    path = tmp_path / "events.jsonl"
    write_all(path, steady_typing(T0, 5) + [app(T0, "Code")])
    client = FakeClient(["f_vscode", "d_tiktok"])
    engine.score_now(path=path, now=T0 + 300, client=client)
    assert client.calls == 1


# ---------------------------------------------------------------- the words

def test_nudge_copy_matches_the_live_rule():
    for likely in ([], ["Instagram"]):
        msg = mb.nudge_message(likely)
        assert "slowed down" not in msg
        assert "past phone pickups" in msg


def test_memory_text_has_no_statistics():
    more = day(300, pattern_every=15)
    t = T0 + (18 * 15 - 3) * 60 + 120
    m = precursor.match([r for r in more if r["ts"] < t], t)
    assert m["text"]
    assert "σ" not in m["text"] and "moments like this" not in m["text"]
    assert m["lift"] is not None    # the numbers are still there for the dashboard


def test_amber_headline_says_what_amber_means():
    assert mb.ISLAND_HEADLINES["amber"] == "Looks like before a pickup"


# ---------------------------------------------------------------- round 2

from unittest import mock  # noqa: E402

from loophole import phone as phone_mod, tracker  # noqa: E402
from loophole.island import PULSE_FPS, MOVE_FPS  # noqa: E402


def test_this_is_work_allows_one_video_not_the_site():
    tutorial = {"app": "Google Chrome", "domain": "youtube.com", "title": "Python decorators"}
    prank = {"app": "Google Chrome", "domain": "youtube.com", "title": "I pranked my roommate"}
    assert mb.work_key(tutorial) != mb.work_key(prank)
    assert mb.work_key({"app": "Google Chrome", "domain": "www.linkedin.com"}) == "linkedin.com"
    assert mb.work_key({"app": "Messages"}) == "messages"
    assert mb.work_key({"kind": "phone", "app": "tiktok"}) is None


def test_distraction_screen_offers_this_is_work_only_when_it_can():
    base = dict(in_session=True, on_break=False, colour="green", nudge_active=False,
                back_prompt=False, focused_s=60, session_min=25, remaining_s=600,
                break_left_s=0, bars=[], likely=[], reason="", work_app="Code",
                distraction="youtube.com")
    ids = lambda v: [b[0] for b in v["buttons"]]  # noqa: E731
    assert ids(mb.island_view(**base)) == ["im_back"]
    assert ids(mb.island_view(**base, can_mark_work=True)) == ["im_back", "is_work"]


def test_query_strings_are_dropped():
    assert tracker.clean_url("https://www.google.com/search?q=secret#x") == "https://www.google.com/search"
    assert tracker.clean_url("https://youtube.com/shorts/abc?feature=share") == "https://youtube.com/shorts/abc"
    assert tracker.clean_url(None) is None


def test_tracker_writes_urls_without_query(tmp_path):
    t = tracker.Tracker(path=tmp_path / "e.jsonl", read_browser=False)
    rec = t.app_record("Google Chrome", "com.google.Chrome",
                       "https://mail.google.com/mail/u/0/?token=abc#inbox/123", "Inbox")
    assert rec["url"] == "https://mail.google.com/mail/u/0/" and rec["domain"] == "mail.google.com"


def test_incognito_tabs_are_not_recorded():
    out = mock.Mock(returncode=0, stdout="https://secret.example/page\nSecret\nincognito\n")
    with mock.patch("subprocess.run", return_value=out):
        assert tracker.browser_tab("com.google.Chrome") == (None, None)
    out.stdout = "https://ok.example/\nOK\nnormal\n"
    with mock.patch("subprocess.run", return_value=out):
        assert tracker.browser_tab("com.google.Chrome") == ("https://ok.example/", "OK")


def test_weak_ntfy_topics_get_a_warning():
    assert phone_mod.topic_warning("loophole-change-me")
    assert phone_mod.topic_warning("deven")
    assert phone_mod.topic_warning("loophole-" + "x" * 20) is None
    assert phone_mod.topic_warning("") is None


def test_pulse_only_frame_rate_is_lower():
    assert PULSE_FPS < MOVE_FPS


# ---------------------------------------------------------------- round 3
# (debated 2026-11-08: templates need laptop context, >= 3 different pickups
# behind the lift, dev-testing is a tag only. User: ready at 5 usable pickups,
# marked an early, less accurate guess until 15.)

def test_backfilled_pickup_is_an_outcome_not_a_template():
    recs = day(40)                                   # typing in Code for 40 min
    later = T0 + 40 * 60 + 3 * 3600                  # a pickup 3 h later, laptop untouched
    recs.append(phone(later, "instagram"))
    log = precursor._Log(recs, later + 600)
    assert [e["usable"] for e in log.episodes] == [False]
    assert log.templates == []
    m = precursor.match(recs, later + 600)
    assert m["n_episodes"] == 1 and m["n_usable"] == 0 and m["ready"] is False


def test_pickups_during_typing_are_templates():
    recs = day(40) + [phone(T0 + 20 * 60, "tiktok")]
    log = precursor._Log(sorted(recs, key=lambda r: r["ts"]), T0 + 39 * 60)
    assert [e["usable"] for e in log.episodes] == [True]


def test_ready_at_five_but_early_until_fifteen():
    from loophole.config import PRECURSOR_CONFIDENT_EPISODES, PRECURSOR_MIN_EPISODES
    assert (PRECURSOR_MIN_EPISODES, PRECURSOR_CONFIDENT_EPISODES) == (5, 15)
    four = precursor.match(day(70, pattern_every=15), T0 + 69 * 60)       # 4 pickups
    assert four["ready"] is False and four["early"] is False and four["text"] is None
    more = day(160, pattern_every=15)                                       # 10 pickups
    t = T0 + (10 * 15 - 3) * 60 + 120                                       # 2 min into the 10th pattern
    m = precursor.match([r for r in more if r["ts"] < t], t)
    assert m["n_usable"] == 9 and m["ready"] and m["early"]
    assert m["text"] and "Early guess, less accurate" in m["text"]
    full = day(300, pattern_every=15)
    t = T0 + (18 * 15 - 3) * 60 + 120
    m = precursor.match([r for r in full if r["ts"] < t], t)
    assert m["ready"] and not m["early"] and "Early guess" not in (m["text"] or "")


def test_dev_testing_is_tagged_not_filtered():
    recs = day(40) + [app(T0 + 17 * 60, "Google Chrome", domain="ntfy.sh"),
                      app(T0 + 18 * 60, "Code"), phone(T0 + 20 * 60, "tiktok")]
    log = precursor._Log(sorted(recs, key=lambda r: r["ts"]), T0 + 39 * 60)
    (e,) = log.episodes
    assert e["dev_testing"] is True and e["usable"] is True and log.templates == [e]


def test_menu_counts_usable_pickups():
    assert mb.memory_status({"n_episodes": 6, "n_usable": 3, "ready": False}) == \
        "Pickup memory: 3 of 5 pickups needed (6 in total)"
    assert mb.memory_status({"n_episodes": 9, "n_usable": 7, "ready": True, "early": True}) == \
        "Pickup memory: early guesses, less accurate (7 of 15 pickups) (9 in total)"
    assert mb.memory_status({"n_episodes": 16, "n_usable": 16, "ready": True}) == \
        "Pickup memory: learned from 16 pickups"
