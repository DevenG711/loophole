"""The one data format every part reads and writes: a JSON-lines log.

Each line is one record with a `ts` (unix seconds, float) and a `kind`:

  app      {"app": "Code", "bundle": "com.microsoft.VSCode",
            "url": str|None, "domain": str|None, "title": str|None}
           Written when the front app or browser tab changes.
  input    {"idle": {"any": s, "key": s, "mouse": s, "click": s, "scroll": s},
            "counts": {"key": n, "click": n, "scroll": n, "mouse": n}}
           Every SAMPLE_EVERY seconds. Counts are running totals since boot.
           Never key contents.
  phone    {"app": "Instagram", "source": "shortcut" | "button" | "hotkey" | "test"}
           A phone pickup. This is the label, so features must never read it
           at or after the moment being scored. source "test" is a test ping
           from the menu, not a pickup: every reader must skip it.
  score    {"when": float, "features": {...}, "where_rank": int|None,
            "where_share": float|None, "top": [item ids], "nudge": bool}
  nudge    {"action": "shown" | "break" | "dismiss" | "phone", "staged": bool?}
           staged=true: from "Demo: show nudge now", not a real nudge. Skip it
           in evals and counts.
  session  {"action": "start" | "stop", "minutes": int}
  distraction {"action": "shown" | "back" | "work", "target": "youtube.com",
               "seconds": int (on "back"/"work": time on the screen), "staged": bool?}
           The menu bar saw a distraction in front and asked the user to step
           away. "work": the user said the page is work (not a break; that
           page, or site, is not asked about again this session).
           staged=true: from "Demo: show distraction". Not a label.
"""
import json
import sys
import threading
import time
from pathlib import Path
from typing import Iterator

from .config import EVENTS_FILE

# Lines that could not be read, per file, from the last full read. A crash
# mid-write leaves a cut-off line; one bad line must not stop every reader.
bad_lines: dict[str, int] = {}
_warned: dict[str, int] = {}
_write_lock = threading.Lock()


def write(kind: str, path: Path | str = EVENTS_FILE, ts: float | None = None, **fields) -> dict:
    path = Path(path)
    rec = {"ts": time.time() if ts is None else ts, "kind": kind, **fields}
    data = (json.dumps(rec) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock, path.open("ab+") as f:
        # If the last write was cut off (no newline at the end), start a new
        # line, so the cut-off record does not swallow this one too.
        f.seek(0, 2)
        if f.tell() > 0:
            f.seek(-1, 2)
            if f.read(1) != b"\n":
                data = b"\n" + data
        f.write(data)   # one write call per record
    return rec


def read(path: Path | str = EVENTS_FILE, kinds: set[str] | None = None,
         before: float | None = None) -> Iterator[dict]:
    """Records in file order. `before` keeps only ts < before (no peeking).
    Lines that are not valid records are skipped and counted in bad_lines."""
    path = Path(path)
    if not path.exists():
        return
    bad = 0
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                rec["ts"] = float(rec["ts"])
                rec["kind"]
            except (ValueError, TypeError, KeyError, IndexError):
                bad += 1
                continue
            if kinds and rec["kind"] not in kinds:
                continue
            if before is not None and rec["ts"] >= before:
                continue
            yield rec
    key = str(path)
    bad_lines[key] = bad
    if bad and _warned.get(key) != bad:
        _warned[key] = bad
        print(f"[loophole] skipped {bad} unreadable line(s) in {path}", file=sys.stderr)
