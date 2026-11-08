"""Tests for the pickup-memory eval and dashboard helpers. No API calls.
precursor itself is replaced by a fake here, so these tests check only our
plumbing: no peeking at records >= t, filtered pickups, nudge grouping."""
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation import eval_pickups as ep  # noqa: E402

T0 = 1_800_000_000.0


def load_dashboard():
    spec = importlib.util.spec_from_file_location("lh_dashboard_mem", ROOT / "dashboard" / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dash = load_dashboard()


# ---- eval: nudge grouping ---------------------------------------------------

def test_shown_nudge_times_skips_staged_and_other_actions():
    recs = [{"ts": T0 + 5, "kind": "nudge", "action": "shown"},
            {"ts": T0 + 1, "kind": "nudge", "action": "shown", "staged": True},
            {"ts": T0 + 2, "kind": "nudge", "action": "dismiss"},
            {"ts": T0 + 3, "kind": "app", "app": "Code"}]
    assert ep.shown_nudge_times(recs) == [T0 + 5]


def test_after_nudge_window_and_no_lookahead():
    n = [T0]
    assert not ep.after_nudge(T0, n)              # nudge at t is not before t
    assert ep.after_nudge(T0 + 1, n)
    assert ep.after_nudge(T0 + ep.LEAD_MAX, n)    # edge of the 3-min window
    assert not ep.after_nudge(T0 + ep.LEAD_MAX + 1, n)
    assert not ep.after_nudge(T0 - 10, n)         # a later nudge never counts
    assert not ep.after_nudge(T0, [])


# ---- eval: what the memory may see -----------------------------------------

def _recs():
    return [{"ts": T0, "kind": "app", "app": "Code"},
            {"ts": T0 + 10, "kind": "phone", "app": "TikTok"},
            {"ts": T0 + 20, "kind": "phone", "app": "x", "source": "test"},
            {"ts": T0 + 30, "kind": "input", "idle": {"any": 0}}]


def test_memory_records_drop_unkept_phones():
    recs = _recs()
    kept, _ = ep.pickup_records(recs, skip=[])
    mem = ep.memory_records(recs, kept)
    phones = [r for r in mem if r["kind"] == "phone"]
    assert [r["app"] for r in phones] == ["TikTok"]          # test ping gone
    assert [r["ts"] for r in mem] == sorted(r["ts"] for r in mem)
    mem2 = ep.memory_records(recs, [])                       # e.g. all skipped
    assert not any(r["kind"] == "phone" for r in mem2)
    assert len(mem2) == 2


def test_add_memory_only_sees_records_before_t(monkeypatch):
    seen = []

    def match(records, t):
        seen.append((t, [r["ts"] for r in records]))
        return {"score": float(len(records)), "ready": False, "n_episodes": 0}

    fake = types.ModuleType("loophole.precursor")
    fake.match = match
    import loophole
    monkeypatch.setitem(sys.modules, "loophole.precursor", fake)
    monkeypatch.setattr(loophole, "precursor", fake, raising=False)

    mem = {"p": ep.memory_records(_recs(), [])}
    rows = [{"person": "p", "t": T0 + 30}, {"person": "p", "t": T0 + 31}]
    assert ep.add_memory(rows, mem) is None
    for t, ts in seen:
        assert all(x < t for x in ts)
    assert rows[0]["memory"] == 1.0   # the input at exactly t is not seen
    assert rows[1]["memory"] == 2.0


def test_add_memory_import_failure_is_reported(monkeypatch):
    import loophole
    monkeypatch.setitem(sys.modules, "loophole.precursor", None)  # import raises
    monkeypatch.delattr(loophole, "precursor", raising=False)
    rows = [{"person": "p", "t": T0}]
    err = ep.add_memory(rows, {"p": []})
    assert err and "precursor" in err
    assert rows[0]["memory"] is None


def test_memory_row_does_not_change_matched_rate():
    def row(i, y, mem):
        return {"person": "p", "t": T0 + 30 * i, "y": y, "idle_s": 0.0, "since_pickup": 60.0 * i,
                "stall": 0.1 * i, "rank_prereg": None, "rank_tuned": None, "memory": mem,
                "active_prereg": True, "active_recent": True, "features": {}}
    base = [row(i, i % 5 == 0, None) for i in range(40)]
    with_mem = [dict(r, memory=float(i)) for i, r in enumerate(base)]
    res0, rate0, src0 = ep.run_methods(base, {"p": []})
    res1, rate1, src1 = ep.run_methods(with_mem, {"p": []})
    assert (rate0, src0) == (rate1, src1)
    names0 = [r["method"] for r in res0]
    names1 = [r["method"] for r in res1]
    assert ep.MEMORY_ROW in names1
    assert [n for n in names1 if n != ep.MEMORY_ROW] == [n for n in names0 if n != ep.MEMORY_ROW]
    mem = next(r for r in res1 if r["method"] == ep.MEMORY_ROW)
    assert not mem.get("flipped")   # only baselines get flipped


# ---- dashboard --------------------------------------------------------------

def test_describe_snapshot_readable():
    s = dash.describe_snapshot({"dwell_s": 180, "key_trend": 20, "switches_5m": 3,
                                "min_since_pickup": 8.2, "scroll_trend": 0})
    assert s == "on one window 3 min, typing +20/min vs before, 3 switches, last pickup 8 min ago"


def test_describe_snapshot_missing_and_bad_values():
    assert dash.describe_snapshot(None) == "no earlier pickup"
    s = dash.describe_snapshot({"dwell_s": float("nan"), "key_trend": "x", "switches_5m": 1,
                                "idle_s": 45, "stall": 2.0})
    assert "1 switch," in s and "idle 45 s" in s and "(high)" in s
    assert "window" not in s and "typing" not in s


def test_precursor_rows_excludes_test_pings(monkeypatch):
    got = {}

    def episodes(records, before):
        got["records"] = records
        got["before"] = before
        return [{"ts": T0 + 10, "app": "app=tiktok", "features": {"dwell_s": 60}}]

    fake = types.SimpleNamespace(episodes=episodes, SNAPSHOT_FEATURES=["dwell_s"])
    monkeypatch.setattr(dash, "_precursor", fake)
    rows = dash.precursor_rows(_recs())
    assert not any(r.get("source") == "test" for r in got["records"])
    assert got["before"] > max(r["ts"] for r in _recs())
    assert rows[0]["app"] == "tiktok" and rows[0]["dwell_s"] == 60
    assert rows[0]["before"].startswith("on one window 1 min")


def test_precursor_rows_without_module(monkeypatch):
    monkeypatch.setattr(dash, "_precursor", None)
    assert dash.precursor_rows(_recs()) == []


# ---- eval: live (memory) rule, old rule, where_gap row ----------------------

def _row(**kw):
    r = {"person": "p", "t": T0, "stall": 0.0, "rank_tuned": 50, "active_recent": True,
         "memory_text": None, "memory_ready": True, "memory_n_episodes": 6}
    r.update(kw)
    return r


def test_memory_rule_fires_only_on_text_and_when_active():
    assert ep.nudge_memory(_row(memory_text="This looks like 14:02 ..."))
    assert not ep.nudge_memory(_row())
    assert not ep.nudge_memory(_row(memory_text="x", active_recent=False))
    # a big stall and a top-ranked distraction no longer fire on their own
    assert not ep.nudge_memory(_row(stall=9.0, rank_tuned=0))
    assert ep.nudge_tuned is ep.nudge_memory


def test_old_rule_row_uses_decide_when_where():
    from loophole import config
    assert ep.nudge_old(_row(stall=config.STALL_THRESHOLD + 1, rank_tuned=0))
    assert not ep.nudge_old(_row(stall=0.0, rank_tuned=0, memory_text="x"))


def test_add_where_gap_cache_only_and_no_lookahead(monkeypatch):
    seen = []

    def cached_signal(records, t, cache=None):
        seen.append((t, [r["ts"] for r in records], cache))
        return {"gap": 0.5} if t > T0 + 15 else None

    monkeypatch.setattr(ep.where_score, "cached_signal", cached_signal)
    monkeypatch.setattr(ep.where_score, "load_cache", lambda path=None: {"k": 1})
    src = [{"ts": T0, "kind": "app"}, {"ts": T0 + 10, "kind": "phone"}, {"ts": T0 + 20, "kind": "app"}]
    rows = [{"person": "p", "t": T0 + 10, "where_n": 1}, {"person": "p", "t": T0 + 20, "where_n": 2}]
    n, err = ep.add_where_gap(rows, {"p": src}, caches=["only-one"])
    assert err is None and n == 1
    assert rows[0]["where_gap"] is None and rows[1]["where_gap"] == 0.5
    for t, ts, cache in seen:
        assert all(x < t for x in ts) and cache == {"k": 1}


def test_add_where_gap_without_function(monkeypatch):
    monkeypatch.delattr(ep.where_score, "cached_signal", raising=False)
    rows = [{"person": "p", "t": T0, "where_n": 0}]
    n, err = ep.add_where_gap(rows, {"p": []})
    assert n == 0 and err and rows[0]["where_gap"] is None


def test_run_methods_has_all_rule_rows_and_gap_row():
    def row(i):
        return {"person": "p", "t": T0 + 30 * i, "y": int(i % 5 == 0), "idle_s": 0.0,
                "since_pickup": 60.0 * i, "stall": 0.1 * i, "rank_prereg": 5, "rank_tuned": 5,
                "memory": 0.1 * i, "memory_text": "x" if i % 7 == 0 else None,
                "memory_ready": True, "memory_n_episodes": 6, "where_gap": 0.01 * i,
                "active_prereg": True, "active_recent": True, "features": {}}
    res, _, _ = ep.run_methods([row(i) for i in range(40)], {"p": []})
    names = [r["method"] for r in res]
    for n in (ep.MEMORY_ROW, ep.GAP_ROW, ep.OLD_RULE_ROW, ep.MEMORY_RULE_ROW):
        assert n in names
    assert any(n.startswith("nudge rule [pre-registered") for n in names)
    live = next(r for r in res if r["method"] == ep.MEMORY_RULE_ROW)
    assert live["alert_rate"] == sum(1 for i in range(40) if i % 7 == 0) / 40
