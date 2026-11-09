"""where_cache.json has several writers (app threads, eval scripts in other
processes). Saves must merge, not overwrite, and a corrupt file must be kept."""
import json
import multiprocessing as mp
import threading
from types import SimpleNamespace

from loophole import where_score

T0 = 1_700_000_000.0


def app(ts, name, domain=None):
    return {"ts": ts, "kind": "app", "app": name, "bundle": None,
            "url": None, "domain": domain, "title": None}


def ranked(*ids):
    # Equal scores can't be z-scored: no reference fetch, one API call per history.
    return SimpleNamespace(items=[SimpleNamespace(id=i, score=1.0) for i in ids])


class Client:
    def __init__(self, during=None):
        self.during, self.calls = during, 0

    def complete(self, history, **kw):
        self.calls += 1
        if self.during:                    # another writer saves while we wait on the API
            run, self.during = self.during, None
            run()
        return ranked("f_vscode", "d_tiktok")


RECS_A = [app(T0, "Code")]
RECS_B = [app(T0, "Terminal")]


def keys_for(*recs):
    return [where_score._cache_key(where_score._specs(r, T0 + 10)) for r in recs]


def test_two_writers_that_loaded_before_either_saved_both_survive(tmp_path):
    cache = tmp_path / "where_cache.json"
    cache.write_text(json.dumps({"old": ["f_vscode"]}))
    # A loads the cache, then (while its API call runs) B loads, asks and saves.
    # A saves last; before the fix it erased B's entry.
    b = lambda: where_score.score(RECS_B, T0 + 10, client=Client(), cache_path=cache)
    out = where_score.score(RECS_A, T0 + 10, client=Client(during=b), cache_path=cache)
    assert out["source"] == "api"
    stored = json.loads(cache.read_text())
    ka, kb = keys_for(RECS_A, RECS_B)
    assert ka in stored and kb in stored and "old" in stored
    # load_cache sees the merged file too.
    assert {ka, kb, "old"} <= set(where_score.load_cache(cache))
    assert not list(tmp_path.glob("*.tmp"))


def test_save_keeps_keys_written_after_our_load(tmp_path):
    cache = tmp_path / "where_cache.json"
    where_score._save_cache(cache, {"a": [1]})
    snapshot = dict(where_score.load_cache(cache))
    where_score._save_cache(cache, {"b": [2]})              # someone else
    snapshot["c"] = [3]
    where_score._save_cache(cache, {"c": snapshot["c"]})    # we save only ours
    assert json.loads(cache.read_text()) == {"a": [1], "b": [2], "c": [3]}


def test_new_entry_replaces_old_one_for_same_key(tmp_path):
    cache = tmp_path / "where_cache.json"
    where_score._save_cache(cache, {"k": ["f_vscode"], "other": [1]})
    where_score._save_cache(cache, {"k": {"f_vscode": 1.2}})   # upgrade
    assert json.loads(cache.read_text()) == {"k": {"f_vscode": 1.2}, "other": [1]}


def test_threads_saving_at_once_lose_nothing(tmp_path):
    cache = tmp_path / "where_cache.json"
    start = threading.Barrier(8)

    def worker(n):
        start.wait()
        for j in range(20):
            where_score._save_cache(cache, {f"t{n}-{j}": [n, j]})

    ts = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(json.loads(cache.read_text())) == 160
    assert not list(tmp_path.glob("*.tmp"))


def _proc_worker(path, n, count):
    from pathlib import Path
    from loophole import where_score as ws
    for j in range(count):
        ws._save_cache(Path(path), {f"p{n}-{j}": [n, j]})


def test_processes_saving_at_once_lose_nothing(tmp_path):
    cache = tmp_path / "where_cache.json"
    ctx = mp.get_context("spawn")
    ps = [ctx.Process(target=_proc_worker, args=(str(cache), n, 15)) for n in range(4)]
    for p in ps:
        p.start()
    for p in ps:
        p.join(60)
        assert p.exitcode == 0
    assert len(json.loads(cache.read_text())) == 60
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupt_file_is_backed_up_not_lost(tmp_path, capsys):
    cache = tmp_path / "where_cache.json"
    cache.write_text('{"half": ["f_vs')                    # torn write
    out = where_score.score(RECS_A, T0 + 10, client=Client(), cache_path=cache)
    assert out["source"] == "api"
    backups = list(tmp_path.glob("where_cache.json.corrupt-*"))
    assert len(backups) == 1 and backups[0].read_text() == '{"half": ["f_vs'
    assert set(json.loads(cache.read_text())) == set(keys_for(RECS_A))
    assert "unreadable" in capsys.readouterr().err


def test_non_dict_json_is_backed_up_too(tmp_path):
    cache = tmp_path / "where_cache.json"
    cache.write_text("[1, 2]")
    where_score._save_cache(cache, {"k": [1]})
    assert json.loads(cache.read_text()) == {"k": [1]}
    assert len(list(tmp_path.glob("where_cache.json.corrupt-*"))) == 1


def test_failed_write_cleans_temp_and_keeps_file(tmp_path, monkeypatch):
    cache = tmp_path / "where_cache.json"
    where_score._save_cache(cache, {"a": [1]})

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(where_score.os, "replace", boom)
    where_score.score(RECS_A, T0 + 10, client=Client(), cache_path=cache)   # must not raise
    monkeypatch.undo()
    assert json.loads(cache.read_text()) == {"a": [1]}
    assert not list(tmp_path.glob("*.tmp"))
