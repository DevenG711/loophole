"""Tests for the evaluation. Most of them guard against cheating ("circularity"):
the label (phone pickups) and the future must never leak into the features.

Run: .venv/bin/python -m pytest tests/test_eval.py -q
No API calls are made here.
"""
import csv
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation import eval_pickups as ep  # noqa: E402
from evaluation import eval_titles as et  # noqa: E402
from evaluation import export_titles as ex  # noqa: E402
from evaluation.fake_data import simulate  # noqa: E402
from focusguard import when_score  # noqa: E402


@pytest.fixture(scope="module")
def recs():
    return simulate("fakeTest", 1.0, seed=3)


def _t_mid(recs):
    return recs[len(recs) // 2]["ts"] + 0.5


# ---------------- circularity guards ----------------

def test_features_ignore_records_at_or_after_t(recs):
    t = _t_mid(recs)
    before = [r for r in recs if r["ts"] < t]
    assert when_score.features(recs, t) == when_score.features(before, t)
    # A wild record exactly at t must not count either.
    odd = {"ts": t, "kind": "input", "idle": {"any": 999, "key": 999, "click": 999,
                                              "scroll": 999, "mouse": 999},
           "counts": {"key": 10**6, "click": 0, "scroll": 0, "mouse": 0}}
    assert when_score.features(before + [odd], t) == when_score.features(before, t)


def test_phone_records_never_feed_when(recs):
    t = _t_mid(recs)
    no_phone = [r for r in recs if r["kind"] != "phone"]
    many_phones = no_phone + [{"ts": t - k * 7.0, "kind": "phone", "app": "TikTok",
                               "source": "button"} for k in range(1, 50)]
    f1, f2 = when_score.features(no_phone, t), when_score.features(many_phones, t)
    assert f1 == f2
    assert when_score.stall_score(f1) == when_score.stall_score(f2)


def test_eval_features_do_not_change_when_phones_move(recs):
    """In the eval pipeline, moving pickups changes labels, never when-features."""
    rows_a, _, _ = ep.moments_for("p", recs)
    shifted = [dict(r, ts=r["ts"] + 400) if r["kind"] == "phone" else r for r in recs]
    shifted.sort(key=lambda r: r["ts"])
    rows_b, _, _ = ep.moments_for("p", shifted)
    assert [r["t"] for r in rows_a] == [r["t"] for r in rows_b]
    assert [r["features"] for r in rows_a] == [r["features"] for r in rows_b]


def test_active_check_uses_only_the_past():
    inputs = [(100.0, 0.0), (110.0, 0.0)]
    assert ep.active_at(inputs, 105.0)        # input at 100, idle 0 -> 5 s ago
    assert not ep.active_at(inputs, 99.0)     # nothing before t
    assert not ep.active_at([(100.0, 0.0)], 111.0)  # 11 s > ACTIVE_WITHIN
    assert not ep.active_at([(100.0, 0.0)], 100.0)  # a sample AT t is not "before t"


# ---------------- label window ----------------

@pytest.mark.parametrize("dt,expected", [
    (10, 0), (30, 0), (30.01, 1), (100, 1), (180, 1), (180.01, 0), (-5, 0)])
def test_label_window(dt, expected):
    t = 1000.0
    assert ep.label_at(t, [t + dt]) == expected


def test_catch_window_needs_30s_warning():
    p = 1000.0
    # alert 29 s before: too late. alert 31 s before: caught.
    assert ep.catch_stats([p - 29], [True], [p]) == (0.0, None)
    caught, lead = ep.catch_stats([p - 31, p - 150], [True, True], [p])
    assert caught == 1.0 and lead == 150


# ---------------- matched alert rate ----------------

def test_flag_top_matches_rate_even_with_ties():
    scores = [1.0] * 50 + [0.0] * 50          # heavy ties
    flags = ep.flag_top(scores, 0.1)
    assert flags.sum() == 10
    assert all(scores[i] == 1.0 for i in np.flatnonzero(flags))
    flags = ep.flag_top(np.arange(200), 0.05)
    assert flags.sum() == 10 and set(np.flatnonzero(flags)) == set(range(190, 200))


# ---------------- bootstrap ----------------

def test_bootstrap_ci_shape():
    rng = np.random.default_rng(1)
    x = rng.normal(5, 1, 200)
    lo, hi = et.bootstrap_ci(lambda i: float(x[i].mean()), len(x), reps=500)
    assert lo < x.mean() < hi and hi - lo < 1.0
    groups = np.repeat(np.arange(20), 10)  # cluster bootstrap
    lo2, hi2 = et.bootstrap_ci(lambda i: float(x[i].mean()), len(x), groups=groups, reps=500)
    assert lo2 < hi2
    assert et.bootstrap_ci(lambda i: None, 10, reps=50) == (None, None)


def test_auc_needs_two_classes():
    assert et.auc([1, 1, 1], [0.1, 0.2, 0.3]) is None
    assert et.auc([0, 1], [0.1, 0.9]) == 1.0


# ---------------- title rules ----------------

def test_title_rules():
    assert et.domain_rule("www.youtube.com") == 1
    assert et.domain_rule("github.com") == 0
    assert et.keyword_rule("Rust tutorial", "youtube.com") == 0
    assert et.keyword_rule("Funny cats compilation", "youtube.com") == 1
    assert et.keyword_rule("Some video", "youtube.com") == 1   # falls back to domain
    assert et.keyword_rule("Funny tutorial", "youtube.com") == 0  # on-task words win


def test_titles_csv_skips_examples_and_unlabeled(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("owner,title,domain,label\nexample,A,youtube.com,on\n"
                 "me,B,youtube.com,\nme,C,reddit.com,off\n")
    rows, skipped = et.load_rows(p, include_examples=False)
    assert [r["title"] for r in rows] == ["C"]
    assert skipped == {"unlabeled": 1, "example": 1}


# ---------------- browser export (tiny fake databases) ----------------

def test_export_titles_from_fake_chrome_and_safari(tmp_path):
    now = time.time()
    chrome = tmp_path / "History"
    con = sqlite3.connect(chrome)
    con.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url LONGVARCHAR, title LONGVARCHAR,"
                " visit_count INTEGER, typed_count INTEGER, last_visit_time INTEGER, hidden INTEGER)")
    chrome_t = lambda s: int((s + ex.CHROME_EPOCH) * 1_000_000)  # noqa: E731
    con.executemany("INSERT INTO urls (url, title, last_visit_time) VALUES (?, ?, ?)", [
        ("https://www.youtube.com/watch?v=1", "(2) Rust tutorial - YouTube", chrome_t(now - 3600)),
        ("https://www.youtube.com/watch?v=2", "Rust tutorial - YouTube", chrome_t(now - 7200)),  # dupe
        ("https://www.youtube.com/", "YouTube", chrome_t(now)),                    # home page: skip
        ("https://www.reddit.com/r/x/comments/1/a", "Cat video : r/x", chrome_t(now)),
        ("https://github.com/a/b", "Repo", chrome_t(now)),                         # other site: skip
        ("https://www.youtube.com/watch?v=3", "Old video - YouTube", chrome_t(now - 40 * 86400)),
    ])
    con.commit()
    con.close()

    safari = tmp_path / "History.db"
    con = sqlite3.connect(safari)
    con.execute("CREATE TABLE history_items (id INTEGER PRIMARY KEY, url TEXT)")
    con.execute("CREATE TABLE history_visits (id INTEGER PRIMARY KEY, history_item INTEGER,"
                " visit_time REAL, title TEXT)")
    con.execute("INSERT INTO history_items VALUES (1, 'https://youtube.com/shorts/abc')")
    con.execute("INSERT INTO history_visits VALUES (1, 1, ?, 'Prank gone wrong - YouTube')",
                (now - ex.SAFARI_EPOCH - 60,))
    con.commit()
    con.close()

    found = ex.collect([chrome], [safari], days=14)
    assert sorted(r["title"] for r in found) == ["Cat video : r/x", "Prank gone wrong", "Rust tutorial"]

    out = tmp_path / "titles.csv"
    assert ex.append_rows(out, "me", found, max_n=10, seed=0) == 3
    assert ex.append_rows(out, "me", found, max_n=10, seed=0) == 0  # dedupe against the CSV
    rows = list(csv.DictReader(out.open()))
    assert all(r["label"] == "" and r["owner"] == "me" for r in rows)


# ---------------- fake data is marked ----------------

def test_fake_data_is_marked_synthetic(recs):
    assert recs[0]["kind"] == "session" and recs[0]["synthetic"] is True
    assert any(r["kind"] == "phone" for r in recs)
