"""SYNTHETIC event logs, for testing the pipeline end to end. NOT real data.

Every file this writes starts with a `session` record that has
"synthetic": true, and every file name contains "fake". Never report numbers
from these files as results.

What is built in, so the evaluation has something to find:
  - "stall" pickups: typing slows down 1-4 minutes before the phone pickup,
    and sometimes the person drifts to YouTube/Reddit first.
  - "surprise" pickups: no warning at all; typing is normal right up to it.
  - "false stalls": typing slows down, but no pickup follows.
After a pickup the laptop sits idle for 30 s to 4 min.

Usage:
  python evaluation/fake_data.py --out data/events-fake.jsonl --people 2 --hours 4 --seed 1
This writes data/events-fake-fakeA.jsonl, data/events-fake-fakeB.jsonl and
dashboard/sample_events.jsonl (1 hour, 1 person).
"""
import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STEP = 2  # seconds between `input` records (config.SAMPLE_EVERY)
START_TS = 1762592400.0  # 2025-11-08 09:00 UTC; any fixed time works

# (app, bundle, url, domain, title). Browser tabs have a url; apps do not.
WORK = [
    ("Code", "com.microsoft.VSCode", None, None, None),
    ("Terminal", "com.apple.Terminal", None, None, None),
    ("Google Chrome", "com.google.Chrome", "https://github.com/team/loophole/pull/12",
     "github.com", "Add stall score by teammate - Pull Request #12"),
    ("Google Chrome", "com.google.Chrome", "https://docs.python.org/3/library/json.html",
     "docs.python.org", "json - JSON encoder and decoder"),
    ("Google Chrome", "com.google.Chrome", "https://www.youtube.com/watch?v=tut1",
     "youtube.com", "Python asyncio tutorial for beginners - YouTube"),
]
DRIFT = [
    ("Google Chrome", "com.google.Chrome", "https://www.youtube.com/watch?v=fun1",
     "youtube.com", "Try not to laugh compilation #47 - YouTube"),
    ("Google Chrome", "com.google.Chrome", "https://www.reddit.com/r/funny/comments/x1",
     "reddit.com", "My cat learned to open the fridge : r/funny"),
    ("Messages", "com.apple.MobileSMS", None, None, None),
]
PHONE_APPS = ["Instagram", "TikTok", "Messages", "YouTube", "Reddit"]


def app_record(ts, a):
    app, bundle, url, domain, title = a
    return {"ts": ts, "kind": "app", "app": app, "bundle": bundle,
            "url": url, "domain": domain, "title": title}


def simulate(person: str, hours: float, seed: int, start_ts: float = START_TS) -> list[dict]:
    """One person's records, sorted by time."""
    rng = random.Random(f"{seed}-{person}")
    end_ts = start_ts + hours * 3600
    base_rate = rng.uniform(1.5, 3.0)  # keys per second while typing normally

    # 1. Plan the phone pickups and the false stalls up front.
    pickups = []  # (ts, kind) kind = "stall" | "surprise"
    t = start_ts + rng.uniform(120, 600)
    while t < end_ts - 60:
        pickups.append((t, "stall" if rng.random() < 0.6 else "surprise"))
        t += rng.expovariate(1 / 720) + 300  # about 4 pickups per hour
    false_stalls = []
    t = start_ts + rng.uniform(300, 900)
    while t < end_ts:
        false_stalls.append(t)
        t += rng.expovariate(1 / 1500) + 300  # about 2 per hour

    # For each moment, how much slower than normal is typing? (1 = normal)
    slow_windows = []  # (start, end, factor, drift)
    for p, kind in pickups:
        if kind == "stall":
            lead = rng.uniform(60, 240)
            slow_windows.append((p - lead, p, rng.uniform(0.05, 0.3), rng.random() < 0.4))
    for s in false_stalls:
        slow_windows.append((s, s + rng.uniform(60, 180), rng.uniform(0.05, 0.3), False))
    away = [(p, p + rng.uniform(30, 240)) for p, _ in pickups]  # laptop idle on phone

    recs = [{"ts": start_ts - 1, "kind": "session", "action": "start",
             "minutes": int(hours * 60), "synthetic": True, "person": person,
             "note": "SYNTHETIC data from evaluation/fake_data.py, not a real person"}]

    counts = {"key": 0, "click": 0, "scroll": 0, "mouse": 0}
    idle = {"key": 0.0, "click": 0.0, "scroll": 0.0, "mouse": 0.0}
    cur = WORK[0]
    recs.append(app_record(start_ts, cur))
    next_switch = start_ts + rng.uniform(180, 900)
    in_drift = False
    pi = 0
    t = start_ts
    while t < end_ts:
        # phone pickups that happen in this step
        while pi < len(pickups) and pickups[pi][0] < t + STEP:
            recs.append({"ts": round(pickups[pi][0], 3), "kind": "phone",
                         "app": rng.choice(PHONE_APPS),
                         "source": rng.choice(["shortcut", "button"])})
            pi += 1

        is_away = any(a <= t < b for a, b in away)
        slow = next((w for w in slow_windows if w[0] <= t < w[1]), None)

        # app switches: drift to a fun site in some stalls, else normal work switching
        if slow and slow[3] and not in_drift and rng.random() < 0.05:
            cur, in_drift = rng.choice(DRIFT), True
            recs.append(app_record(t + 0.5, cur))
        elif not slow and in_drift and not is_away:
            cur, in_drift = rng.choice(WORK[:4]), False
            recs.append(app_record(t + 0.5, cur))
        elif t >= next_switch and not is_away:
            cur = rng.choice(WORK)
            recs.append(app_record(t + 0.5, cur))
            next_switch = t + rng.uniform(180, 900)

        # input activity for this 2-second step
        if is_away:
            rate = 0.0
        else:
            rate = base_rate * (slow[2] if slow else 1.0)
            if rng.random() < 0.08:  # normal short thinking pauses
                rate = 0.0
        new = {
            "key": _poisson(rng, rate * STEP),
            "click": _poisson(rng, 0.0 if is_away else (0.15 if not slow else 0.1) * STEP),
            "scroll": _poisson(rng, 0.0 if is_away else (0.2 if in_drift else 0.1) * STEP),
            "mouse": _poisson(rng, 0.0 if is_away else (0.5 if not slow else 0.3) * STEP),
        }
        for k, n in new.items():
            counts[k] += n
            idle[k] = rng.uniform(0, STEP) if n else idle[k] + STEP
        recs.append({"ts": round(t + STEP, 3), "kind": "input",
                     "idle": {"any": round(min(idle.values()), 2),
                              **{k: round(v, 2) for k, v in idle.items()}},
                     "counts": dict(counts)})
        t += STEP

    recs.append({"ts": end_ts + 1, "kind": "session", "action": "stop",
                 "minutes": int(hours * 60), "synthetic": True, "person": person})
    recs.sort(key=lambda r: r["ts"])
    # keep the synthetic marker as the very first line
    return recs


def add_scores(recs: list[dict]) -> list[dict]:
    """For the dashboard sample only: add `score` records every 30 s using the
    real when_score and nudge code if they exist. No API calls (where = None).

    Nudges use nudge.decide_when_where (the old stall rule; with where = None it
    needs a strong stall). The live rule (nudge.decide) fires only on pickup-
    memory text, which needs PRECURSOR_MIN_EPISODES real pickups and a lift that
    a 1-hour fake sample rarely reaches, so the sample would show no nudges.
    The memory is not simulated: these score records have no memory field."""
    try:
        from loophole import nudge, when_score
    except ImportError:
        return recs
    rule = getattr(nudge, "decide_when_where", None) or nudge.decide
    out, last_nudge = list(recs), None
    t = recs[1]["ts"] + 60
    while t < recs[-1]["ts"]:
        past = [r for r in recs if r["ts"] < t and r["kind"] != "phone"]
        f = when_score.features(past, t)
        w = when_score.stall_score(f)
        fire, _ = rule(w, None, f.get("active_recent", False), last_nudge, t)
        if fire:
            last_nudge = t
        out.append({"ts": t, "kind": "score", "when": w, "features": f,
                    "where_rank": None, "where_share": None, "top": [], "nudge": fire,
                    "synthetic": True})
        t += 30
    out.sort(key=lambda r: r["ts"])
    return out


def _poisson(rng, lam):
    """Small Poisson sampler (no numpy needed)."""
    if lam <= 0:
        return 0
    import math
    n, p, limit = 0, 1.0, math.exp(-lam)
    while True:
        p *= rng.random()
        if p < limit:
            return n
        n += 1


def write_jsonl(path: Path, recs: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/events-fake.jsonl")
    ap.add_argument("--people", type=int, default=2)
    ap.add_argument("--hours", type=float, default=4)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--no-dashboard", action="store_true", help="skip dashboard/sample_events.jsonl")
    args = ap.parse_args()

    out = Path(args.out)
    stem = out.stem if "fake" in out.stem else out.stem + "-fake"
    for i in range(args.people):
        person = "fake" + "ABCDEFGH"[i]
        path = out.with_name(f"{stem}-{person}.jsonl")
        recs = simulate(person, args.hours, args.seed)
        write_jsonl(path, recs)
        n_phone = sum(r["kind"] == "phone" for r in recs)
        print(f"wrote {path} ({len(recs)} records, {n_phone} pickups) - SYNTHETIC")

    if not args.no_dashboard:
        sample = add_scores(simulate("fakeDemo", 1, args.seed))
        path = ROOT / "dashboard" / "sample_events.jsonl"
        write_jsonl(path, sample)
        print(f"wrote {path} ({len(sample)} records) - SYNTHETIC")


if __name__ == "__main__":
    main()
