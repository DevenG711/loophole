"""Tests for the review fixes to pickup labels, nudge logging and the eval:
button/hotkey presses are not labels, `nudge shown` only when really shown,
staged nudges skip the cooldown, no chained episodes, subset "caught"
denominators, the dev_testing row, ntfy-id dedupe and pickups.json staleness.
Synthetic records only; no API calls."""
import json
import sys
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation import eval_pickups as ep  # noqa: E402
from loophole import config, engine, events, nudge, phone, precursor  # noqa: E402
from loophole import menubar as mb  # noqa: E402
from tests.test_scoring import FakeClient, steady_typing, write_all  # noqa: E402

T0 = 1_800_000_000.0


def ph(ts, app="Instagram", source="shortcut"):
    return {"ts": ts, "kind": "phone", "app": app, "source": source}


# ---- 1. button / hotkey presses are not labels ----

def test_button_and_hotkey_are_not_labels():
    recs = [ph(T0, source="test"), ph(T0 + 100, source="button"), ph(T0 + 200, source="hotkey"),
            ph(T0 + 300, source="button"), ph(T0 + 900)]
    kept, dropped = ep.pickup_records(recs)
    assert [r["ts"] for r in kept] == [T0 + 900]
    assert dropped == {"dropped_source": {"test": 1, "button": 2, "hotkey": 1}, "skipped_range": 0}
    assert {"test", "button", "hotkey"} <= precursor.NOT_EPISODE_SOURCES


def test_nudge_then_button_is_not_a_caught_pickup():
    """A nudge answered with "I picked up my phone" must not create a positive."""
    from evaluation.fake_data import simulate
    recs = [r for r in simulate("fakeLbl", 1.0, seed=3) if r["kind"] != "phone"]
    mid = recs[len(recs) // 2]["ts"]
    with_button = sorted(recs + [ph(mid, "unknown", source="button")], key=lambda r: r["ts"])
    rows, pickups, wsrc = ep.moments_for("p", with_button)
    assert pickups == [] and not any(r["y"] for r in rows)
    # the where history still sees it, as the live where_score does
    assert any(r.get("source") == "button" for r in wsrc)


# ---- 2. the engine only decides; `shown` is written where it is displayed ----

def _engine_log(tmp_path):
    path = tmp_path / "ev.jsonl"
    write_all(path, [{"ts": T0, "kind": "app", "app": "Code", "bundle": "com.microsoft.VSCode"}]
              + steady_typing(T0, 12))
    return path


def test_engine_does_not_log_shown(tmp_path, monkeypatch):
    path = _engine_log(tmp_path)
    monkeypatch.setattr(nudge, "decide", lambda *a, **kw: (True, "forced"))
    monkeypatch.setattr(engine, "_memory", lambda records, now: None)
    out = engine.score_now(path=path, now=T0 + 12 * 60, client=FakeClient(["f_slack"] * 10))
    assert out["nudge"] is True
    recs = list(events.read(path))
    assert [r for r in recs if r["kind"] == "score"][-1]["nudge"] is True
    assert not any(r["kind"] == "nudge" for r in recs)


# ---- 3. staged nudges don't start the cooldown ----

def test_last_nudge_ts_skips_staged():
    recs = [{"ts": T0, "kind": "nudge", "action": "shown"},
            {"ts": T0 + 50, "kind": "nudge", "action": "shown", "staged": True},
            {"ts": T0 + 60, "kind": "nudge", "action": "dismiss"}]
    assert engine._last_nudge_ts(recs) == T0
    assert engine._last_nudge_ts(recs[1:]) is None


def _app(tmp_path, demo):
    app = mb.build_app(demo=demo, events_path=tmp_path / "ev.jsonl", island=False)
    app.session_start = time.time()
    return app


def _tick(app, score):
    app._latest = (score, {})
    with mock.patch.object(mb, "notify"), mock.patch("rumps.alert", return_value=0):
        app.tick(None)
    return [r for r in events.read(app.path) if r["kind"] == "nudge"]


def test_menubar_logs_shown_only_when_displayed(tmp_path):
    app = _app(tmp_path, demo=False)
    app.break_end = time.time() + 300            # on a break: not shown, not logged
    assert _tick(app, {"nudge": True}) == []
    app.break_end = None
    recs = _tick(app, {"nudge": True})
    assert [(r["action"], r.get("staged")) for r in recs] == [("shown", None), ("dismiss", None)]
    assert _tick(app, {"nudge": True}) == recs   # cooldown: nothing new


def test_demo_mode_nudges_are_staged(tmp_path):
    app = _app(tmp_path, demo=True)
    score = {"nudge": False, "memory": {"matches": [{"ts": 1}], "n_episodes": 1}}  # looser rule
    recs = _tick(app, score)
    assert recs[0]["action"] == "shown" and recs[0]["staged"] is True
    assert engine._last_nudge_ts(recs) is None


# ---- 4. episodes: no chaining ----

def test_episode_joins_only_within_merge_gap_of_its_first_record():
    g = config.PICKUP_MERGE_S
    recs = [ph(T0), ph(T0 + 0.8 * g), ph(T0 + 1.6 * g), ph(T0 + 2.4 * g)]   # each < g from the last
    eps = ep.merge_episodes(recs)
    assert [e["ts"] for e in eps] == [T0, T0 + 1.6 * g]
    assert [e["n"] for e in eps] == [2, 2]


def test_eval_episodes_match_precursor():
    g = config.PICKUP_MERGE_S
    recs = [ph(T0 + x) for x in (0, 0.8 * g, 1.6 * g, 2.4 * g, 10 * g, 10 * g + 5)]
    assert [e["ts"] for e in ep.merge_episodes(recs)] == \
        [e["ts"] for e in precursor._episode_list(recs, float("inf"))]


# ---- 5. subset "caught" denominators ----

def test_subset_caught_counts_only_pickups_in_its_range():
    rows = [{"person": "p", "t": T0 + i * 30} for i in range(20, 40)]   # T0+600 .. T0+1170
    pick = {"p": [T0 + 100, T0 + 700, T0 + 1300, T0 + 1400], "q": [T0 + 700]}
    assert ep.pickups_in_rows(rows, pick) == {"p": [T0 + 700, T0 + 1300], "q": []}


# ---- 6. dev_testing sensitivity row ----

def _mem_rows():
    rows = []
    for i in range(40):
        t = T0 + i * 30
        y = int(any(t + 30 < p <= t + 180 for p in (T0 + 400, T0 + 1000)))
        rows.append({"person": "p", "t": t, "y": y, "memory": float(y)})
    return rows


def test_dev_testing_row_drops_only_dev_positives():
    rows = _mem_rows()
    pick = {"p": [T0 + 400, T0 + 1000]}
    res = ep.dev_testing_row(rows, pick, {"p": [T0 + 400]}, 0.1)
    n_dev_pos = sum(1 for r in rows if r["y"] and r["t"] + 30 < T0 + 400 <= r["t"] + 180)
    assert res["n_moments"] == len(rows) - n_dev_pos
    assert res["n_pickups"] == 1 and res["method"] == ep.DEV_ROW
    assert ep.dev_testing_row(rows, pick, {"p": []}, 0.1)["n_moments"] == len(rows)


def test_dev_testing_times_use_precursor_tag():
    from tests.test_precursor import app, day, phone as pphone
    T = 1_700_000_000.0
    recs = sorted(day(40) + [app(T + 17 * 60, "Google Chrome", domain="ntfy.sh"),
                             app(T + 18 * 60, "Code"), pphone(T + 20 * 60, "tiktok")],
                  key=lambda r: r["ts"])
    assert ep.dev_testing_times(recs) == [T + 20 * 60]


# ---- 7. ntfy dedupe by message id ----

def _line(mid, msg, t):
    return json.dumps({"id": mid, "time": t, "event": "message", "topic": "x", "message": msg})


def _resp(lines):
    r = mock.Mock(text="\n".join(lines) + "\n")
    r.raise_for_status = mock.Mock()
    return r


def test_two_messages_in_one_second_are_both_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(phone, "_seen", set())
    path = tmp_path / "ev.jsonl"
    since, logged = phone.resume_point(path, now=1000.0)
    body = [_line("a1", "Instagram", 1000), _line("a2", "Instagram", 1000)]
    with mock.patch("requests.get", return_value=_resp(body)):
        written = phone.poll_once(since, topic="t", path=path, logged=logged)
    assert [r["ntfy_id"] for r in written] == ["a1", "a2"]
    # restart: both refetched, neither written again
    monkeypatch.setattr(phone, "_seen", set())
    since, logged = phone.resume_point(path, now=1001.0)
    with mock.patch("requests.get", return_value=_resp(body + [_line("a3", "TikTok", 1001)])):
        again = phone.poll_once(since, topic="t", path=path, logged=logged)
    assert [r["ntfy_id"] for r in again] == ["a3"]


def test_old_records_without_id_still_dedupe(tmp_path, monkeypatch):
    monkeypatch.setattr(phone, "_seen", set())
    path = tmp_path / "ev.jsonl"
    events.write("phone", path, ts=500.0, app="Instagram", source="shortcut")   # no ntfy_id
    since, logged = phone.resume_point(path, now=1000.0)
    with mock.patch("requests.get", return_value=_resp([_line("m1", "Instagram", 500)])):
        assert phone.poll_once(since, topic="t", path=path, logged=logged) == []


# ---- 8. pickups.json says what it was built from ----

def test_pickups_json_records_source_log(tmp_path, monkeypatch):
    from evaluation.fake_data import simulate
    log = tmp_path / "events-fakeStale.jsonl"
    write_all(log, simulate("fakeStale", 1.0, seed=2))
    monkeypatch.setattr(ep, "RESULTS", tmp_path)
    monkeypatch.setattr(ep, "CACHE", tmp_path / "c.json")
    monkeypatch.setattr(ep, "LIVE_CACHE", tmp_path / "live.json")
    monkeypatch.setattr(ep, "add_memory", lambda rows, srcs: [r.update(memory=None) for r in rows]
                        and None)
    ep.main(["--events", str(log), "--offline"])
    out = json.loads((tmp_path / "pickups.json").read_text())
    (src,) = out["source_logs"]
    recs = list(events.read(log))
    assert src["bytes"] == log.stat().st_size and src["n_records"] == len(recs)
    assert src["last_record_ts"] == max(r["ts"] for r in recs)
    assert "dev_testing" in out and "dropped_source" in out["labels"]["events-fakeStale"]
