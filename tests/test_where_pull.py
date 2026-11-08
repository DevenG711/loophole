"""BehaviorGPT "pull" signals in where_score: gap, pull_app, cache compatibility."""
import json
from types import SimpleNamespace

from loophole import where_score
from loophole.catalog import APPS
from loophole.config import WHERE_PULL_MIN_Z, WHERE_REFERENCE_HISTORIES

T0 = 1_700_000_000.0
IDS = [a[0] for a in APPS]
F_IDS = [i for i in IDS if i.startswith("f_")]
D_IDS = [i for i in IDS if i.startswith("d_")]


def app(ts, name, domain=None):
    return {"ts": ts, "kind": "app", "app": name, "bundle": None,
            "url": None, "domain": domain, "title": None}


def work_scores():
    """Plain-work ranking: focus items high, distraction items low and spread."""
    s = {i: 1.40 - 0.002 * n for n, i in enumerate(F_IDS)}
    s.update({i: 1.15 - 0.002 * n for n, i in enumerate(D_IDS)})
    return s


class FakeClient:
    """Reference histories get work_scores(); anything else gets `scores`."""
    def __init__(self, scores=None, fail=False):
        self.scores, self.fail, self.calls = scores, fail, 0

    def complete(self, history, **kw):
        self.calls += 1
        if self.fail:
            raise ConnectionError("API down")
        assert kw.get("register_event") is False
        ids = [getattr(v, "item", None) for v in history]
        ref = any(ids == h for h in WHERE_REFERENCE_HISTORIES)
        s = work_scores() if ref or self.scores is None else self.scores
        ranked = sorted(s.items(), key=lambda kv: -kv[1])
        return SimpleNamespace(items=[SimpleNamespace(id=i, score=v) for i, v in ranked])


def instagram_scores(bump):
    s = work_scores()
    s["d_instagram"] += bump
    return s


RECS = [app(T0, "Code"), app(T0 + 1, "Safari", domain="instagram.com")]


def test_zscores_and_gap_math():
    z = where_score._zscores({"f_a": 1.0, "f_b": 2.0, "d_a": 3.0, "d_b": 4.0})
    assert abs(sum(z.values())) < 1e-9
    assert abs(sum(v * v for v in z.values()) / 4 - 1) < 1e-9
    # d mean z - f mean z = (z3+z4)/2 - (z1+z2)/2 = 2 / sd(=1.118) = 1.789
    assert abs(where_score._gap(z) - 2 / 1.25 ** 0.5) < 1e-3
    assert where_score._zscores({"f_a": 1.0, "d_a": 1.0}) is None   # sd 0


def test_score_reports_gap_and_pull(tmp_path):
    cache = tmp_path / "c.json"
    client = FakeClient(instagram_scores(0.5))
    out = where_score.score(RECS, T0 + 10, client=client, cache_path=cache)
    assert out["source"] == "api" and client.calls == 1 + len(WHERE_REFERENCE_HISTORIES)
    assert out["pull_app"] == "d_instagram" and out["pull_name"] == "Instagram Reels"
    assert out["pull_z"] >= WHERE_PULL_MIN_Z
    assert out["pulls"][0][0] == "d_instagram" and len(out["pulls"]) == 3
    assert out["gap"] is not None and out["rank"] is not None
    # The reference is cached: a new history costs one call, not five.
    stored = json.loads(cache.read_text())
    assert where_score.REFERENCE_KEY in stored
    other = [app(T0, "Terminal"), app(T0 + 1, "Safari", domain="instagram.com")]
    where_score.score(other, T0 + 10, client=client, cache_path=cache)
    assert client.calls == 2 + len(WHERE_REFERENCE_HISTORIES)


def test_pull_app_none_below_threshold(tmp_path):
    client = FakeClient(instagram_scores(0.005))
    out = where_score.score(RECS, T0 + 10, client=client, cache_path=tmp_path / "c.json")
    assert out["pull_z"] is not None and out["pull_z"] < WHERE_PULL_MIN_Z
    assert out["pull_app"] is None and out["pull_name"] is None
    assert out["pulls"]   # still listed for the UI


def test_no_reference_means_pull_none(tmp_path, monkeypatch):
    monkeypatch.setattr(where_score, "_ref_failed", {})

    class NoRef(FakeClient):
        def complete(self, history, **kw):
            ids = [getattr(v, "item", None) for v in history]
            if any(ids == h for h in WHERE_REFERENCE_HISTORIES):
                raise ConnectionError("down")
            return super().complete(history, **kw)

    out = where_score.score(RECS, T0 + 10, client=NoRef(instagram_scores(0.5)),
                            cache_path=tmp_path / "c.json")
    assert out["gap"] is not None and out["rank"] is not None
    assert out["pull_app"] is None and out["pull_z"] is None and out["pulls"] == []


def test_old_list_cache_entries_still_work(tmp_path):
    cache = tmp_path / "c.json"
    specs = where_score._specs(RECS, T0 + 10)
    ids = ["f_slack", "d_hulu", "d_instagram"] + F_IDS[:7]
    cache.write_text(json.dumps({where_score._cache_key(specs): ids}))
    client = FakeClient(fail=True)
    out = where_score.score(RECS, T0 + 10, client=client, cache_path=cache)
    assert out["source"] == "cache" and client.calls == 0
    assert out["rank"] == 2 and out["top"] == ids[:3]
    assert where_score.score(RECS, T0 + 10, client=client, cache_path=cache,
                             skip_sticky=False)["rank"] == 1
    assert out["gap"] is None and out["pull_app"] is None and out["pulls"] == []
    assert where_score.cached_signal(RECS, T0 + 10, cache_path=cache) is None


def test_new_entries_keep_rank_and_share(tmp_path):
    s = instagram_scores(0.5)
    out = where_score.score(RECS, T0 + 10, client=FakeClient(s), cache_path=tmp_path / "c.json")
    ranked = [i for i, _ in sorted(s.items(), key=lambda kv: -kv[1])]
    assert out == {**out, **where_score._summarise(ranked)}


def test_cached_signal_never_calls_api(tmp_path):
    cache = tmp_path / "c.json"
    client = FakeClient(instagram_scores(0.5))
    live = where_score.score(RECS, T0 + 10, client=client, cache_path=cache)
    calls = client.calls

    def boom(*a, **k):
        raise AssertionError("cached_signal must not call the API")
    old = where_score._default_client
    where_score._default_client = boom
    try:
        sig = where_score.cached_signal(RECS, T0 + 10, cache_path=cache)
        sig2 = where_score.cached_signal(RECS, T0 + 10, cache=where_score.load_cache(cache))
        spec_sig = where_score.signal_from_specs(where_score._specs(RECS, T0 + 10),
                                                 where_score.load_cache(cache))
        unseen = where_score.cached_signal([app(T0, "Xcode")], T0 + 10, cache_path=cache)
        empty = where_score.cached_signal([], T0 + 10, cache_path=cache)
    finally:
        where_score._default_client = old
    assert client.calls == calls
    for k in ("gap", "pull_app", "pull_name", "pull_z", "pulls"):
        assert sig[k] == live[k] == sig2[k] == spec_sig[k]
    assert unseen is None and empty is None


def test_load_cache_memoised_by_mtime(tmp_path):
    cache = tmp_path / "c.json"
    assert where_score.load_cache(cache) == {}
    cache.write_text(json.dumps({"a": [1]}))
    first = where_score.load_cache(cache)
    assert where_score.load_cache(cache) is first          # not re-read
    cache.write_text(json.dumps({"a": [1], "b": [2]}))
    assert "b" in where_score.load_cache(cache)            # re-read on change


def test_api_failure_still_never_raises(tmp_path):
    out = where_score.score(RECS, T0 + 10, client=FakeClient(fail=True), cache_path=tmp_path / "c.json")
    assert out["rank"] is None and out["gap"] is None and out["pull_app"] is None
    assert out["pulls"] == [] and "API down" in out["error"]
