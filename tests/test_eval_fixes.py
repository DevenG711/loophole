"""Tests for the 2026-11-08 eval fixes: pickup episodes, test pings, staged
nudges, the skip_sticky switch and --skip-pickups parsing. No API calls."""
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation import eval_pickups as ep  # noqa: E402
from evaluation import eval_titles as et  # noqa: E402
from loophole import config, where_score  # noqa: E402
from loophole.events import read  # noqa: E402,F401

T0 = 1_800_000_000.0


def phone(ts, app="Instagram", source="shortcut"):
    return {"ts": ts, "kind": "phone", "app": app, "source": source}


# ---------------- episode merging ----------------

def test_records_closer_than_merge_gap_are_one_episode():
    g = config.PICKUP_MERGE_S
    recs = [phone(T0), phone(T0 + 30), phone(T0 + g - 1),   # within g of the first: one episode
            phone(T0 + g),                                   # exactly g after the first: new
            phone(T0 + 5000)]
    eps = ep.merge_episodes(recs)
    assert [e["ts"] for e in eps] == [T0, T0 + g, T0 + 5000]
    assert [e["n"] for e in eps] == [3, 1, 1]
    assert eps[0]["end"] == T0 + g - 1


def test_labels_use_episode_start():
    recs = [phone(T0 + 100), phone(T0 + 150)]
    ts = [e["ts"] for e in ep.merge_episodes(recs)]
    assert ts == [T0 + 100]
    assert ep.label_at(T0, ts) == 1          # 100 s ahead
    assert ep.label_at(T0 - 100, ts) == 0    # 200 s ahead: outside the window


def test_test_burst_warning():
    burst = ep.merge_episodes([phone(T0, "app=snapchat"), phone(T0 + 42, "app=tiktok"),
                               phone(T0 + 44, "app=instagram")])[0]
    assert ep.looks_like_test_burst(burst)
    real = ep.merge_episodes([phone(T0, "instagram"), phone(T0 + 4, "tiktok")])[0]
    assert not ep.looks_like_test_burst(real)
    spread = ep.merge_episodes([phone(T0, "a"), phone(T0 + 50, "b"), phone(T0 + 115, "c")])[0]
    assert not ep.looks_like_test_burst(spread)  # 3 apps, but not within 60 s


# ---------------- test pings ----------------

def test_test_source_is_not_a_pickup():
    recs = [phone(T0, source="test"), phone(T0 + 500)]
    kept, dropped = ep.pickup_records(recs)
    assert [r["ts"] for r in kept] == [T0 + 500]
    assert dropped == {"dropped_source": {"test": 1}, "skipped_range": 0}


def test_test_source_is_not_where_history():
    assert where_score._spec_for_phone(phone(T0, "Instagram", source="test")) is None
    assert where_score._spec_for_phone(phone(T0, "Instagram")) == ("id", "d_instagram")
    assert where_score._spec_for_phone(phone(T0, "app=tiktok")) == ("id", "d_tiktok")


def test_moments_ignore_test_pings():
    """Adding test pings must not change labels or where history."""
    from evaluation.fake_data import simulate
    recs = simulate("fakeFix", 1.0, seed=5)
    rows_a, pick_a, wsrc_a = ep.moments_for("p", recs)
    mid = recs[len(recs) // 2]["ts"]
    noisy = sorted(recs + [phone(mid + k * 3, source="test") for k in range(5)], key=lambda r: r["ts"])
    rows_b, pick_b, wsrc_b = ep.moments_for("p", noisy)
    assert [r["y"] for r in rows_a] == [r["y"] for r in rows_b]
    assert pick_a == pick_b
    assert not any(r.get("source") == "test" for r in wsrc_b)


# ---------------- staged nudges ----------------

def test_staged_nudges_are_ignored():
    recs = [{"ts": T0, "kind": "nudge", "action": "shown"},
            {"ts": T0 + 1, "kind": "nudge", "action": "shown", "staged": True},
            {"ts": T0 + 2, "kind": "phone", "app": "x"}]
    assert [r["ts"] for r in ep.real_nudges(recs)] == [T0]


# ---------------- skip_sticky ----------------

def test_skip_sticky_param():
    ids = ["f_zoom", "d_hulu", "f_vscode", "d_spotify_pod", "d_instagram"] + ["f_slack"] * 5
    assert where_score._summarise(ids)["rank"] == 4                      # default: live, tuned
    assert where_score._summarise(ids, skip_sticky=True)["rank"] == 4
    assert where_score._summarise(ids, skip_sticky=False)["rank"] == 1   # pre-registered
    assert where_score._summarise(ids, skip_sticky=False)["share"] == 0.3


def test_score_both_ranks_from_one_cache_entry(tmp_path, monkeypatch):
    cache = tmp_path / "c.json"
    monkeypatch.setattr(ep, "CACHE", cache)
    recs = [{"ts": T0, "kind": "app", "app": "Code", "bundle": "com.microsoft.VSCode"}]
    key = where_score._cache_key(where_score._specs(recs, T0 + 1))
    cache.write_text(json.dumps({key: ["f_slack", "d_pinterest", "f_gmail", "d_reddit"] + ["f_zoom"] * 6}))
    pre, tuned, how = ep.where_ranks(recs, T0 + 1, et.BudgetClient(None, {"left": 0, "used": 0}),
                                     caches=(cache,))
    assert (pre, tuned, how) == (1, 3, "eval cache")


def test_offline_missing_cache_gives_none(tmp_path, monkeypatch):
    monkeypatch.setattr(ep, "CACHE", tmp_path / "eval.json")
    recs = [{"ts": T0, "kind": "app", "app": "Code", "bundle": "com.microsoft.VSCode"}]
    budget = {"left": 0, "used": 0}
    pre, tuned, how = ep.where_ranks(recs, T0 + 1, et.BudgetClient(None, budget),
                                     caches=(tmp_path / "none.json",))
    assert (pre, tuned, how) == (None, None, "missing") and budget["used"] == 0


# ---------------- --skip-pickups parsing ----------------

def test_parse_skip():
    assert ep.parse_skip("13:34-13:37") == (13 * 3600 + 34 * 60, 13 * 3600 + 37 * 60)
    assert ep.parse_skip("13:34:49-13:36:50") == (48889, 49010)
    for bad in ("13:37-13:34", "1334-1337", "13:34", "25:00-26:00", "13:61-14:00"):
        with pytest.raises(ValueError):
            ep.parse_skip(bad)


def test_skip_range_drops_phone_records_in_local_time():
    day = dt.datetime(2026, 11, 8)
    at = lambda h, m, s=0: day.replace(hour=h, minute=m, second=s).timestamp()  # noqa: E731
    ranges = [ep.parse_skip("13:34-13:37")]
    recs = [phone(at(13, 34, 49)), phone(at(13, 36, 49)), phone(at(13, 37, 0)), phone(at(14, 7, 43))]
    kept, dropped = ep.pickup_records(recs, ranges)
    assert [r["ts"] for r in kept] == [at(13, 37, 0), at(14, 7, 43)]
    assert dropped["skipped_range"] == 2


# ---------------- titles: no headline from EXAMPLE rows ----------------

def test_count_real_labels():
    rows = [{"owner": "example", "label": "on"}, {"owner": "deven", "label": "off"},
            {"owner": "deven", "label": ""}]
    assert et.count_real_labels(rows) == 1
    assert et.count_real_labels(rows[:1]) == 0
