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
    more = day(150, pattern_every=15)
    t = T0 + (9 * 15 - 3) * 60 + 120           # 2 min into the 9th pattern
    recs = [r for r in more if r["ts"] < t]
    m = precursor.match(recs, t)
    assert m["ready"] and m["matches"], m
    assert m["likely_app"] == "Tiktok"
    assert m["lift"] is not None and m["lift"] >= 2, m
    assert m["text"] and "Tiktok" in m["text"] and "learned from" in m["text"]


def test_no_pattern_no_text():
    # Pickups at moments that look like every other moment: lift ~1, so quiet.
    recs = day(90)
    recs += [phone(T0 + k * 600 + 77, "tiktok") for k in range(1, 9)]
    recs.sort(key=lambda r: r["ts"])
    m = precursor.match(recs, T0 + 89 * 60)
    assert m["ready"] and m["text"] is None


def test_snapshot_has_all_features():
    recs = day(30, pattern_every=10)
    eps = precursor.episodes(recs)
    assert eps and set(precursor.SNAPSHOT_FEATURES) <= set(eps[0]["features"])
    # snapshot is taken PRECURSOR_LEAD_S before the pickup: phone itself unseen
    assert eps[1]["features"]["min_since_pickup"] == round((eps[1]["ts"] - PRECURSOR_LEAD_S - eps[0]["ts"]) / 60, 1)
