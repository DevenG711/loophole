"""Fill the where_score cache with item SCORES for past moments in a log, so
where_score.cached_signal() (gap / pull) works when replaying them.

Walks the log every --every seconds, finds the distinct app histories that
have no scored cache entry (missing, or an old plain id list), and asks
BehaviorGPT for them through where_score.score() (the app's own code).
Never writes to the event log; only the where cache.

  --max-calls 0 (default) is a dry run: print how many histories need calls.
  The first live run also spends 4 calls on the reference (plain work) vector.

Run: .venv/bin/python evaluation/backfill_where.py --events data/events-deven.jsonl --max-calls 120
"""
import argparse
import bisect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loophole import events, where_score  # noqa: E402
from loophole.config import HISTORY_MAX_AGE, PHONE_HISTORY_MAX_AGE  # noqa: E402

WINDOW = max(HISTORY_MAX_AGE, PHONE_HISTORY_MAX_AGE)


class CountingClient:
    """Wraps the real client; refuses once the call budget is spent."""
    def __init__(self, client, budget):
        self.client, self.budget, self.calls = client, budget, 0

    def complete(self, *a, **kw):
        if self.calls >= self.budget:
            raise RuntimeError("call budget spent")
        self.calls += 1
        return self.client.complete(*a, **kw)


def moments(records, every):
    """(t, records_slice) every `every` s. The slice holds what _specs needs:
    app/phone records in the window before t plus the newest app record before it."""
    hist = sorted((r for r in records if r.get("kind") in ("app", "phone")), key=lambda r: r["ts"])
    if not hist:
        return
    ts = [r["ts"] for r in hist]
    apps_idx = [i for i, r in enumerate(hist) if r["kind"] == "app"]
    t = hist[0]["ts"] + every
    end = max(r["ts"] for r in records) + 1
    while t <= end:
        hi = bisect.bisect_left(ts, t)
        lo = bisect.bisect_left(ts, t - WINDOW)
        j = bisect.bisect_left(apps_idx, lo) - 1    # newest app record before the window
        extra = [hist[apps_idx[j]]] if j >= 0 else []
        yield t, extra + hist[lo:hi]
        t += every


def needs_scores(entry) -> bool:
    return not isinstance(entry, dict)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--events", required=True)
    ap.add_argument("--every", type=float, default=30)
    ap.add_argument("--max-calls", type=int, default=0)
    ap.add_argument("--cache", default=str(where_score.CACHE_FILE))
    args = ap.parse_args(argv)
    cache_path = Path(args.cache)

    records = list(events.read(args.events))
    todo, n_moments, n_empty = {}, 0, 0   # key -> (t, slice) of first moment
    cache = where_score._load_cache(cache_path)
    for t, recs in moments(records, args.every):
        n_moments += 1
        specs = where_score._specs(recs, t)
        if not specs:
            n_empty += 1
            continue
        key = where_score._cache_key(specs)
        if needs_scores(cache.get(key)) and key not in todo:
            todo[key] = (t, recs)
    has_ref = where_score._reference(cache) is not None
    print(f"{n_moments} moments ({n_empty} with no history); "
          f"{len(todo)} distinct histories need scores; reference cached: {has_ref}")

    if args.max_calls > 0 and todo:
        raw = where_score._default_client()
        if raw is None:
            print("no API key (UNBOXAI_API_KEY): nothing fetched")
        else:
            client = CountingClient(raw, args.max_calls)
            done = 0
            for t, recs in todo.values():
                if client.calls >= client.budget:
                    break
                out = where_score.score(recs, t, client=client, cache_path=cache_path, upgrade=True)
                if out["source"] == "api":
                    done += 1
                elif out["error"]:
                    print(f"  t={t:.0f}: {out['error']}")
            print(f"fetched {done} histories with {client.calls} API calls")

    # Report coverage from the cache only.
    cache = where_score._load_cache(cache_path)
    n_gap = n_pull = n_hist = 0
    for t, recs in moments(records, args.every):
        specs = where_score._specs(recs, t)
        if not specs:
            continue
        n_hist += 1
        sig = where_score.signal_from_specs(specs, cache)
        if sig and sig["gap"] is not None:
            n_gap += 1
            n_pull += sig["pull_app"] is not None
    print(f"cached gap for {n_gap}/{n_hist} moments with history; "
          f"pull_app named at {n_pull}")


if __name__ == "__main__":
    main()
