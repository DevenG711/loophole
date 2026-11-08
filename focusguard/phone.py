"""Phone pickups. An iOS Shortcut posts the app name to ntfy.sh when a
distracting app opens (see docs/phone-setup.md). We poll ntfy.sh and write a
`phone` record for each new message (see events.py).

Run on its own to watch for messages:
    .venv/bin/python -m focusguard.phone
"""
import json
import threading
import time
from pathlib import Path

import requests

from . import events
from .config import EVENTS_FILE, NTFY_TOPIC, PHONE_POLL_EVERY

NTFY = "https://ntfy.sh"

# ntfy message ids we already wrote, so a message is never written twice.
_seen: set[str] = set()


def parse_line(line: str) -> dict | None:
    """One ntfy JSON line -> {"id", "time", "app"}, or None if it is not a message.

    ntfy also sends "open" and "keepalive" events; we skip those.
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
    app = (msg.get("message") or "").strip() or "Unknown"
    return {"id": msg["id"], "time": float(msg.get("time") or time.time()), "app": app}


def poll_once(since: float, topic: str = NTFY_TOPIC, path: Path = EVENTS_FILE) -> list[dict]:
    """Fetch messages since `since` (unix seconds) and write the new ones.

    Returns the phone records written. Raises on network errors; run() catches them.
    """
    if not topic:
        return []
    url = f"{NTFY}/{topic}/json?poll=1&since={int(since)}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    written = []
    for line in resp.text.splitlines():
        msg = parse_line(line)
        if msg is None or msg["id"] in _seen:
            continue
        _seen.add(msg["id"])
        written.append(events.write("phone", path, ts=msg["time"],
                                    app=msg["app"], source="shortcut"))
    return written


def record_manual(source: str = "button", app: str = "Unknown", path: Path = EVENTS_FILE) -> dict:
    """Write a phone pickup now, e.g. from the menu-bar button or a hotkey."""
    return events.write("phone", path, app=app, source=source)


def run(stop: threading.Event, topic: str = NTFY_TOPIC, path: Path = EVENTS_FILE) -> None:
    """Poll every PHONE_POLL_EVERY seconds until `stop` is set.

    Starts from "now", so old messages from before the app started are ignored.
    """
    since = time.time()
    while not stop.is_set():
        try:
            for rec in poll_once(since, topic, path):
                # Move the window forward. ntfy's `since` may include the same
                # second again; the seen-ids set stops duplicates.
                since = max(since, rec["ts"])
        except requests.RequestException as e:
            print(f"[phone] poll failed (will retry): {e}")
        stop.wait(PHONE_POLL_EVERY)


def send_test(app: str = "Test", topic: str = NTFY_TOPIC) -> bool:
    """Post a fake pickup to the topic. True if ntfy.sh accepted it."""
    if not topic:
        return False
    try:
        resp = requests.post(f"{NTFY}/{topic}", data=app.encode("utf-8"), timeout=10)
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
                for rec in poll_once(since):
                    since = max(since, rec["ts"])
                    print(f"{time.strftime('%H:%M:%S')}  phone pickup: {rec['app']}")
            except requests.RequestException as e:
                print(f"poll failed: {e}")
            time.sleep(PHONE_POLL_EVERY)
    except KeyboardInterrupt:
        pass
