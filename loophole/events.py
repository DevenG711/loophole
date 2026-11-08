"""The one data format every part reads and writes: a JSON-lines log.

Each line is one record with a `ts` (unix seconds, float) and a `kind`:

  app      {"app": "Code", "bundle": "com.microsoft.VSCode",
            "url": str|None, "domain": str|None, "title": str|None}
           Written when the front app or browser tab changes.
  input    {"idle": {"any": s, "key": s, "mouse": s, "click": s, "scroll": s},
            "counts": {"key": n, "click": n, "scroll": n, "mouse": n}}
           Every SAMPLE_EVERY seconds. Counts are running totals since boot.
           Never key contents.
  phone    {"app": "Instagram", "source": "shortcut" | "button" | "hotkey"}
           A phone pickup. This is the label, so features must never read it
           at or after the moment being scored.
  score    {"when": float, "features": {...}, "where_rank": int|None,
            "where_share": float|None, "top": [item ids], "nudge": bool}
  nudge    {"action": "shown" | "break" | "dismiss" | "phone"}
  session  {"action": "start" | "stop", "minutes": int}
"""
import json
import time
from pathlib import Path
from typing import Iterator

from .config import EVENTS_FILE


def write(kind: str, path: Path | str = EVENTS_FILE, ts: float | None = None, **fields) -> dict:
    path = Path(path)
    rec = {"ts": time.time() if ts is None else ts, "kind": kind, **fields}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def read(path: Path | str = EVENTS_FILE, kinds: set[str] | None = None,
         before: float | None = None) -> Iterator[dict]:
    """Records in file order. `before` keeps only ts < before (no peeking)."""
    path = Path(path)
    if not path.exists():
        return
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if kinds and rec["kind"] not in kinds:
                continue
            if before is not None and rec["ts"] >= before:
                continue
            yield rec
