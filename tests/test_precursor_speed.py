"""Pickup memory speed: past moments are not recomputed on every tick, even
when BehaviorGPT has no cached answer for them yet, and where_gap still
shows up once the BehaviorGPT cache gains the entry."""
from loophole import precursor, when_score, where_score
from tests.test_precursor import T0, app, day


def _setup(monkeypatch):
    precursor._vec_cache.clear()
    calls = {"features": 0}
    real = when_score.features

    def counting(*a, **k):
        calls["features"] += 1
        return real(*a, **k)
    monkeypatch.setattr(when_score, "features", counting)
    bgpt = {}   # tuple(specs) -> signal: a fake BehaviorGPT cache
    monkeypatch.setattr(where_score, "signal_from_specs",
                        lambda specs, cache: bgpt.get(tuple(specs)) if specs else None)
    return calls, bgpt


def test_moment_without_where_gap_is_not_recomputed(monkeypatch):
    calls, bgpt = _setup(monkeypatch)
    recs = day(30) + [app(T0 + 25 * 60, "Google Chrome", domain="example.com")]
    recs.sort(key=lambda r: r["ts"])
    t = T0 + 20 * 60

    v1 = precursor._Log(recs, t).match_vector(t)
    assert v1["where_gap"] is None
    first = calls["features"]
    assert first >= 1                           # stall computed once

    v2 = precursor._Log(recs, t).match_vector(t)
    assert calls["features"] == first           # second call: from the cache
    assert v2 == v1

    # BehaviorGPT answers later: where_gap appears, other features unchanged.
    log = precursor._Log(recs, t)
    specs = where_score._specs(log.moves, t)
    assert specs
    bgpt[tuple(specs)] = {"gap": -0.75}
    v3 = log.match_vector(t)
    assert calls["features"] == first
    assert v3 == {**v1, "where_gap": -0.75}


def test_returned_vector_is_a_copy(monkeypatch):
    _setup(monkeypatch)
    recs = day(30)
    t = T0 + 20 * 60
    v1 = precursor._Log(recs, t).match_vector(t)
    saved = dict(v1)
    v1["stall"] = 999.0
    v1["dwell_s"] = -1.0
    assert precursor._Log(recs, t).match_vector(t) == saved


def test_match_tick_reuses_past_moments(monkeypatch):
    """A second tick 30 s later only computes stall for the few new moments."""
    calls, _ = _setup(monkeypatch)
    recs = day(60, pattern_every=10)
    t = T0 + 55 * 60
    a = precursor.match(recs, t)
    cold = calls["features"]
    precursor.match(recs, t + 30)
    assert calls["features"] - cold <= 3, (cold, calls["features"])
    precursor._vec_cache.clear()
    assert precursor.match(recs, t) == a        # cache gives the same answer
