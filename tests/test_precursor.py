"""Pickup memory (loophole/precursor.py): no peeking, test pings ignored,
pickups merged, silent until enough pickups and a real lift."""
from loophole import precursor
from loophole.config import PICKUP_MERGE_S, PRECURSOR_LEAD_S, PRECURSOR_MIN_EPISODES

T0 = 1_700_000_000.0


def inp(ts, key, scroll=0, idle=0.0):
    return {"ts": ts, "kind": "input",
            "idle": {"any": idle, "key": idle, "mouse": idle, "click": idle, "scroll": idle},
            "counts": {"key": key, "click": 0, "scroll": scroll, "mouse": 0}}


def app(ts, name, domain=None):
    return {"ts": ts, "kind": "app", "app": name, "bundle": None, "url": None,
            "domain": domain, "title": None}


def phone(ts, name, source="shortcut"):
    return {"ts": ts, "kind": "phone", "app": name, "source": source}


def day(minutes, pattern_every=None):
    """Steady typing in Code. If pattern_every (min) is set, every that many
    minutes: 3 min of no typing on one Chrome page, then a TikTok pickup.
    Otherwise the user keeps typing in Code with a switch every 2 min."""
    recs, keys = [], 0
    recs.append(app(T0, "Code"))
    for i in range(int(minutes * 30)):
        ts = T0 + i * 2
        minute = (ts - T0) / 60
        in_pattern = pattern_every and (minute % pattern_every) >= pattern_every - 3
        if not in_pattern:
            keys += 8
        recs.append(inp(ts, keys))
    if pattern_every:
        for k in range(1, int(minutes // pattern_every) + 1):
            start = T0 + (k * pattern_every - 3) * 60
            recs.append(app(start, "Google Chrome", domain="example.com"))
            recs.append(phone(start + 180, "tiktok"))
            recs.append(app(start + 181, "Code"))
    else:
        recs += [app(T0 + j * 120, "Slack" if j % 2 else "Code") for j in range(1, int(minutes // 2))]
    return sorted(recs, key=lambda r: r["ts"])


def test_episodes_merge_and_skip_test_pings():
    recs = [phone(T0, "app=tiktok"), phone(T0 + 30, "instagram"),
            phone(T0 + 30 + PICKUP_MERGE_S + 1, "youtube"), phone(T0 + 5000, "x", source="test")]
    eps = precursor._episode_list(recs, T0 + 10_000)
    assert [(e["ts"], e["app"]) for e in eps] == [(T0, "Tiktok"), (T0 + 30 + PICKUP_MERGE_S + 1, "Youtube")]


def test_no_peeking_past_t():
    recs = day(60, pattern_every=10)
    t = T0 + 35 * 60
    before = precursor.match(recs, t)
    later = recs + [phone(t + 1, "tiktok"), inp(t + 2, 10**6), app(t + 3, "Messages")]
    assert precursor.match(later, t) == before
    assert precursor.window_features(later, t) == precursor.window_features(recs, t)


def test_match_ignores_pickup_timing():
    """Phone records feed min_since_pickup for display, but similarity must
    not depend on them (pickups cluster, so it would match after any pickup)."""
    recs = day(60, pattern_every=10)
    t = T0 + 55 * 60
    log = precursor._Log(recs, t)
    vec = log.match_vector(t)
    precursor._vec_cache.clear()
    no_phone = [r for r in recs if r["kind"] != "phone"]
    assert precursor._Log(no_phone, t).match_vector(t) == vec
    assert set(precursor.MATCH_FEATURES).isdisjoint({"min_since_pickup", "pickups_1h"})


def test_silent_before_enough_pickups():
    recs = day(40, pattern_every=10)          # 4 pickups < PRECURSOR_MIN_EPISODES
    m = precursor.match(recs, T0 + 40 * 60 - 60)
    assert m["n_episodes"] < PRECURSOR_MIN_EPISODES and m["ready"] is False and m["text"] is None


def test_speaks_up_when_a_repeated_pattern_returns():
    # The same 3-min "stop typing on one page" precedes every pickup (every
    # 15 min, about the real base rate). It is happening again: the memory
    # should say so and name the app.
    # 17 earlier pickups: enough usable ones (PRECURSOR_MIN_EPISODES = 15).
    more = day(300, pattern_every=15)
    t = T0 + (18 * 15 - 3) * 60 + 120          # 2 min into the 18th pattern
    recs = [r for r in more if r["ts"] < t]
    m = precursor.match(recs, t)
    assert m["ready"] and m["matches"], m
    assert m["likely_app"] == "Tiktok"
    assert m["lift"] is not None and m["lift"] >= 2, m
    assert m["n_usable"] == m["n_episodes"] == 17
    assert m["followed_episodes"] >= 3
    assert m["text"] and "Tiktok" in m["text"] and "learned from" in m["text"]


def test_no_pattern_no_text():
    # Pickups at moments that look like every other moment: lift ~1, so quiet.
    recs = day(180)
    recs += [phone(T0 + k * 600 + 77, "tiktok") for k in range(1, 18)]
    recs.sort(key=lambda r: r["ts"])
    m = precursor.match(recs, T0 + 179 * 60)
    assert m["ready"] and m["text"] is None


def test_snapshot_has_all_features():
    recs = day(30, pattern_every=10)
    eps = precursor.episodes(recs)
    assert eps and set(precursor.SNAPSHOT_FEATURES) <= set(eps[0]["features"])
    # snapshot is taken PRECURSOR_LEAD_S before the pickup: phone itself unseen
    assert eps[1]["features"]["min_since_pickup"] == round((eps[1]["ts"] - PRECURSOR_LEAD_S - eps[0]["ts"]) / 60, 1)


# ---------------------------------------------------------------- review fixes 2026-11-08

def test_merge_does_not_chain():
    """A record joins an episode only within PICKUP_MERGE_S of its FIRST record."""
    step = PICKUP_MERGE_S * 0.8
    recs = [phone(T0 + i * step, "tiktok") for i in range(4)]       # each < merge apart
    eps = precursor._episode_list(recs, T0 + 10_000)
    assert [e["ts"] for e in eps] == [T0, T0 + 2 * step]


def test_hotkey_is_not_an_episode():
    assert "hotkey" in precursor.NOT_EPISODE_SOURCES
    recs = [phone(T0, "tiktok", source="hotkey"), phone(T0 + 1000, "x")]
    assert [e["ts"] for e in precursor._episode_list(recs, T0 + 2000)] == [T0 + 1000]


def test_rate_skips_sample_gaps():
    """A step longer than MAX_STEP_S gives neither counts nor time."""
    recs = [inp(T0, 0), inp(T0 + 2, 10), inp(T0 + 60, 500), inp(T0 + 62, 510)]
    log = precursor._Log(recs, T0 + 100)
    rate, active = log.rate(T0 - 1, T0 + 100, "key")
    assert active == 4 and rate == 20 / (4 / 60)


def test_trend_unknown_is_none_not_zero():
    recs = [app(T0, "Code")] + [inp(T0 + i * 2, i * 5) for i in range(30)]   # 1 min of typing
    t = T0 + 60
    log = precursor._Log(recs, t)
    assert log.trend(t, "key") is None
    vec = log.match_vector(t)
    assert vec["key_trend"] is None and vec["scroll_trend"] is None
    f = precursor.window_features(recs, t)
    assert f["key_trend"] is None
    assert "typing" not in precursor._describe(f)
    # missing trends + missing where_gap -> fewer than MIN_SHARED features shared
    v = precursor._scaled({**vec, "where_gap": None})
    spread = precursor._spreads([v])
    assert precursor._distance(v, v, spread) == float("inf")
    assert precursor._distance({**v, "where_gap": 0.1}, {**v, "where_gap": 0.1}, spread) == 0.0


def test_min_matched_moments_is_five():
    from loophole.config import PRECURSOR_MIN_MATCHED_MOMENTS
    assert PRECURSOR_MIN_MATCHED_MOMENTS == 5


def test_dev_testing_window_reaches_past_the_pickup_but_not_t():
    p = T0 + 20 * 60
    recs = sorted(day(40) + [app(p + 30, "Google Chrome", domain="ntfy.sh"), app(p + 90, "Code"),
                             phone(p, "tiktok")], key=lambda r: r["ts"])
    (e,) = precursor._Log(recs, T0 + 39 * 60).episodes
    assert e["dev_testing"] is True                       # testing 30 s after the pickup
    (e,) = precursor._Log(recs, p + 20).episodes
    assert e["dev_testing"] is False                      # ntfy record is at/after t: unseen
    # just before the pickup (old window ended at pickup - 60 s)
    recs2 = sorted(day(40) + [app(p - 10, "Google Chrome", domain="ntfy.sh"), phone(p, "tiktok")],
                   key=lambda r: r["ts"])
    (e,) = precursor._Log(recs2, T0 + 39 * 60).episodes
    assert e["dev_testing"] is True and e["usable"] is True


def _vec(key_trend):
    return {"dwell_s": 10.0, "key_trend": key_trend, "scroll_trend": 0.0,
            "switches_5m": 0.0, "stall": 0.0, "where_gap": None}


A, B, C = _vec(0.0), _vec(500.0), _vec(-500.0)


def test_now_does_not_match_the_pickup_that_just_happened(monkeypatch):
    recs = day(120) + [phone(T0 + k * 600 + 77, "tiktok") for k in range(1, 9)]
    t = T0 + 119 * 60
    recent = t - 120                                      # picked up 2 min ago
    recs = sorted(recs + [phone(recent, "instagram")], key=lambda r: r["ts"])

    def mv(self, x):   # now looks exactly like the snapshot before the recent pickup
        return A if abs(x - t) < 1 or abs(x - (recent - PRECURSOR_LEAD_S)) < 1 else C
    monkeypatch.setattr(precursor._Log, "match_vector", mv)
    m = precursor.match(recs, t)
    assert m["n_usable"] == 9
    assert all(x["ts"] != recent for x in m["matches"]) and m["matches"] == []


def test_lift_depends_on_the_matched_template(monkeypatch):
    g1 = [T0 + mins * 60 for mins in (20, 50, 80, 110)]     # moments like these led to pickups
    g2 = [T0 + mins * 60 for mins in (140, 170, 200, 230)]  # moments like these mostly did not
    recs = sorted(day(240) + [phone(p, "tiktok") for p in g1] + [phone(p, "instagram") for p in g2],
                  key=lambda r: r["ts"])
    t = T0 + 239 * 60
    now_vec = {}

    def mv(self, x):
        if abs(x - t) < 1:
            return now_vec["v"]
        if any(p - 90 <= x <= p - 30 for p in g1):
            return A
        if any(abs(x - (p - PRECURSOR_LEAD_S)) < 1 or p - 25 * 60 <= x <= p - 15 * 60 for p in g2):
            return B
        return C
    monkeypatch.setattr(precursor._Log, "match_vector", mv)
    now_vec["v"] = A
    ma = precursor.match(recs, t)
    now_vec["v"] = B
    mb = precursor.match(recs, t)
    assert ma["likely_app"] == "Tiktok" and mb["likely_app"] == "Instagram"
    assert ma["matched_moments"] != mb["matched_moments"]
    assert ma["lift"] is not None and mb["lift"] is not None
    assert ma["lift"] >= 2 > mb["lift"], (ma, mb)
    now_vec["v"] = C                                       # matches no template
    mc = precursor.match(recs, t)
    assert mc["matches"] == [] and mc["matched_moments"] == 0 and mc["lift"] is None
