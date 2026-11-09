"""Phone pickups. An iOS Shortcut posts the app name to ntfy.sh when a
distracting app opens (see docs/phone-setup.md). We poll ntfy.sh and write a
`phone` record for each new message (see events.py).

Run on its own to watch for messages:
    .venv/bin/python -m loophole.phone
"""
import json
import threading
import time
from pathlib import Path

import requests

from . import events
from . import config
from .config import EVENTS_FILE, NTFY_TOPIC, PHONE_POLL_EVERY

NTFY = "https://ntfy.sh"

# "Send test ping" tags its message with this, so the poller can log it with
# source="test" instead of counting it as a real pickup.
TEST_TAG = config.PHONE_TEST_TAG
# On start, fetch messages missed while the app was closed, but go back at
# most this far (ntfy.sh keeps messages about 12 h). TODO: move to config.py.
BACKFILL_MAX_S = config.PHONE_BACKFILL_MAX_S

# ntfy message ids we already wrote, so a message is never written twice.
_seen: set[str] = set()


def parse_line(line: str) -> dict | None:
    """One ntfy JSON line -> {"id", "time", "app"}, or None if it is not a message.

    ntfy also sends "open" and "keepalive" events; we skip those.
    Messages from "Send test ping" also get "test": True.
    """
    line = line.strip()
    if not line:
        return None
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return None
    if msg.get("event") != "message" or "id" not in msg:
        return None
    app = (msg.get("message") or "").strip()
    # iOS Shortcuts "Form" body arrives as "app=snapchat": keep just the value.
    if app.lower().startswith("app="):
        from urllib.parse import parse_qs
        app = (parse_qs(app).get("app") or [""])[0].strip()
    app = app or "Unknown"
    out = {"id": msg["id"], "time": float(msg.get("time") or time.time()), "app": app}
    if TEST_TAG in (msg.get("tags") or []):
        out["test"] = True
    return out


MIN_TOPIC_LEN = 20   # ntfy.sh topics have no password: the name is the key


def topic_warning(topic: str) -> str | None:
    """A warning if the topic is easy to guess, else None. Anyone who knows
    the topic can read your pickups and post fake ones (ntfy.sh has no auth
    on free topics), so it must be long and random."""
    if not topic:
        return None
    if topic == "loophole-change-me":
        return "NTFY_TOPIC is still the example value. Pick a long random one (docs/phone-setup.md)."
    if len(topic) < MIN_TOPIC_LEN:
        return (f"NTFY_TOPIC is only {len(topic)} characters. Anyone who guesses it can read "
                f"your pickups or post fake ones; use {MIN_TOPIC_LEN}+ random characters.")
    return None


def _logged_key(ntfy_id: str | None, ts: float, app: str | None) -> tuple:
    """Key for "already in the log". The ntfy message id when we have it, so
    two different messages in the same second (same app) are both kept.
    Records written before we stored the id fall back to (ts, app)."""
    return ("ntfy", ntfy_id) if ntfy_id else (ts, app)


def resume_point(path: Path = EVENTS_FILE, now: float | None = None) -> tuple[float, set]:
    """Where to start polling after a (re)start, and what is already logged.

    Returns (since, logged). `since` is the ts of the last phone record that
    came from ntfy, so pickups made while the app was closed are not lost. It
    is never older than BACKFILL_MAX_S, and is "now" if there is no such
    record. `logged` holds the keys of ntfy records already in the log (see
    _logged_key), so a message fetched again is not written twice.
    """
    now = time.time() if now is None else now
    logged = set()
    last = None
    for rec in events.read(path, kinds={"phone"}):
        if rec.get("source") in ("shortcut", "test"):
            logged.add(_logged_key(rec.get("ntfy_id"), float(rec["ts"]), rec.get("app")))
            last = rec["ts"] if last is None else max(last, rec["ts"])
    if last is None:
        return now, logged
    return max(last, now - BACKFILL_MAX_S), logged


def poll_once(since: float, topic: str = NTFY_TOPIC, path: Path = EVENTS_FILE,
              logged: set | None = None, cursor: dict | None = None) -> list[dict]:
    """Fetch messages since `since` (unix seconds) and write the new ones.

    `logged` is the set of keys already in the log (see resume_point): the
    ntfy id, or (ts, app) for old records without one. Those are skipped.
    Returns the phone records written (each with `ntfy_id`). Raises on
    network errors; run() catches them.

    If `cursor` is given, cursor["newest"] is set to the time of the newest
    ntfy message in the response, including skipped (already logged) and
    test messages, so the caller can ask only for newer ones next time. It
    is not touched if the request fails or no message came back.
    """
    if not topic:
        return []
    url = f"{NTFY}/{topic}/json?poll=1&since={int(since)}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    written = []
    newest = None
    for line in resp.text.splitlines():
        msg = parse_line(line)
        if msg is None:
            continue
        newest = msg["time"] if newest is None else max(newest, msg["time"])
        if msg["id"] in _seen:
            continue
        _seen.add(msg["id"])
        if logged is not None:
            # Skip if this id is logged, or an old id-less record has the same
            # (ts, app). Only the id is added, so a second message in the same
            # second is not mistaken for this one.
            if (_logged_key(msg["id"], msg["time"], msg["app"]) in logged
                    or (msg["time"], msg["app"]) in logged):
                continue
            logged.add(_logged_key(msg["id"], msg["time"], msg["app"]))
        source = "test" if msg.get("test") else "shortcut"
        written.append(events.write("phone", path, ts=msg["time"],
                                    app=msg["app"], source=source, ntfy_id=msg["id"]))
    if cursor is not None and newest is not None:
        cursor["newest"] = newest
    return written


def last_pickup(path: Path = EVENTS_FILE) -> dict | None:
    """The most recent phone record that is not a test ping, or None."""
    last = None
    for rec in events.read(path, kinds={"phone"}):
        if rec.get("source") == "test":
            continue
        if last is None or rec["ts"] >= last["ts"]:
            last = rec
    return last


def record_manual(source: str = "button", app: str = "Unknown", path: Path = EVENTS_FILE) -> dict:
    """Write a phone pickup now, e.g. from the menu-bar button or a hotkey."""
    return events.write("phone", path, app=app, source=source)


def run(stop: threading.Event, topic: str = NTFY_TOPIC, path: Path = EVENTS_FILE,
        on_record=None) -> None:
    """Poll every PHONE_POLL_EVERY seconds until `stop` is set.

    `on_record(rec)` is called (on this thread) for each phone record written.

    Starts from the last ntfy phone record in the log (see resume_point), so
    pickups made while the app was closed or restarting are still logged.
    """
    warning = topic_warning(topic)
    if warning:
        print(f"[phone] WARNING: {warning}")
    since, logged = resume_point(path)
    while not stop.is_set():
        try:
            cursor: dict = {}
            written = poll_once(since, topic, path, logged, cursor)
            # Move the window to the newest message seen, written or not, so
            # an already-logged catch-up window is not fetched again every
            # poll. ntfy's `since` may include the same second again; the
            # seen-ids and `logged` sets stop duplicates.
            since = max(since, cursor.get("newest", since))
            for rec in written:
                if on_record:
                    on_record(rec)
        except requests.RequestException as e:
            print(f"[phone] poll failed (will retry): {e}")
        stop.wait(PHONE_POLL_EVERY)


def send_test(app: str = "Test", topic: str = NTFY_TOPIC) -> bool:
    """Post a test message to the topic. True if ntfy.sh accepted it.

    It is tagged TEST_TAG, so the poller logs it with source="test".
    """
    if not topic:
        return False
    try:
        resp = requests.post(f"{NTFY}/{topic}", data=app.encode("utf-8"),
                             headers={"Tags": TEST_TAG}, timeout=10)
        return resp.ok
    except requests.RequestException:
        return False


if __name__ == "__main__":
    if not NTFY_TOPIC:
        raise SystemExit("Set NTFY_TOPIC in .env first.")
    print(f"Listening on {NTFY}/{NTFY_TOPIC}. Ctrl-C to stop.")
    try:
        since = time.time()
        while True:
            try:
                cursor = {}
                written = poll_once(since, cursor=cursor)
                since = max(since, cursor.get("newest", since))
                for rec in written:
                    print(f"{time.strftime('%H:%M:%S')}  phone pickup: {rec['app']}")
            except requests.RequestException as e:
                print(f"poll failed: {e}")
            time.sleep(PHONE_POLL_EVERY)
    except KeyboardInterrupt:
        pass
