"""Export YouTube and Reddit page titles from your own browser history into
evaluation/titles.csv, with a BLANK label for you to fill in (on|off).

Reads (read-only, from a copy, because Chrome locks its file while running):
  Chrome: ~/Library/Application Support/Google/Chrome/*/History  (every profile)
  Safari: ~/Library/Safari/History.db  (needs Full Disk Access; skipped if not readable)

Only video pages (youtube.com/watch, /shorts) and Reddit posts (/comments/) are
kept. Titles are deduped (also against rows already in the CSV), then a RANDOM
sample of --max titles is taken, so you do not pick the easy ones.

Privacy: these are your real titles. Before labeling, delete any row you do not
want to share. Never delete rows after you have seen scores.

Usage:
  python evaluation/export_titles.py --owner alice --days 14 --max 35
"""
import argparse
import csv
import random
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
CHROME_GLOB = Path.home() / "Library/Application Support/Google/Chrome"
SAFARI_DB = Path.home() / "Library/Safari/History.db"

CHROME_EPOCH = 11644473600   # seconds from 1601-01-01 to 1970-01-01
SAFARI_EPOCH = 978307200     # seconds from 1970-01-01 to 2001-01-01
JUNK_TITLES = {"", "youtube", "reddit", "reddit - dive into anything",
               "reddit - the heart of the internet"}


def site_of(url: str) -> str | None:
    """'youtube.com' or 'reddit.com' for pages we want, else None."""
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if host == "youtu.be" or host.endswith("youtube.com"):
        if u.path.startswith("/watch") or u.path.startswith("/shorts/") or host == "youtu.be":
            return "youtube.com"
    if host.endswith("reddit.com") and "/comments/" in u.path:
        return "reddit.com"
    return None


def clean_title(title: str) -> str:
    t = (title or "").strip()
    for suffix in (" - YouTube",):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    if t.startswith("(") and ") " in t[:8]:   # "(3) Video title": unread-count prefix
        t = t.split(") ", 1)[1]
    return t


def _copy(path: Path) -> Path:
    """Copy the database (and its -wal file if any) to a temp folder."""
    tmp = Path(tempfile.mkdtemp())
    shutil.copy2(path, tmp / path.name)
    wal = path.with_name(path.name + "-wal")
    if wal.exists():
        shutil.copy2(wal, tmp / wal.name)
    return tmp / path.name


def read_chrome(path: Path, since_unix: float) -> list[tuple[str, str]]:
    """(url, title) visited since `since_unix`, from one Chrome History file."""
    since = int((since_unix + CHROME_EPOCH) * 1_000_000)
    con = sqlite3.connect(f"file:{_copy(path)}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT url, title FROM urls WHERE last_visit_time >= ?", (since,)).fetchall()
    finally:
        con.close()
    return rows


def read_safari(path: Path, since_unix: float) -> list[tuple[str, str]]:
    since = since_unix - SAFARI_EPOCH
    con = sqlite3.connect(f"file:{_copy(path)}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT i.url, v.title FROM history_visits v "
            "JOIN history_items i ON i.id = v.history_item "
            "WHERE v.visit_time >= ? AND v.title IS NOT NULL", (since,)).fetchall()
    finally:
        con.close()
    return rows


def collect(chrome_files, safari_files, days) -> list[dict]:
    since = time.time() - days * 86400
    pairs = []
    for f in chrome_files:
        try:
            pairs += read_chrome(Path(f), since)
        except Exception as e:
            print(f"skip Chrome {f}: {e}")
    for f in safari_files:
        try:
            pairs += read_safari(Path(f), since)
        except Exception as e:
            print(f"skip Safari {f} (Full Disk Access needed?): {e}")
    out, seen = [], set()
    for url, title in pairs:
        site = site_of(url or "")
        t = clean_title(title)
        if not site or t.lower() in JUNK_TITLES or t.lower() in seen:
            continue
        seen.add(t.lower())
        out.append({"title": t, "domain": site})
    return out


def append_rows(csv_path: Path, owner: str, found: list[dict], max_n: int, seed: int) -> int:
    existing = set()
    if csv_path.exists():
        with csv_path.open(newline="") as f:
            existing = {r["title"].strip().lower() for r in csv.DictReader(f)}
    new = [r for r in found if r["title"].lower() not in existing]
    random.Random(seed).shuffle(new)
    new = new[:max_n]
    write_header = not csv_path.exists()
    with csv_path.open("a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["owner", "title", "domain", "label"])
        for r in new:
            w.writerow([owner, r["title"], r["domain"], ""])
    return len(new)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", required=True, help="your name; you label only your own rows")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--max", type=int, default=35, help="random sample size")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=str(HERE / "titles.csv"))
    ap.add_argument("--chrome", nargs="*", help="Chrome History files (default: all profiles)")
    ap.add_argument("--safari", nargs="*", help="Safari History.db files (default: ~/Library/Safari)")
    args = ap.parse_args()

    chrome = args.chrome if args.chrome is not None else [str(p) for p in CHROME_GLOB.glob("*/History")]
    safari = args.safari if args.safari is not None else ([str(SAFARI_DB)] if SAFARI_DB.exists() else [])
    found = collect(chrome, safari, args.days)
    n = append_rows(Path(args.out), args.owner, found, args.max, args.seed)
    print(f"Found {len(found)} distinct titles; added {n} rows for {args.owner} to {args.out}.")
    print("Next: delete rows you won't share, then fill `label` with on|off BEFORE running eval_titles.py.")


if __name__ == "__main__":
    main()
