"""Loophole menu-bar app (rumps).

What it does:
- Shows a dot in the macOS menu bar. Grey = no session. During a session the
  dot is green, amber or red, from the engine's latest score.
- "Start focus session" runs three background threads: the tracker (app and
  input sampling), the phone listener (ntfy.sh) and the engine (scores every
  30 s). They all share one `threading.Event` that we set to stop them.
- The engine calls `on_score` from ITS thread. rumps UI is not thread-safe, so
  `on_score` only stores the score. A 1-second rumps.Timer on the main thread
  picks it up and updates the dot and the menu.
- When a nudge fires (at most once per cooldown), we send a notification and
  show three action items in the menu: Start break, I'm fine, I grabbed my phone.
- During a break the menu-bar title is a countdown. At the end we ask
  "Back to <work app>?" and bring that app to the front with `open -a`.

Run:  .venv/bin/python -m loophole.menubar [--demo]

The helpers at the top of this file are plain functions with no rumps import,
so tests can use them without a GUI.
"""
from __future__ import annotations

import argparse
import subprocess
import threading
import time
from pathlib import Path

from . import catalog, config, events

# ---------------------------------------------------------------------------
# Settings for normal and demo mode
# ---------------------------------------------------------------------------

SESSION_LENGTHS = (25, 50, 90)  # minutes offered in the menu

# Demo mode: the stage demo can't wait 20 minutes for a second nudge.
DEMO_COOLDOWN = 60          # seconds between nudges in demo mode
DEMO_STALL_THRESHOLD = 0.3  # lower "when" threshold (normal: config.STALL_THRESHOLD)
DEMO_RANK_THRESHOLD = 25    # looser "where" threshold (normal: config.RANK_THRESHOLD)

DOTS = {"green": "🟢", "amber": "🟡", "red": "🔴"}
IDLE_DOT = "⚪"
BREAK_ICON = "☕"

# Sites and apps that count as distractions. Used to find the last
# *work* app for "Back to work". Keep in sync with dashboard/app.py.
DISTRACTION_DOMAINS = (
    "youtube.com", "tiktok.com", "instagram.com", "twitter.com", "x.com",
    "reddit.com", "old.reddit.com", "facebook.com", "netflix.com", "twitch.tv", "hulu.com",
    "open.spotify.com", "store.steampowered.com", "discord.com", "amazon.com",
    "espn.com", "buzzfeed.com", "9gag.com", "pinterest.com", "snapchat.com",
    "cnn.com", "web.whatsapp.com", "linkedin.com", "chess.com", "nytimes.com",
    "ebay.com", "zillow.com", "robinhood.com", "primevideo.com",
    "news.ycombinator.com",
)
DISTRACTION_APPS = ("messages", "whatsapp", "discord", "steam", "spotify",
                    "netflix", "tv", "music", "photo booth", "chess")
# Apps that are neither work nor distraction; never "go back" to these.
NEUTRAL_APPS = {
    "finder", "loginwindow", "python", "python3", "loophole",
    "screensaverengine", "usernotificationcenter", "system settings",
    "notification center", "dock", "spotlight",
}

SPARK = "▁▂▃▄▅▆▇█"


# ---------------------------------------------------------------------------
# Pure helpers (no GUI). Tested in tests/test_ui.py.
# ---------------------------------------------------------------------------

def format_minutes(seconds: float) -> str:
    """12.3 minutes -> '12 min'; 75 minutes -> '1 h 15 min'."""
    minutes = int(max(0, seconds) // 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min"


def format_countdown(seconds: float) -> str:
    """299 -> '4:59'. Never negative."""
    seconds = int(max(0, round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


def app_kind(rec: dict) -> str:
    """Classify an `app` record as 'work', 'distraction' or 'neutral'."""
    app = (rec.get("app") or "").lower()
    if app in NEUTRAL_APPS:
        return "neutral"
    # Exact match (so console.aws.amazon.com is not "amazon.com")
    domain = (rec.get("domain") or "").lower().removeprefix("www.").removeprefix("m.")
    if domain in DISTRACTION_DOMAINS:
        return "distraction"
    if app in DISTRACTION_APPS:
        return "distraction"
    return "work"


def last_work_app(app_records: list[dict]) -> str | None:
    """Name of the most recent app the user was working in (for `open -a`)."""
    for rec in reversed(app_records):
        if rec.get("app") and app_kind(rec) == "work":
            return rec["app"]
    return None


def colour_for(score: dict | None, stall: float = config.STALL_THRESHOLD,
               rank: int = config.RANK_THRESHOLD) -> str:
    """Dot colour. Uses the engine's colour if it sent a valid one.

    Fallback: red if it wants a nudge, amber if either signal is half-way
    there, otherwise green.
    """
    if not score:
        return "green"
    if score.get("colour") in DOTS:
        return score["colour"]
    if score.get("nudge"):
        return "red"
    when = score.get("when") or 0.0
    where_rank = score.get("where_rank")
    if when >= stall / 2 or (where_rank is not None and where_rank <= rank * 2):
        return "amber"
    return "green"


def wants_nudge(score: dict | None, demo: bool = False) -> bool:
    """Does this score ask for a nudge? Demo mode uses looser thresholds."""
    if not score:
        return False
    if score.get("nudge"):
        return True
    if demo:
        when = score.get("when") or 0.0
        where_rank = score.get("where_rank")
        return when >= DEMO_STALL_THRESHOLD and where_rank is not None \
            and where_rank <= DEMO_RANK_THRESHOLD
    return False


def cooldown_over(last_nudge_ts: float | None, now: float, cooldown: float) -> bool:
    return last_nudge_ts is None or now - last_nudge_ts >= cooldown


def activity_per_minute(input_records: list[dict], now: float, minutes: int = 10) -> list[int]:
    """Keys + clicks in each of the last `minutes` minutes (oldest first).

    Input records hold running totals, so we add up the differences between
    neighbouring records. A negative difference (reboot) counts as 0.
    """
    buckets = [0] * minutes
    prev = None
    for rec in input_records:
        counts = rec.get("counts") or {}
        total = int(counts.get("key", 0)) + int(counts.get("click", 0))
        if prev is not None:
            age = now - rec["ts"]
            if 0 <= age < minutes * 60:
                buckets[minutes - 1 - int(age // 60)] += max(0, total - prev)
        prev = total
    return buckets


def sparkline(values: list[float]) -> str:
    """[0, 5, 10] -> '▁▅█'."""
    if not values:
        return ""
    top = max(values)
    if top <= 0:
        return SPARK[0] * len(values)
    return "".join(SPARK[min(len(SPARK) - 1, int(v / top * (len(SPARK) - 1)))] for v in values)


def momentum_text(buckets: list[int]) -> str:
    """One menu line: sparkline + last 2 minutes vs the 10-minute average."""
    if not buckets or sum(buckets) == 0:
        return "Momentum: no typing yet"
    baseline = sum(buckets) / len(buckets)
    recent = sum(buckets[-2:]) / min(2, len(buckets))
    pct = round(100 * recent / baseline)
    if pct >= 120:
        word = "picking up"
    elif pct >= 80:
        word = "steady"
    elif pct >= 50:
        word = "slowing"
    else:
        word = "stalled"
    return f"Momentum {sparkline(buckets)}  {word} ({pct}% of your last 10 min)"


def friendly_names(top_ids: list[str], explain=None, limit: int = 2) -> list[str]:
    """Distraction item ids -> short names, e.g. ['X', 'YouTube'].

    Uses where_score.explain() if given, else the catalog brand name.
    """
    ids = [i for i in (top_ids or []) if catalog.is_distraction(i)][:limit]
    if not ids:
        return []
    if explain is not None:
        try:
            names = list(explain(ids))
            if names:
                return names[:limit]
        except Exception:
            pass
    return [catalog.ROW[i][1] if i in catalog.ROW else i for i in ids]


def nudge_message(likely: list[str]) -> str:
    if likely:
        return (f"You've slowed down, and {likely[0]} looks like your next move. "
                f"Take a {config.BREAK_MINUTES}-minute break instead?")
    return f"You've slowed down. Take a {config.BREAK_MINUTES}-minute break instead?"


def last_break_text(last_break_ts: float | None, now: float) -> str:
    if last_break_ts is None:
        return "Last break: none yet"
    return f"Last break: {format_minutes(now - last_break_ts)} ago"


# ---------------------------------------------------------------------------
# Small side-effect helpers
# ---------------------------------------------------------------------------

def notify(title: str, subtitle: str, message: str) -> None:
    """macOS notification. rumps needs an Info.plist next to the Python binary
    (see docs/run.md); if that is missing we fall back to osascript."""
    try:
        import rumps
        rumps.notification(title, subtitle, message)
        return
    except Exception as e:
        print(f"[loophole] rumps notification failed ({e!s:.60}); using osascript")
    script = (f'display notification {_osa(message)} with title {_osa(title)} '
              f'subtitle {_osa(subtitle)}')
    subprocess.run(["osascript", "-e", script], check=False)


def _osa(text: str) -> str:
    """Quote a string for AppleScript."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def bring_to_front(app_name: str) -> None:
    subprocess.run(["open", "-a", app_name], check=False)


def _load(module: str):
    """Import a sibling module, or return None if it is missing/broken.

    The tracker, phone and engine modules are built by teammates; the menu bar
    should still open if one of them isn't ready.
    """
    try:
        return __import__(f"loophole.{module}", fromlist=[module])
    except Exception as e:
        print(f"[loophole] {module} unavailable: {e}")
        return None


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------

def build_app(demo: bool = False, events_path: Path = config.EVENTS_FILE):
    """Create the rumps App. rumps is imported here so tests can import this
    file without a GUI."""
    import rumps

    class LoopholeApp(rumps.App):
        def __init__(self):
            super().__init__("Loophole", title=IDLE_DOT, quit_button="Quit Loophole")
            self.demo = demo
            self.path = events_path
            self.cooldown = DEMO_COOLDOWN if demo else config.NUDGE_COOLDOWN

            # Session state (main thread only, except _latest which uses a lock)
            self.stop_event: threading.Event | None = None
            self.session_start: float | None = None
            self.session_minutes = 0
            self.break_end: float | None = None
            self.break_seconds = 0.0       # total break time this session
            self.last_break: float | None = None
            self.last_nudge: float | None = None
            self.nudge_active = False
            self.work_app: str | None = None
            self.score: dict | None = None
            self.view: dict = {}            # momentum + likely names
            self._latest: tuple[dict, dict] | None = None
            self._lock = threading.Lock()

            # Info lines (no callback = greyed out, read-only)
            self.i_focused = rumps.MenuItem("Not in a session")
            self.i_momentum = rumps.MenuItem("Momentum: -")
            self.i_likely = rumps.MenuItem("Likely next: -")
            self.i_break = rumps.MenuItem("Last break: none yet")
            self.i_reason = rumps.MenuItem("")   # engine's plain-English reason
            # Nudge actions, only visible while a nudge is active
            self.n_header = rumps.MenuItem("Nudge: take a break?")
            self.n_break = rumps.MenuItem(f"Start {config.BREAK_MINUTES}-min break",
                                          callback=self.on_start_break)
            self.n_fine = rumps.MenuItem("I'm fine", callback=self.on_fine)
            # "Back to <app>", only visible after a break ends
            self.back = rumps.MenuItem("Back to work", callback=self.on_back)
            # Session controls
            self.phone_item = rumps.MenuItem("I grabbed my phone", callback=self.on_phone)
            self.stop_item = rumps.MenuItem("Stop session", callback=self.on_stop)
            self.start_menu = rumps.MenuItem("Start focus session")
            for m in SESSION_LENGTHS:
                self.start_menu.add(rumps.MenuItem(f"{m} min", callback=self._starter(m)))
            # Phone setup
            topic = config.NTFY_TOPIC or "(not set: add NTFY_TOPIC to .env)"
            setup = rumps.MenuItem("Phone setup")
            setup.add(rumps.MenuItem(f"ntfy topic: {topic}"))
            setup.add(rumps.MenuItem("Open setup guide (docs/phone-setup.md)",
                                     callback=self.on_open_docs))
            setup.add(rumps.MenuItem("Send test ping", callback=self.on_test_ping))

            items = [self.i_focused, self.i_momentum, self.i_likely, self.i_break,
                     self.i_reason, None, self.n_header, self.n_break, self.n_fine, self.back,
                     self.phone_item, self.stop_item, None, self.start_menu, setup]
            if demo:
                items += [None,
                          rumps.MenuItem("Demo: score now", callback=self.on_demo_score),
                          rumps.MenuItem("Demo: show nudge now", callback=self.on_demo_nudge)]
            self.menu = items
            self._refresh_menu()

            # Main-thread tick: applies the latest score and runs the break timer
            self.ticker = rumps.Timer(self.tick, 1)
            self.ticker.start()

        # ---- background -> main thread hand-off -------------------------
        def on_score(self, score: dict) -> None:
            """Called by the engine on ITS thread. Do the slow file reading
            here, then hand the result to the main thread via _latest."""
            now = time.time()
            try:
                inputs = list(events.read(self.path, kinds={"input"}, before=now + 1))
                momentum = momentum_text(activity_per_minute(inputs[-400:], now))
            except Exception:
                momentum = "Momentum: -"
            where = _load("where_score")
            likely = friendly_names(score.get("top") or [],
                                    getattr(where, "explain", None))
            with self._lock:
                self._latest = (score, {"momentum": momentum, "likely": likely,
                                        "reason": score.get("reason") or ""})

        def tick(self, _timer) -> None:
            with self._lock:
                latest, self._latest = self._latest, None
            if latest and self.session_start:
                self.score, self.view = latest
                if (wants_nudge(self.score, self.demo) and self.break_end is None
                        and not self.nudge_active
                        and cooldown_over(self.last_nudge, time.time(), self.cooldown)):
                    self.show_nudge(engine_logged=bool(self.score.get("nudge")))
            if self.break_end is not None and time.time() >= self.break_end:
                self.end_break()
            if (self.session_start and self.break_end is None and
                    time.time() - self.session_start >= self.session_minutes * 60):
                notify("Loophole", "Session done",
                       f"You focused for {format_minutes(self.focused_seconds())}. Nice work.")
                self.stop_session()
            self._refresh_menu()

        # ---- session ----------------------------------------------------
        def _starter(self, minutes: int):
            return lambda _item: self.start_session(minutes)

        def start_session(self, minutes: int) -> None:
            if self.session_start:
                return
            self.session_start = time.time()
            self.session_minutes = minutes
            self.break_seconds = 0.0
            self.last_break = self.last_nudge = None
            self.score, self.view = None, {}
            self.stop_event = threading.Event()
            events.write("session", self.path, action="start", minutes=minutes)

            tracker, phone, engine = _load("tracker"), _load("phone"), _load("engine")
            jobs = []
            if tracker and hasattr(tracker, "Tracker"):
                jobs.append(("tracker", lambda s: tracker.Tracker(path=self.path).run(s)))
            if phone and hasattr(phone, "run"):
                jobs.append(("phone", lambda s: phone.run(s, path=self.path)))
            if engine and hasattr(engine, "run"):
                jobs.append(("engine", lambda s: engine.run(s, on_score=self.on_score,
                                                            path=self.path)))
            for name, job in jobs:
                threading.Thread(target=self._guarded, args=(name, job), daemon=True,
                                 name=f"fg-{name}").start()
            print(f"[loophole] session started: {minutes} min, threads: "
                  f"{[n for n, _ in jobs]}")

        def _guarded(self, name, job) -> None:
            """Run a background loop; log crashes instead of killing the app."""
            try:
                job(self.stop_event)
            except Exception as e:
                print(f"[loophole] {name} thread crashed: {e!r}")

        def focused_seconds(self) -> float:
            if not self.session_start:
                return 0.0
            on_break = time.time() - (self.break_end - config.BREAK_MINUTES * 60) \
                if self.break_end else 0.0
            return time.time() - self.session_start - self.break_seconds - on_break

        def stop_session(self) -> None:
            if not self.session_start:
                return
            events.write("session", self.path, action="stop",
                         minutes=int(self.focused_seconds() // 60))
            if self.stop_event:
                self.stop_event.set()
            self.session_start = None
            self.break_end = None
            self.nudge_active = False
            self.score, self.view = None, {}

        def on_stop(self, _item) -> None:
            self.stop_session()
            self._refresh_menu()

        # ---- nudge ------------------------------------------------------
        def show_nudge(self, engine_logged: bool = False) -> None:
            self.nudge_active = True
            self.last_nudge = time.time()
            # The engine already logs "shown" when IT decides to nudge.
            # Log it here only for nudges the menu bar decided (demo mode).
            if not engine_logged:
                events.write("nudge", self.path, action="shown")
            notify("Loophole", "Time for a real break?",
                   nudge_message(self.view.get("likely", [])))

        def on_start_break(self, _item) -> None:
            if self.nudge_active:
                events.write("nudge", self.path, action="break")
            self.nudge_active = False
            try:
                apps = list(events.read(self.path, kinds={"app"}))
            except Exception:
                apps = []
            self.work_app = last_work_app(apps)
            self.break_end = time.time() + config.BREAK_MINUTES * 60
            self.last_break = time.time()
            self._refresh_menu()

        def end_break(self) -> None:
            self.break_seconds += config.BREAK_MINUTES * 60
            self.break_end = None
            self._refresh_menu()
            app_name = self.work_app
            if not app_name:
                notify("Loophole", "Break's over", "Time to get back to work.")
                return
            # A modal alert blocks only the main thread; tracker/engine keep running.
            clicked = rumps.alert(title=f"Back to {app_name}?",
                                  message="Your break is over.",
                                  ok=f"Back to {app_name}", cancel="Not yet")
            if clicked == 1:
                bring_to_front(app_name)

        def on_back(self, _item) -> None:
            if self.work_app:
                bring_to_front(self.work_app)

        def on_fine(self, _item) -> None:
            if self.nudge_active:
                events.write("nudge", self.path, action="dismiss")
            self.nudge_active = False
            self._refresh_menu()

        def on_phone(self, _item) -> None:
            phone = _load("phone")
            try:
                phone.record_manual(source="button", path=self.path)
            except Exception:
                events.write("phone", self.path, app="unknown", source="button")
            if self.nudge_active:
                events.write("nudge", self.path, action="phone")
                self.nudge_active = False
            self._refresh_menu()

        # ---- phone setup ------------------------------------------------
        def on_open_docs(self, _item) -> None:
            subprocess.run(["open", str(config.ROOT / "docs" / "phone-setup.md")], check=False)

        def on_test_ping(self, _item) -> None:
            phone = _load("phone")
            if not phone or not hasattr(phone, "send_test"):
                notify("Loophole", "Phone setup", "Phone module not ready yet.")
                return
            # Network call: keep it off the main thread.
            def send():
                try:
                    ok = phone.send_test(app="Test")
                    print(f"[loophole] test ping {'sent' if ok else 'FAILED (check NTFY_TOPIC)'}")
                except Exception as e:
                    print(f"[loophole] test ping failed: {e}")
            threading.Thread(target=send, daemon=True).start()
            notify("Loophole", "Phone setup",
                   "Test ping sent. It should show up here within ~10 s.")

        # ---- demo helpers -----------------------------------------------
        def on_demo_score(self, _item) -> None:
            engine = _load("engine")
            if not engine or not hasattr(engine, "score_now"):
                return
            def run():
                try:
                    self.on_score(engine.score_now(path=self.path))
                except Exception as e:
                    print(f"[loophole] score_now failed: {e!r}")
            threading.Thread(target=run, daemon=True).start()

        def on_demo_nudge(self, _item) -> None:
            if self.session_start and self.break_end is None:
                self.show_nudge()
                self._refresh_menu()

        # ---- drawing ----------------------------------------------------
        def _refresh_menu(self) -> None:
            now = time.time()
            in_session = self.session_start is not None
            on_break = self.break_end is not None

            if on_break:
                self.title = f"{BREAK_ICON} {format_countdown(self.break_end - now)}"
            elif in_session:
                self.title = DOTS[colour_for(self.score)]
            else:
                self.title = IDLE_DOT

            if in_session:
                self.i_focused.title = (f"Focused {format_minutes(self.focused_seconds())}"
                                        f" of {self.session_minutes} min")
            else:
                self.i_focused.title = "Not in a session"
            self.i_momentum.title = self.view.get("momentum", "Momentum: waiting for data")
            likely = self.view.get("likely") or []
            self.i_likely.title = "Likely next: " + (", ".join(likely) if likely else "-")
            self.i_break.title = last_break_text(self.last_break, now)
            self.i_reason.title = self.view.get("reason", "")

            for item in (self.i_momentum, self.i_likely, self.i_break,
                         self.phone_item, self.stop_item):
                item.hidden = not in_session
            self.i_reason.hidden = not (in_session and self.view.get("reason"))
            for item in (self.n_header, self.n_break, self.n_fine):
                item.hidden = not self.nudge_active
            self.back.title = f"Back to {self.work_app}" if self.work_app else "Back to work"
            self.back.hidden = not (in_session and not on_break and self.work_app
                                    and self.last_break is not None)
            self.start_menu.hidden = in_session

    return LoopholeApp()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Loophole menu-bar app")
    parser.add_argument("--demo", action="store_true",
                        help=f"stage demo: {DEMO_COOLDOWN}s nudge cooldown, lower thresholds")
    args = parser.parse_args(argv)
    if args.demo:
        print("=" * 60)
        print(" DEMO MODE IS ON")
        print(f"  nudge cooldown: {DEMO_COOLDOWN}s (normal {config.NUDGE_COOLDOWN // 60} min)")
        print(f"  stall threshold: {DEMO_STALL_THRESHOLD} (normal {config.STALL_THRESHOLD})")
        print(f"  rank threshold: {DEMO_RANK_THRESHOLD} (normal {config.RANK_THRESHOLD})")
        print("  extra menu items: 'Demo: score now', 'Demo: show nudge now'")
        print("=" * 60)
    print(f"[loophole] writing events to {config.EVENTS_FILE}")
    build_app(demo=args.demo).run()


if __name__ == "__main__":
    main()
