"""Tests for the scoring engine. Run: .venv/bin/python -m pytest tests -q
The live API test runs only with LIVE=1.
"""
import os
from types import SimpleNamespace

import pytest

from loophole import engine, events, nudge, when_score, where_score
from loophole.config import NUDGE_COOLDOWN, STALL_THRESHOLD

T0 = 1_700_000_000.0


# ---------------------------------------------------------------- helpers

def inp(ts, key, scroll=0, click=0, idle=0.0, key_idle=None):
    """An input record with running-total counts."""
    k_idle = idle if key_idle is None else key_idle
    return {"ts": ts, "kind": "input",
            "idle": {"any": idle, "key": k_idle, "mouse": idle, "click": k_idle, "scroll": idle},
            "counts": {"key": key, "click": click, "scroll": scroll, "mouse": 0}}


def app(ts, name, bundle=None, domain=None, title=None, url=None):
    return {"ts": ts, "kind": "app", "app": name, "bundle": bundle,
            "url": url, "domain": domain, "title": title}


def phone(ts, name):
    return {"ts": ts, "kind": "phone", "app": name, "source": "button"}


def steady_typing(start, minutes, keys_per_sample=10, every=2):
    """Typing at a steady rate; counts are running totals."""
    recs, total = [], 0
    n = int(minutes * 60 / every)
    for i in range(n + 1):
        recs.append(inp(start + i * every, total))
        total += keys_per_sample
    return recs


def write_all(path, records):
    for r in records:
        r = dict(r)
        events.write(r.pop("kind"), path=path, ts=r.pop("ts"), **r)


class FakeClient:
    def __init__(self, ids=None, fail=False):
        self.ids, self.fail, self.calls = ids or [], fail, 0

    def complete(self, history, **kw):
        self.calls += 1
        if self.fail:
            raise ConnectionError("API down")
        return SimpleNamespace(items=[SimpleNamespace(id=i, score=1.0) for i in self.ids])


# ---------------------------------------------------------------- when

def test_no_peeking_records_at_or_after_t_are_ignored():
    recs = steady_typing(T0, 10)
    t = T0 + 600
    base = when_score.features(recs, t)
    # A huge typing burst exactly at t and after t must change nothing.
    later = recs + [inp(t, 10**6), inp(t + 2, 2 * 10**6), app(t, "Safari", domain="youtube.com")]
    assert when_score.features(later, t) == base


def test_phone_records_never_affect_features():
    recs = steady_typing(T0, 10) + [app(T0 + 100, "Code")]
    t = T0 + 600
    base = when_score.features(recs, t)
    with_phone = recs + [phone(T0 + 50, "Instagram"), phone(T0 + 599, "TikTok")]
    assert when_score.features(with_phone, t) == base


def test_counter_differencing_and_reset():
    # 10 keys every 2 s = 300 keys/min, steady.
    recs = steady_typing(T0, 10)
    f = when_score.features(recs, T0 + 601)
    assert f["key_rate_now"] == pytest.approx(300, rel=0.05)
    assert f["key_rate_base"] == pytest.approx(300, rel=0.05)
    assert f["key_drop"] == pytest.approx(0, abs=0.05)

    # Counter resets to 0 in the middle (tracker restart): no negative typing.
    reset = recs[:150] + [inp(r["ts"], r["counts"]["key"] - recs[150]["counts"]["key"])
                          for r in recs[150:]]
    g = when_score.features(reset, T0 + 601)
    assert g["key_rate_base"] > 250
    assert g["key_rate_now"] == pytest.approx(f["key_rate_now"], rel=0.05)


def test_typing_drop_and_scroll_no_type():
    # 8 min of typing, then 2 min of scrolling only.
    recs = steady_typing(T0, 8)
    total = recs[-1]["counts"]["key"]
    for i in range(1, 61):
        recs.append(inp(T0 + 480 + i * 2, total, scroll=i * 5, key_idle=i * 2))
    f = when_score.features(recs, T0 + 601)
    assert f["key_rate_now"] == 0
    assert f["key_drop"] == 1.0
    assert f["scroll_no_type"] > 100
    assert f["active_recent"] is True
    assert when_score.stall_score(f) > 0.6


def test_burst_pause_and_break():
    recs = []
    # burst of 100 keys over 20 s, then 60 s with no keys, then another burst
    total = 0
    ts = T0
    for _ in range(2):
        for _ in range(10):
            total += 10
            recs.append(inp(ts, total)); ts += 2
        for _ in range(30):
            recs.append(inp(ts, total)); ts += 2
    f = when_score.features(recs, ts)
    assert f["burst_pause_count"] == 2

    # A 5-minute idle sample counts as a break.
    recs2 = steady_typing(T0, 30) + [inp(T0 + 1802, 9000, idle=300)] + steady_typing(T0 + 1804, 2)
    g = when_score.features(recs2, T0 + 1804 + 120)
    assert g["minutes_since_break"] < 3


def test_contributions_sum_to_score():
    f = {"key_drop": 1.0, "scroll_no_type": 90, "burst_pause_count": 0, "dwell_no_input_s": 0,
         "switches_per_min": 0, "minutes_since_break": 0, "idle_s": 0}
    parts = when_score.contributions(f)
    assert parts["key_drop"] == 0.6 and parts["scroll_no_type"] == 0.4
    assert when_score.stall_score(f) == pytest.approx(sum(parts.values()))
    assert when_score.why(f)[0] == when_score.REASONS["key_drop"]


# ---------------------------------------------------------------- where

def test_app_to_id_mapping():
    recs = [
        app(T0 + 1, "Code", bundle="com.microsoft.VSCode"),
        app(T0 + 2, "iTerm2"),
        app(T0 + 3, "Google Chrome", domain="github.com"),
        app(T0 + 4, "Messages"),
        app(T0 + 5, "Slack"),
        app(T0 + 6, "Safari", domain="console.aws.amazon.com"),
        app(T0 + 7, "Safari", domain="www.amazon.com"),
        app(T0 + 8, "Warp"),
    ]
    assert [where_score._label(s) for s in where_score._specs(recs, T0 + 100)] == [
        "f_vscode", "f_terminal", "f_github", "d_imessage", "f_slack", "f_aws", "d_amazon", "f_terminal"]


def test_unknown_app_and_phone_mapping():
    recs = [app(T0 + 1, "Some Weird App"), app(T0 + 2, "Arc", domain="example.org"),
            phone(T0 + 3, "Instagram"), phone(T0 + 4, "YouTube"), phone(T0 + 5, "Duolingo")]
    labels = [where_score._label(s) for s in where_score._specs(recs, T0 + 100)]
    assert labels == ["ext:Some Weird App", "ext:example.org", "d_instagram",
                      "d_youtube_shorts", "ext:Duolingo"]


def test_title_domains_become_external_items():
    recs = [app(T0 + 1, "Chrome", domain="www.youtube.com", title="Python asyncio tutorial"),
            app(T0 + 2, "Chrome", domain="reddit.com", title="r/funny - cat prank"),
            app(T0 + 3, "Chrome", domain="youtube.com", url="https://youtube.com/shorts/abc")]
    specs = where_score._specs(recs, T0 + 100)
    assert specs == [("ext", "Python asyncio tutorial", "youtube.com"),
                     ("ext", "r/funny - cat prank", "reddit.com"),
                     ("id", "d_youtube_shorts")]
    items = where_score.to_items(recs, T0 + 100)
    assert len(items) == 3


def test_history_window_and_no_peeking():
    recs = [app(T0 + i, f"App{i}") for i in range(12)] + [phone(T0 + 50, "TikTok")]
    specs = where_score._specs(recs, T0 + 50)   # phone at exactly t is NOT included
    assert len(specs) == 8 and specs[-1] == ("ext", "App11", None)
    specs = where_score._specs(recs, T0 + 51)   # phone before t IS history
    assert specs[-1] == ("id", "d_tiktok")


def test_noise_apps_and_sites_are_skipped():
    recs = [app(T0 + 1, "Finder"), app(T0 + 2, "System Settings"), app(T0 + 3, "Script Editor"),
            app(T0 + 4, "Google Chrome"),                       # no domain: unknown page
            app(T0 + 5, "Google Chrome", domain="ntfy.sh"),
            app(T0 + 6, "Google Chrome", domain="login.microsoftonline.com"),
            app(T0 + 7, "Google Chrome", domain="newtab"),      # kept on purpose
            app(T0 + 8, "Google Chrome", domain="outlook.cloud.microsoft"),
            app(T0 + 9, "Cursor")]
    labels = [where_score._label(s) for s in where_score._specs(recs, T0 + 100)]
    assert labels == ["ext:newtab", "f_gmail", "f_vscode"]


def test_history_drops_old_events():
    from loophole.config import HISTORY_MAX_AGE
    recs = [app(T0, "Messages"), app(T0 + HISTORY_MAX_AGE, "Code")]
    assert where_score._specs(recs, T0 + HISTORY_MAX_AGE + 1) == [("id", "f_vscode")]
    # Long focus in one app: the only record is old, but it is still kept.
    assert where_score._specs(recs[1:], T0 + 3 * HISTORY_MAX_AGE) == [("id", "f_vscode")]


def test_rank_skips_sticky_distractions():
    ids = ["f_zoom", "d_hulu", "f_vscode", "d_spotify_pod", "d_instagram"] + ["f_slack"] * 5
    out = where_score._summarise(ids)
    assert out["rank"] == 4 and out["share"] == 0.3


def test_just_returned_from_pause_is_not_active():
    # Typing, then a 2-minute pause, then one sample of input again.
    recs = steady_typing(T0, 5)
    last = recs[-1]["counts"]["key"]
    recs += [inp(T0 + 300 + i * 2, last, idle=float(i * 2)) for i in range(1, 61)]  # idle to 120 s
    back = T0 + 300 + 122
    recs.append(inp(back, last + 5, idle=0.0))
    f = when_score.features(recs, back + 1)
    assert f["just_returned"] is True and f["active_recent"] is False
    # 90 s later, with steady input, the grace period is over.
    later = back + 92
    recs += [inp(back + i * 2, last + 5 + i, idle=0.0) for i in range(1, 47)]
    f = when_score.features(recs, later + 1)
    assert f["just_returned"] is False and f["active_recent"] is True


def test_where_score_uses_cache(tmp_path):
    cache = tmp_path / "cache.json"
    recs = [app(T0, "Code"), app(T0 + 1, "Messages")]
    ids = ["f_slack", "f_gmail", "d_imessage"] + ["f_vscode"] * 7
    client = FakeClient(ids)
    a = where_score.score(recs, T0 + 10, client=client, cache_path=cache)
    b = where_score.score(recs, T0 + 10, client=client, cache_path=cache)
    assert a["rank"] == 2 and a["share"] == 0.1 and a["top"] == ids[:3]
    assert a["source"] == "api" and b["source"] == "cache" and client.calls == 1


def test_api_failure_falls_back(tmp_path):
    recs = [app(T0, "Code")]
    out = where_score.score(recs, T0 + 10, client=FakeClient(fail=True), cache_path=tmp_path / "c.json")
    assert out["rank"] is None and out["share"] is None and "API down" in out["error"]
    assert not (tmp_path / "c.json").exists()   # failures are not cached


def test_explain():
    assert where_score.explain(["d_youtube_shorts", "f_vscode", "zzz"]) == [
        "YouTube Shorts", "Visual Studio Code", "zzz"]


# ---------------------------------------------------------------- nudge

def test_old_rule_needs_both_scores():
    hi, lo = STALL_THRESHOLD + 0.1, STALL_THRESHOLD - 0.1
    d = nudge.decide_when_where
    assert d(hi, 3, True, None, T0)[0] is True
    assert d(hi, 4, True, None, T0)[0] is False
    assert d(lo, 0, True, None, T0)[0] is False
    assert d(hi, 0, False, None, T0)[0] is False   # away from keyboard
    assert d(STALL_THRESHOLD * 1.2, None, True, None, T0)[0] is False
    assert d(STALL_THRESHOLD * 1.5, None, True, None, T0)[0] is True


MEM = {"n_episodes": 6, "ready": True, "text": "This looks like 14:16, right before you opened Tiktok."}


def test_decide_nudges_only_on_memory():
    hi = STALL_THRESHOLD * 2
    # Stalled with a distraction at #0 used to nudge; now only the memory can.
    assert nudge.decide(hi, 0, True, None, T0)[0] is False
    assert nudge.decide(hi, 0, True, None, T0, memory={**MEM, "text": None})[0] is False
    ok, reason = nudge.decide(0.0, 30, True, None, T0, memory=MEM)
    assert ok is True and "past phone pickups" in reason
    ok, reason = nudge.decide(0.0, 30, True, None, T0, memory={"n_episodes": 2, "ready": False})
    assert ok is False and "2 real pickups" in reason


def test_decide_cooldown_and_away_block_memory():
    ok, reason = nudge.decide(0.0, 0, True, T0 - 60, T0, memory=MEM)
    assert ok is False and "Recently nudged" in reason
    assert nudge.decide(0.0, 0, True, T0 - NUDGE_COOLDOWN - 1, T0, memory=MEM)[0] is True
    assert nudge.decide(0.0, 0, False, None, T0, memory=MEM)[0] is False


# ---------------------------------------------------------------- engine

def test_engine_writes_score_and_nudge(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setattr(where_score, "CACHE_FILE", tmp_path / "cache.json")
    # 8 min of typing, then 2 min of scrolling and app hopping; still active.
    recs = steady_typing(T0, 8)
    total = recs[-1]["counts"]["key"]
    for i in range(1, 61):
        recs.append(inp(T0 + 480 + i * 2, total, scroll=i * 5, idle=1, key_idle=i * 2))
    for i, name in enumerate(["Code", "Messages", "Slack", "Messages", "Safari", "Messages",
                              "Slack", "Messages", "Code", "Messages", "Slack", "Messages"]):
        recs.append(app(T0 + 480 + i * 10, name))
    write_all(path, recs)

    client = FakeClient(["d_imessage"] + ["f_slack"] * 9)
    now = T0 + 601
    # Stalled with a distraction at #0, but no pickup memory: no nudge any more.
    out = engine.score_now(path=path, now=now, client=client)
    assert out["nudge"] is False and out["colour"] == "green", out

    # The pickup memory recognises the moment: nudge.
    from loophole import precursor
    memory = {"n_episodes": 6, "ready": True, "text": "This looks like 14:16..."}
    monkeypatch.setattr(precursor, "match", lambda records, t: memory)
    out = engine.score_now(path=path, now=now + 1, client=client)
    assert out["nudge"] is True and out["colour"] == "red", out
    kinds = [r["kind"] for r in events.read(path) if r["ts"] == now + 1]
    assert kinds == ["score", "nudge"]

    # 30 s later: cooldown blocks it; amber because the memory still matches.
    out2 = engine.score_now(path=path, now=now + 31, client=client)
    assert out2["nudge"] is False and out2["colour"] == "amber"


def test_engine_api_down_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(where_score, "CACHE_FILE", tmp_path / "cache.json")  # not the real cache
    path = tmp_path / "events.jsonl"
    write_all(path, steady_typing(T0, 5) + [app(T0, "Code")])
    out = engine.score_now(path=path, now=T0 + 300, client=FakeClient(fail=True))
    assert out["where_rank"] is None and out["colour"] == "green"


# ---------------------------------------------------------------- live (optional)

@pytest.mark.live
@pytest.mark.skipif(os.getenv("LIVE") != "1", reason="set LIVE=1 to call the real API")
def test_live_coding_ranks_higher_than_youtube(tmp_path):
    cache = tmp_path / "cache.json"   # fresh cache: really call the API
    coding = [app(T0 + i, n, domain=d) for i, (n, d) in enumerate([
        ("Code", None), ("Terminal", None), ("Chrome", "github.com"), ("Code", None),
        ("Chrome", "stackoverflow.com"), ("Code", None), ("Terminal", None), ("Code", None)])]
    youtube = [app(T0 + i, "Chrome", domain=d, title=title) for i, (d, title) in enumerate([
        ("youtube.com", "MrBeast extreme prank compilation"), ("reddit.com", "r/funny - cat fails"),
        ("youtube.com", "Daily vlog: my morning routine"), ("tiktok.com", None),
        ("youtube.com", "Try not to laugh challenge"), ("instagram.com", None),
        ("youtube.com", "Funniest pranks of 2026"), ("x.com", None)])]
    c = where_score.score(coding, T0 + 100, cache_path=cache)
    y = where_score.score(youtube, T0 + 100, cache_path=cache)
    print(f"\nLIVE coding: rank={c['rank']} share={c['share']} top={c['top']}")
    print(f"LIVE youtube: rank={y['rank']} share={y['share']} top={y['top']}")
    assert c["rank"] is not None and y["rank"] is not None, (c["error"], y["error"])
    assert c["rank"] > y["rank"]


# ---------------------------------------------------------------- settled / reading

def _reading_after_typing(dwell_app, scroll_every=20, minutes=3, domain=None):
    """5 min typing in Code, then `minutes` on one window: no keys, a scroll
    every `scroll_every` s (input samples every 2 s)."""
    recs = steady_typing(T0, 5) + [app(T0, "Code")]
    keys = recs[-2]["counts"]["key"] if recs[-1]["kind"] == "app" else recs[-1]["counts"]["key"]
    start = T0 + 302
    recs.append(app(start, dwell_app, domain=domain))
    scroll, last_scroll = 0, start
    for i in range(int(minutes * 60 / 2)):
        ts = start + i * 2
        if (ts - start) % scroll_every == 0:
            scroll += 3
            last_scroll = ts
        recs.append(inp(ts, keys, scroll=scroll, idle=float(ts - last_scroll),
                        key_idle=float(ts - start + 2)))
    return recs, start + minutes * 60


def test_reading_one_page_is_not_a_stall():
    recs, t = _reading_after_typing("Google Chrome", domain="developer.mozilla.org")
    f = when_score.features(recs, t)
    assert f["settled"] is True
    parts = when_score.contributions(f)
    assert parts["key_drop"] == 0 and parts["scroll_no_type"] == 0 and parts["dwell_no_input_s"] == 0
    assert when_score.stall_score(f) < STALL_THRESHOLD


def test_hopping_without_typing_is_a_stall():
    recs, t = _reading_after_typing("Google Chrome", domain="developer.mozilla.org")
    # Same input, but bouncing between apps every 8 s for the last 2 minutes.
    names = ["Google Chrome", "Code", "Slack", "Messages"]
    recs += [app(t - 120 + i * 8, names[i % 4]) for i in range(15)]
    f = when_score.features(recs, t)
    assert f["settled"] is False
    assert when_score.stall_score(f) >= STALL_THRESHOLD


def test_tab_title_change_is_not_a_switch():
    recs = steady_typing(T0, 3)
    recs += [app(T0 + 10 + i, "Google Chrome", domain="mail.google.com", title=f"Inbox ({i})")
             for i in range(10)]
    f = when_score.features(recs, T0 + 100)
    assert f["dwell_s"] == 90.0          # from the first Gmail record, not the last title
    assert f["switches_per_min"] <= 1.0


def test_call_counts_as_settled_without_input():
    recs = steady_typing(T0, 3) + [app(T0, "Code"), app(T0 + 180, "zoom.us")]
    keys = recs[-3]["counts"]["key"]
    recs += [inp(T0 + 180 + i * 2, keys, idle=float(i * 2)) for i in range(1, 60)]
    f = when_score.features(recs, T0 + 180 + 119)
    assert f["settled"] is True


def test_typing_rate_ignores_idle_time():
    # Type steadily, then go fully idle for 100 s: the active-time rate stays
    # the same, so no "typing slowed" signal from the pause itself.
    recs = steady_typing(T0, 5)
    last = recs[-1]["counts"]["key"]
    recs += [inp(T0 + 300 + i * 2, last, idle=float(i * 2)) for i in range(1, 51)]
    f = when_score.features(recs, T0 + 400)
    # Only the ~10 s right after the last key counts as active-but-not-typing
    # (the old wall-clock rate gave about 0.8 here).
    assert f["key_drop"] < 0.3


def test_old_phone_pickup_leaves_history():
    from loophole.config import PHONE_HISTORY_MAX_AGE
    recs = [app(T0, "Code"), phone(T0 + 10, "TikTok"), app(T0 + 20, "Slack")]
    assert ("id", "d_tiktok") in where_score._specs(recs, T0 + 30)
    assert ("id", "d_tiktok") not in where_score._specs(recs, T0 + 11 + PHONE_HISTORY_MAX_AGE)
