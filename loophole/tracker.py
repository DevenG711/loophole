"""Watches the laptop: which app is in front, which browser tab, and how busy
the keyboard and mouse are. Writes `app` and `input` records (see events.py).

We only read timers and counters from macOS. We never read what keys you press.

Run on its own to watch it work:
    .venv/bin/python -m loophole.tracker
"""
import ctypes
import ctypes.util
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse, urlsplit, urlunsplit

from . import events
from .config import EVENTS_FILE, SAMPLE_EVERY

# --- CoreGraphics input timers and counters (no permissions needed) ---------

_cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))

_seconds_since = _cg.CGEventSourceSecondsSinceLastEventType
_seconds_since.restype = ctypes.c_double
_seconds_since.argtypes = [ctypes.c_int32, ctypes.c_uint32]

_counter = _cg.CGEventSourceCounterForEventType
_counter.restype = ctypes.c_uint32
_counter.argtypes = [ctypes.c_int32, ctypes.c_uint32]

HID_STATE = 1  # the hardware-level event source: real keyboard and mouse

ANY, KEYDOWN, MOUSEMOVE, LEFTCLICK, SCROLL = 0xFFFFFFFF, 10, 5, 1, 22

# record key name -> CoreGraphics event type
IDLE_TYPES = {"any": ANY, "key": KEYDOWN, "mouse": MOUSEMOVE, "click": LEFTCLICK, "scroll": SCROLL}
COUNT_TYPES = {"key": KEYDOWN, "click": LEFTCLICK, "scroll": SCROLL, "mouse": MOUSEMOVE}


def read_input() -> dict:
    """Seconds since the last event of each type, and running counts since boot."""
    idle = {name: round(_seconds_since(HID_STATE, t), 2) for name, t in IDLE_TYPES.items()}
    counts = {name: int(_counter(HID_STATE, t)) for name, t in COUNT_TYPES.items()}
    return {"idle": idle, "counts": counts}


# --- Front app and browser tab ---------------------------------------------

# Browser bundle id -> AppleScript that prints "URL<newline>title" for the
# active tab. Chrome, Brave and Arc share Chrome's scripting dictionary.
_CHROME_LIKE = (
    'tell application id "{bid}"\n'
    '  set t to active tab of front window\n'
    '  return (URL of t) & linefeed & (title of t)\n'
    'end tell'
)
_SAFARI = (
    'tell application id "com.apple.Safari"\n'
    '  set t to current tab of front window\n'
    '  return (URL of t) & linefeed & (name of t)\n'
    'end tell'
)
# Chrome and Brave can say whether the window is incognito ("mode"). The
# third line is the mode; a private tab is never recorded (see browser_tab).
# Safari and Arc have no such property, so private windows there are a gap.
_CHROME_MODE = (
    'tell application id "{bid}"\n'
    '  set m to "normal"\n'
    '  try\n'
    '    set m to mode of front window\n'
    '  end try\n'
    '  set t to active tab of front window\n'
    '  return (URL of t) & linefeed & (title of t) & linefeed & m\n'
    'end tell'
)
MODE_AWARE = {"com.google.Chrome", "com.brave.Browser"}
BROWSERS = {
    "com.google.Chrome": _CHROME_MODE.format(bid="com.google.Chrome"),
    "com.brave.Browser": _CHROME_MODE.format(bid="com.brave.Browser"),
    "company.thebrowser.Browser": _CHROME_LIKE.format(bid="company.thebrowser.Browser"),  # Arc
    "com.apple.Safari": _SAFARI,
}


def front_app() -> tuple[str | None, str | None]:
    """(name, bundle id) of the app in front, or (None, None) if unknown."""
    from AppKit import NSWorkspace  # imported here so tests do not need AppKit

    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None, None
    return app.localizedName(), app.bundleIdentifier()


def domain_of(url: str | None) -> str | None:
    """'https://www.youtube.com/watch?v=1' -> 'youtube.com'. None if there is no host."""
    if not url:
        return None
    host = urlparse(url).hostname  # lower-case, no port, no user info
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host


def clean_url(url: str | None) -> str | None:
    """Drop the query string and fragment: they can hold search terms, tokens
    or email ids, and nothing reads them (domain and path are enough)."""
    if not url:
        return url
    parts = urlsplit(url)
    if not parts.scheme:
        return url.split("?", 1)[0].split("#", 1)[0]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def parse_mode_output(out: str) -> tuple[str | None, str | None, bool]:
    """'URL\ntitle\nmode' -> (url, title, private)."""
    body, _, mode = out.strip("\n").rpartition("\n")
    url, title = parse_tab_output(body)
    return url, title, mode.strip().lower() == "incognito"


def parse_tab_output(out: str) -> tuple[str | None, str | None]:
    """Split the AppleScript output 'URL\\ntitle' into (url, title)."""
    url, _, title = out.strip("\n").partition("\n")
    return (url.strip() or None), (title.strip() or None)


def browser_tab(bundle: str | None) -> tuple[str | None, str | None]:
    """(url, title) of the active tab, or (None, None) if we cannot read it.

    Fails quietly: no window open, Automation permission denied, or timeout.
    """
    script = BROWSERS.get(bundle or "")
    if script is None:
        return None, None
    try:
        res = subprocess.run(["osascript", "-e", script],
                             capture_output=True, text=True, timeout=2)
    except (subprocess.TimeoutExpired, OSError):
        return None, None
    if res.returncode != 0:
        return None, None
    if bundle in MODE_AWARE:
        url, title, private = parse_mode_output(res.stdout)
        return (None, None) if private else (url, title)
    return parse_tab_output(res.stdout)


# --- The tracker ------------------------------------------------------------

class Tracker:
    def __init__(self, path: Path = EVENTS_FILE, read_browser: bool = True):
        self.path = path
        self.read_browser = read_browser  # False = never call osascript
        self.last_key = None              # (app, url, title) from the last tick
        self.last_app = None              # last app record written (for printing)
        self.last_input = None            # last input record written

    def app_record(self, name, bundle, url, title) -> dict | None:
        """Write an `app` record if (app, url, title) changed. Returns it, or None."""
        url = clean_url(url)
        key = (name, url, title)
        if key == self.last_key:
            return None
        self.last_key = key
        self.last_app = events.write("app", self.path, app=name, bundle=bundle,
                                     url=url, domain=domain_of(url), title=title)
        return self.last_app

    def sample(self) -> None:
        """One tick: front app, browser tab if any, input timers and counts."""
        name, bundle = front_app()
        url = title = None
        if self.read_browser and bundle in BROWSERS:
            url, title = browser_tab(bundle)
        self.app_record(name, bundle, url, title)
        self.last_input = events.write("input", self.path, **read_input())

    def run(self, stop: threading.Event, on_tick=None) -> None:
        """Sample every SAMPLE_EVERY seconds until `stop` is set."""
        while not stop.is_set():
            try:
                self.sample()
                if on_tick:
                    on_tick(self)
            except Exception as e:  # one bad tick should not kill the thread
                print(f"[tracker] sample failed: {e}")
            stop.wait(SAMPLE_EVERY)


def _print_tick(t: Tracker) -> None:
    rec = t.last_input or {}
    idle, counts = rec.get("idle", {}), rec.get("counts", {})
    app = t.last_app or {}
    where = app.get("domain") or app.get("app")
    print(f"{time.strftime('%H:%M:%S')}  {where!s:<24.24} "
          f"idle={idle.get('any', 0):6.1f}s  "
          f"keys={counts.get('key')} clicks={counts.get('click')} "
          f"scroll={counts.get('scroll')} mouse={counts.get('mouse')}")


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Run the Loophole tracker loop.")
    p.add_argument("--no-browser", action="store_true", help="do not read browser tabs")
    p.add_argument("--out", type=Path, default=EVENTS_FILE, help="events file to write")
    p.add_argument("--once", action="store_true", help="take one sample and exit")
    args = p.parse_args()

    tracker = Tracker(path=args.out, read_browser=not args.no_browser)
    if args.once:
        tracker.sample()
        _print_tick(tracker)
    else:
        print(f"Writing to {args.out}. Ctrl-C to stop.")
        stop = threading.Event()
        try:
            tracker.run(stop, on_tick=_print_tick)
        except KeyboardInterrupt:
            stop.set()
