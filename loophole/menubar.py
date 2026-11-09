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
- When a nudge fires (at most once per cooldown), we pop up an alert with
  three choices: take a break, I'm fine, I picked up my phone. The same three
  actions also stay in the menu while the nudge is active. The dot is red
  while a nudge is active.
- When the front app or tab is a distraction (DISTRACTION_DOMAINS/APPS), the
  island pins open: "Distraction detected. Put it away, take a walk" with an
  "I'm back" button. The time until "I'm back" counts as break time.
  If a high-pace spell is pending (pace.py) while a PHONE distraction screen
  is open, the pace nudge is shown on that screen (one sentence more and a
  break button) and logged as `nudge action=shown nudge_kind=pace via=distraction`
  (PROTOCOL.md deviation 16).
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

SESSION_LENGTHS = config.SESSION_LENGTHS  # minutes offered in the menu (.env)

# Demo mode: the stage demo can't wait 10 minutes for a second nudge.
# Not shorter: the demo thresholds sit below the median stall, so a 60 s gap
# re-nudged right after "I'm fine".
DEMO_COOLDOWN = config.DEMO_COOLDOWN     # seconds between nudges in demo mode
# Demo mode nudges on ANY close pickup-memory match (normally the match must
# also beat the base rate by PRECURSOR_MIN_LIFT, after PRECURSOR_MIN_EPISODES).
DEMO_MIN_EPISODES = 1

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

def phone_link(topic: str | None = None) -> str:
    """The URL the iPhone Shortcut posts to (docs/phone-setup.md)."""
    return f"https://ntfy.sh/{config.NTFY_TOPIC if topic is None else topic}"


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


PHONE_APP_NAMES = {"tiktok": "TikTok", "youtube": "YouTube", "whatsapp": "WhatsApp",
                   "linkedin": "LinkedIn", "imessage": "iMessage", "x": "X"}
# A phone pickup older than this (e.g. backfilled at session start) is history,
# not something happening now: don't pop up for it.
PHONE_DISTRACTION_MAX_AGE_S = 120


def phone_triggers_distraction(rec: dict, now: float) -> bool:
    """Should this phone record open the 'Distraction detected' screen?
    Not test pings, not the user's own 'Grabbed phone' button, not old ones."""
    return (rec.get("kind", "phone") == "phone"
            and rec.get("source") not in ("test", "button")
            and now - rec.get("ts", 0) <= PHONE_DISTRACTION_MAX_AGE_S)


def distraction_name(rec: dict) -> str:
    """'youtube.com', 'Messages' or 'TikTok on your phone': what to call the
    distraction on screen."""
    if rec.get("kind") == "phone":
        app = (rec.get("app") or "").strip()
        nice = PHONE_APP_NAMES.get(app.lower(), app[:1].upper() + app[1:])
        return f"{nice} on your phone" if nice and nice.lower() != "unknown" else "your phone"
    domain = (rec.get("domain") or "").lower().removeprefix("www.").removeprefix("m.")
    return domain or rec.get("app") or "a distraction"


# Sites where one page can be work and the next one not (a tutorial vs a
# prank video): "This is work" allows only that page title, not the site.
PER_TITLE_DOMAINS = ("youtube.com", "reddit.com", "old.reddit.com")


def work_key(rec: dict) -> str | None:
    """What "This is work" allows for the rest of the session: one YouTube or
    Reddit page (by title), or a whole other site or app. None for phone
    pickups (the laptop can't vouch for those)."""
    if rec.get("kind") == "phone":
        return None
    domain = (rec.get("domain") or "").lower().removeprefix("www.").removeprefix("m.")
    if domain in PER_TITLE_DOMAINS:
        title = (rec.get("title") or "").strip()
        return f"{domain}|{title}" if title else None
    return domain or (rec.get("app") or "").lower() or None


def last_work_app(app_records: list[dict]) -> str | None:
    """Name of the most recent app the user was working in (for `open -a`)."""
    for rec in reversed(app_records):
        if rec.get("app") and app_kind(rec) == "work":
            return rec["app"]
    return None


def colour_for(score: dict | None, stall: float = config.STALL_THRESHOLD,
               rank: int = config.NUDGE_RANK_THRESHOLD, nudge_active: bool = False) -> str:
    """Dot colour. Red while a nudge is on screen (also demo nudges, which the
    engine does not know about). Otherwise the engine's colour if it sent a
    valid one.

    Fallback (same rule as engine.colour_for): red if it wants a nudge; amber
    if stalling, or if a distraction is very likely next AND the user is
    half-way to a stall; otherwise green.
    """
    if nudge_active:
        return "red"
    if not score:
        return "green"
    if score.get("colour") in DOTS:
        return score["colour"]
    if score.get("nudge"):
        return "red"
    when = score.get("when") or 0.0
    where_rank = score.get("where_rank")
    if when >= stall:
        return "amber"
    if where_rank is not None and where_rank <= rank and when >= stall / 2:
        return "amber"
    return "green"


def wants_nudge(score: dict | None, demo: bool = False) -> bool:
    """Does this score ask for a nudge? Demo mode uses looser thresholds."""
    if not score:
        return False
    if score.get("nudge"):
        return True
    if demo:
        memory = score.get("memory") or {}
        return bool(memory.get("matches")) and \
            (memory.get("n_episodes") or 0) >= DEMO_MIN_EPISODES
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


def nudge_message(likely: list[str], pace_text: str = "") -> str:
    """The nudge text. The live triggers are the pickup memory and the pickup
    pace (nudge.decide), so say which, not "you've slowed down" (the stall
    score no longer decides). `pace_text` is set when the pace trigger fired."""
    base = pace_text or "This looks like the minutes before your past phone pickups."
    if likely:
        return (f"{base} {likely[0]} looks like your next move. "
                f"Take a {config.BREAK_MINUTES}-minute break instead?")
    return f"{base} Take a {config.BREAK_MINUTES}-minute break instead?"


def memory_status(memory: dict | None) -> str:
    """'Pickup memory: learned from 17 pickups' or '...: 7 of 15 pickups needed'.
    Counts usable pickups (laptop in use before them); says how many real
    pickups there were in total when that differs."""
    if not memory:
        return "Pickup memory: -"
    total = memory.get("n_episodes") or 0
    n = memory.get("n_usable", total) or 0
    extra = f" ({total} in total)" if n != total else ""
    if memory.get("ready") and memory.get("early"):
        return (f"Pickup memory: early guesses, less accurate "
                f"({n} of {config.PRECURSOR_CONFIDENT_EPISODES} pickups){extra}")
    if memory.get("ready"):
        return f"Pickup memory: learned from {n} pickup{'s' if n != 1 else ''}{extra}"
    return f"Pickup memory: {n} of {config.PRECURSOR_MIN_EPISODES} pickups needed{extra}"


def with_memory(message: str, memory_text: str) -> str:
    """Add the pickup-memory sentence to a nudge message, if there is one."""
    return f"{message}\n\n{memory_text}" if memory_text else message


# Nudge alert buttons. rumps.alert returns 1 for ok, 0 for cancel, -1 for other.
NUDGE_CHOICES = {1: "break", 0: "dismiss", -1: "phone"}


def nudge_choice(clicked) -> str:
    """rumps.alert return value -> nudge action. Anything odd counts as dismiss."""
    return NUDGE_CHOICES.get(clicked, "dismiss")


def nudge_record(action: str, staged: bool = False) -> dict:
    """Fields for a `nudge` record. Staged (demo button) nudges are marked so
    the dashboard and evals can leave them out."""
    fields = {"action": action}
    if staged:
        fields["staged"] = True
    return fields


def last_phone_text(rec: dict | None, now: float) -> str:
    """'Last phone: Instagram, 2 min ago'. Test pings are labelled."""
    if not rec:
        return "Last phone: none yet"
    app = rec.get("app") or "Unknown"
    if rec.get("source") == "test":
        app += " (test ping)"
    return f"Last phone: {app}, {format_minutes(now - rec['ts'])} ago"


def last_break_text(last_break_ts: float | None, now: float) -> str:
    if last_break_ts is None:
        return "Last break: none yet"
    return f"Last break: {format_minutes(now - last_break_ts)} ago"


# Amber = the pickup memory recognises this moment but can't nudge now
# (cooldown, or you just came back): see engine.colour_for.
ISLAND_HEADLINES = {"green": "In the flow", "amber": "Looks like before a pickup",
                    "red": "Time for a real break?"}


def island_view(*, in_session: bool, on_break: bool, colour: str, nudge_active: bool,
                back_prompt: bool, focused_s: float, session_min: int,
                remaining_s: float, break_left_s: float, bars: list[int],
                likely: list[str], reason: str, work_app: str | None,
                momentum: str = "", memory_text: str = "",
                pace_text: str = "", pace_nudge_text: str = "",
                distraction: str | None = None, can_mark_work: bool = False,
                distraction_pace_text: str = "") -> dict:
    """What the notch island shows. Pure, so tests can check it.

    Keys: colour, value (collapsed: dot + timer), headline, sub, progress,
    details, bars, buttons [(id, title, primary)], pinned (stay open), pulse.
    """
    work = work_app or "work"
    if back_prompt:
        return {"colour": "blue", "value": "", "pinned": True,
                "headline": f"Back to {work}?", "sub": "Your break is over. Nice reset.",
                "buttons": [("back", f"Back to {work}", True), ("not_yet", "Not yet", False)]}
    if distraction and in_session and not on_break:
        sub = (f"You opened {distraction}. Put it away, take a short walk "
               "or get some water, and press the button when you're back.")
        buttons = [("im_back", "I'm back", True)]
        if distraction_pace_text:
            # The pace nudge, shown on the screen that is already open.
            sub += "\n\n" + distraction_pace_text
            buttons.append(("break", f"{config.BREAK_MINUTES}-min break", False))
        return {"colour": "red", "value": "", "pinned": True, "pulse": True,
                "headline": "Distraction detected",
                "sub": sub,
                "details": ["This time counts as a break, not a slip."],
                "buttons": buttons
                + ([("is_work", "This is work", False)] if can_mark_work else [])}
    if not in_session:
        return {"colour": "grey", "value": "",
                "headline": "Ready to focus", "sub": "Pick a session length to start.",
                "buttons": [(f"start:{m}", f"{m} min", m == SESSION_LENGTHS[0])
                            for m in SESSION_LENGTHS]}
    if on_break:
        return {"colour": "blue", "value": format_countdown(break_left_s),
                "headline": "On a break",
                "sub": f"{format_countdown(break_left_s)} left. Stand up, look away, drink water.",
                "progress": 1 - break_left_s / (config.BREAK_MINUTES * 60),
                "buttons": [("stop", "End session", False)]}
    if nudge_active:
        colour = "red"
    progress = focused_s / (session_min * 60) if session_min else None
    view = {"colour": colour,
            "value": format_countdown(remaining_s),
            "headline": ISLAND_HEADLINES.get(colour, "In the flow"),
            "sub": (f"Focused {format_minutes(focused_s)} of {session_min} min · "
                    f"{format_countdown(remaining_s)} left"),
            "progress": progress, "bars": bars, "pinned": nudge_active,
            "pulse": nudge_active}
    details = []
    if momentum and not momentum.endswith("no typing yet"):
        details.append(momentum.split("  ", 1)[-1].capitalize())
    # While green, "likely next" and the nudge rule's reason only confuse
    # ("TikTok next" while typing 3x faster). Show them when something is up.
    if likely and colour != "green":
        details.append("Likely next: " + ", ".join(likely))
    if reason and not nudge_active and colour != "green":
        details.append(reason)
    # The pickup memory is a memory, not an alarm: show it even while green.
    if memory_text:
        details.append("Heads-up: " + memory_text)
    if pace_text:
        details.append(pace_text)
    view["details"] = details
    if nudge_active:
        view["sub"] = nudge_message(likely, pace_nudge_text)
        view["buttons"] = [("break", f"{config.BREAK_MINUTES}-min break", True),
                           ("fine", "I'm fine", False), ("phone", "Grabbed phone", False)]
    else:
        view["buttons"] = [("break", "Take a break", False),
                           ("phone", "Grabbed phone", False), ("stop", "Stop", False)]
    return view


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

# Default session tag for the app's thread callbacks: "whatever session is
# running now". Used by direct calls (tests); real threads pass their own id.
CURRENT = object()


def build_app(demo: bool = False, events_path: Path = config.EVENTS_FILE,
              island: bool = True):
    """Create the rumps App. rumps is imported here so tests can import this
    file without a GUI."""
    import rumps

    class LoopholeApp(rumps.App):
        def __init__(self):
            # Our own Quit item, so quitting logs "session stop" first.
            super().__init__("Loophole", title=IDLE_DOT, quit_button=None)
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
            self.nudge_staged = False       # active nudge came from the demo button
            self.work_app: str | None = None
            self.score: dict | None = None
            self.view: dict = {}            # momentum + likely names
            self._latest: tuple[dict, dict] | None = None
            self._lock = threading.Lock()
            # Session ids (under _lock): each start_session gets a new id, and
            # its threads' callbacks carry it. Results from an old session's
            # threads (e.g. a slow score_now that ends after stop/start) are
            # dropped. None = no session running.
            self._session_counter = 0
            self._current_session: int | None = None
            self._last_phone: dict | None = None   # written by the phone thread
            self.back_prompt = False        # island is asking "Back to <app>?"
            self.distraction: dict | None = None   # app record we're asking about
            self.distraction_since: float | None = None
            # Pace nudge shown on an open phone distraction screen (deviation 16)
            self.distraction_nudge = False
            self.distraction_pace_text = ""
            self._pending_distraction: dict | None = None   # from the tracker thread
            self.work_ok: set[str] = set()  # "This is work" answers, this session
            self._seen_app: dict | None = None
            phone = _load("phone")
            try:
                self._last_phone = phone.last_pickup(self.path) if phone else None
            except Exception:
                self._last_phone = None

            # Info lines (no callback = greyed out, read-only)
            self.i_focused = rumps.MenuItem("Not in a session")
            self.i_momentum = rumps.MenuItem("Momentum: -")
            self.i_likely = rumps.MenuItem("Likely next: -")
            self.i_break = rumps.MenuItem("Last break: none yet")
            self.i_reason = rumps.MenuItem("")   # engine's plain-English reason
            self.i_phone = rumps.MenuItem("Last phone: none yet")
            self.i_memory = rumps.MenuItem("Pickup memory: -")
            self.i_pace = rumps.MenuItem("Pace: -")
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
            setup = rumps.MenuItem("Phone setup")
            if config.NTFY_TOPIC:
                setup.add(rumps.MenuItem(f"Your phone link: {phone_link()}"))
                setup.add(rumps.MenuItem("Copy phone link", callback=self.on_copy_link))
            else:
                setup.add(rumps.MenuItem("No phone link yet: run  bash setup.sh"))
            setup.add(rumps.MenuItem("Open phone setup guide", callback=self.on_open_docs))
            setup.add(rumps.MenuItem("Send test ping", callback=self.on_test_ping))
            setup.add(None)
            setup.add(rumps.MenuItem("Open settings file (.env)", callback=self.on_open_settings))

            items = [self.i_focused, self.i_momentum, self.i_likely, self.i_break,
                     self.i_phone, self.i_pace, self.i_memory, self.i_reason, None, self.n_header, self.n_break, self.n_fine, self.back,
                     self.phone_item, self.stop_item, None, self.start_menu, setup]
            if demo:
                items += [None,
                          rumps.MenuItem("Demo: score now", callback=self.on_demo_score),
                          rumps.MenuItem("Demo: show nudge now", callback=self.on_demo_nudge),
                          rumps.MenuItem("Demo: show distraction", callback=self.on_demo_distraction)]
            items += [None, rumps.MenuItem("Quit Loophole", callback=self.on_quit)]
            self.menu = items

            # Notch island (always on top). If it fails, we fall back to alerts.
            self.island = None
            if island:
                try:
                    from .island import Island
                    self.island = Island(self.on_island)
                except Exception as e:
                    print(f"[loophole] island unavailable: {e!r}")
            self._refresh_menu()

            # Main-thread tick: applies the latest score and runs the break timer
            self.ticker = rumps.Timer(self.tick, 1)
            self.ticker.start()

        # ---- background -> main thread hand-off -------------------------
        def _is_current(self, session) -> bool:
            """True if a callback tagged with `session` belongs to the running
            session. CURRENT (the default for direct calls) always counts.
            Call with self._lock held."""
            return session is CURRENT or (session is not None
                                          and session == self._current_session)

        def on_score(self, score: dict, session=CURRENT) -> None:
            """Called by the engine on ITS thread. Do the slow file reading
            here, then hand the result to the main thread via _latest.
            Dropped if `session` is not the running session."""
            now = time.time()
            bars: list[int] = []
            try:
                inputs = list(events.read(self.path, kinds={"input"}, before=now + 1))
                bars = activity_per_minute(inputs[-400:], now)
                momentum = momentum_text(bars)
            except Exception:
                momentum = "Momentum: -"
            # BehaviorGPT's "app pulling at you": named only when the pull is
            # strong (where_score.WHERE_PULL_MIN_Z), so no fixed-favourite noise.
            # The z value stays on the dashboard; users get the app name only.
            if score.get("pull_name"):
                likely = [score["pull_name"]]
            else:
                likely = []
            memory = score.get("memory") or None
            pace = score.get("pace") or {}
            with self._lock:
                if not self._is_current(session):
                    return   # stale: its session was stopped (maybe a new one started)
                self._latest = (score, {"momentum": momentum, "likely": likely, "bars": bars,
                                        "reason": score.get("reason") or "",
                                        "memory_status": memory_status(memory),
                                        "memory_text": (memory or {}).get("text")
                                        or (self.demo and (memory or {}).get("match_text")) or "",
                                        "pace_text": pace.get("text") or "",
                                        "pace_nudge_text": (pace.get("pending_text")
                                                            or pace.get("nudge_text") or ""
                                                            if score.get("nudge_kind") == "pace"
                                                            else ""),
                                        "pace_pending_text": (pace.get("pending_text") or ""
                                                              if pace.get("pending") else ""),
                                        "pace_pending_ts": (pace.get("pending_ts")
                                                            if pace.get("pending") else None),
                                        "cooldown_s": pace.get("cooldown_s")})

        def on_tracker_tick(self, t, session=CURRENT) -> None:
            """Called by the tracker thread every sample. Flags a newly opened
            distraction; the main-thread tick shows it. Ignored if `session`
            is not the running session."""
            with self._lock:
                if not self._is_current(session):
                    return
            rec = t.last_app
            if rec is None or rec is self._seen_app:
                return
            self._seen_app = rec
            if app_kind(rec) == "distraction" and work_key(rec) not in self.work_ok:
                with self._lock:
                    if self._is_current(session):
                        self._pending_distraction = rec

        def on_phone_record(self, rec: dict, session=CURRENT) -> None:
            """Called by the phone thread for each record it writes. "Last
            phone" is always updated (it is not per session); a distraction
            is only raised for the running session."""
            with self._lock:
                if self._last_phone is None or rec["ts"] >= self._last_phone["ts"]:
                    self._last_phone = rec
                if (self._is_current(session)
                        and phone_triggers_distraction(rec, time.time())):
                    self._pending_distraction = rec

        def tick(self, _timer) -> None:
            with self._lock:
                latest, self._latest = self._latest, None
                pending, self._pending_distraction = self._pending_distraction, None
            if (pending and self.session_start and self.break_end is None
                    and not self.distraction and not self.back_prompt):
                self.show_distraction(pending)
            if latest and self.session_start:
                self.score, self.view = latest
                if not self.demo:
                    # Personal cooldown: your usual gap between pickups (pace.py).
                    self.cooldown = self.view.get("cooldown_s") or config.NUDGE_COOLDOWN
                if (wants_nudge(self.score, self.demo) and self.break_end is None
                        and not self.nudge_active and not self.distraction
                        and cooldown_over(self.last_nudge, time.time(), self.cooldown)):
                    self.show_nudge(staged=self.demo)
            if self.session_start:
                self._pace_on_distraction()
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
            self.distraction = self.distraction_since = None
            self.distraction_nudge, self.distraction_pace_text = False, ""
            self._seen_app = None
            self.work_ok = set()
            with self._lock:
                self._session_counter += 1
                sid = self._current_session = self._session_counter
                self._pending_distraction = None
                self._latest = None
            stop = self.stop_event = threading.Event()
            events.write("session", self.path, action="start", minutes=minutes)

            # Each callback is bound to this session's id, so results that
            # arrive after a stop (or after the next start) are dropped.
            tracker, phone, engine = _load("tracker"), _load("phone"), _load("engine")
            jobs = []
            if tracker and hasattr(tracker, "Tracker"):
                jobs.append(("tracker", lambda s: tracker.Tracker(path=self.path).run(
                    s, on_tick=lambda t: self.on_tracker_tick(t, session=sid))))
            if phone and hasattr(phone, "run"):
                jobs.append(("phone", lambda s: phone.run(
                    s, path=self.path,
                    on_record=lambda rec: self.on_phone_record(rec, session=sid))))
            if engine and hasattr(engine, "run"):
                jobs.append(("engine", lambda s: engine.run(
                    s, on_score=lambda score: self.on_score(score, session=sid),
                    path=self.path)))
            for name, job in jobs:
                threading.Thread(target=self._guarded, args=(name, job, stop), daemon=True,
                                 name=f"fg-{name}").start()
            print(f"[loophole] session started: {minutes} min, threads: "
                  f"{[n for n, _ in jobs]}")

        def _guarded(self, name, job, stop: threading.Event | None = None) -> None:
            """Run a background loop; log crashes instead of killing the app.
            `stop` is the session's own event, passed in at start so a fast
            stop/start can't hand this thread the next session's event."""
            try:
                job(stop if stop is not None else self.stop_event)
            except Exception as e:
                print(f"[loophole] {name} thread crashed: {e!r}")

        def focused_seconds(self) -> float:
            if not self.session_start:
                return 0.0
            on_break = time.time() - (self.break_end - config.BREAK_MINUTES * 60) \
                if self.break_end else 0.0
            walking = time.time() - self.distraction_since if self.distraction_since else 0.0
            return time.time() - self.session_start - self.break_seconds - on_break - walking

        def stop_session(self) -> None:
            if not self.session_start:
                return
            events.write("session", self.path, action="stop",
                         minutes=int(self.focused_seconds() // 60))
            if self.stop_event:
                self.stop_event.set()
            with self._lock:
                self._current_session = None   # late callbacks from its threads are dropped
                self._latest = None
                self._pending_distraction = None
            self.session_start = None
            self.break_end = None
            self.nudge_active = self.nudge_staged = False
            self.back_prompt = False
            self.distraction = self.distraction_since = None
            self.distraction_nudge, self.distraction_pace_text = False, ""
            self.score, self.view = None, {}

        def on_stop(self, _item) -> None:
            self.stop_session()
            self._refresh_menu()

        def on_quit(self, _item) -> None:
            self.stop_session()   # logs "session stop" if a session is running
            rumps.quit_application()

        # ---- nudge ------------------------------------------------------
        def show_nudge(self, staged: bool = False) -> None:
            """Show a nudge and log `nudge action=shown`. This is the only
            place that record is written: the engine only decides (score
            record, nudge=True), so a decision we don't show is never counted.
            staged=True for the demo button and for every --demo mode nudge
            (looser rule), so the cooldown and evals leave them out."""
            self.nudge_active = True
            self.nudge_staged = staged
            self.last_nudge = time.time()
            events.write("nudge", self.path, **nudge_record("shown", staged))
            self._refresh_menu()   # dot goes red before the alert opens
            message = with_memory(nudge_message(self.view.get("likely", []),
                                                self.view.get("pace_nudge_text", "")),
                                  self.view.get("memory_text", ""))
            notify("Loophole", "Time for a real break?", message)
            if self.island:
                return   # the island opens with the three choices; no modal
            # Modal, like end_break. It blocks only the main thread: the
            # 1 s timer runs in the default run-loop mode, so it simply pauses
            # (no re-entry) and catches up when the alert closes. The tracker,
            # phone and engine threads keep running.
            try:
                from AppKit import NSApplication
                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            except Exception:
                pass
            clicked = rumps.alert(title="Time for a real break?", message=message,
                                  ok=f"Take a {config.BREAK_MINUTES}-min break",
                                  cancel="I'm fine", other="I picked up my phone")
            if not self.nudge_active or not self.session_start:
                return   # answered from the menu meanwhile, or session ended
            action = nudge_choice(clicked)
            if action == "break":
                self.on_start_break(None)
            elif action == "phone":
                self.on_phone(None)
            else:
                self.on_fine(None)

        # ---- distraction ------------------------------------------------
        def show_distraction(self, rec: dict, staged: bool = False) -> None:
            """A distraction is in front: ask the user to step away."""
            if self.nudge_active:
                self._log_nudge_answer("dismiss")   # the distraction screen replaces it
            try:
                apps = list(events.read(self.path, kinds={"app"}))
            except Exception:
                apps = []
            self.work_app = last_work_app(apps) or self.work_app
            self.distraction = rec
            self.distraction_since = time.time()
            name = distraction_name(rec)
            events.write("distraction", self.path, action="shown", target=name, staged=staged)
            self._refresh_menu()
            message = (f"You opened {name}. Put it away, take a short walk, "
                       "and come back when you're ready.")
            notify("Loophole", "Distraction detected", message)
            if self.island:
                return   # the island pins open with "I'm back"
            try:
                from AppKit import NSApplication
                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            except Exception:
                pass
            if work_key(rec) is None:
                rumps.alert(title="Distraction detected", message=message, ok="I'm back")
                self.on_im_back(None)
                return
            clicked = rumps.alert(title="Distraction detected", message=message,
                                  ok="I'm back", cancel="This is work")
            if clicked == 0:
                self.on_this_is_work(None)
            else:
                self.on_im_back(None)

        def _pace_on_distraction(self) -> None:
            """Show a pending pace spell on the open PHONE distraction screen
            (deviation 16). The modal show_nudge never opens on top of that
            screen, so without this the spell would be lost while the user is
            on the phone. One per spell; the cooldown applies."""
            spell = self.view.get("pace_pending_ts")
            now = time.time()
            if (self.distraction and self.distraction.get("kind") == "phone"
                    and not self.distraction_nudge and self.break_end is None
                    and spell and spell > (self.last_nudge or 0)
                    and cooldown_over(self.last_nudge, now, self.cooldown)):
                self.last_nudge = now
                self.distraction_pace_text = self.view.get("pace_pending_text", "")
                self.distraction_nudge = True
                # Unstaged (except in --demo), so the engine's _last_nudge_ts
                # uses up the spell and the cooldown stays in sync.
                events.write("nudge", self.path, **nudge_record("shown", self.demo),
                             nudge_kind="pace", via="distraction")
                self._refresh_menu()

        def _answer_distraction_nudge(self, action: str) -> None:
            """Log the answer to a pace nudge shown on the distraction screen."""
            if self.distraction_nudge:
                events.write("nudge", self.path, **nudge_record(action, self.demo),
                             nudge_kind="pace", via="distraction")
            self.distraction_nudge, self.distraction_pace_text = False, ""

        def on_distraction_break(self) -> None:
            """'Break' on the distraction screen: close it, then a break."""
            self._answer_distraction_nudge("break")
            self.on_im_back(None, bring_back=False)
            self.on_start_break(None)

        def on_im_back(self, _item, bring_back: bool = True) -> None:
            if not self.distraction:
                return
            self._answer_distraction_nudge("dismiss")
            away = time.time() - (self.distraction_since or time.time())
            self.break_seconds += away
            self.last_break = time.time()
            events.write("distraction", self.path, action="back",
                         target=distraction_name(self.distraction), seconds=round(away))
            self.distraction = self.distraction_since = None
            if self.work_app and bring_back:
                bring_to_front(self.work_app)
            self._refresh_menu()

        def on_this_is_work(self, _item) -> None:
            """The user says the page in front is work: close the screen without
            counting a break, and don't ask again about it this session."""
            if not self.distraction:
                return
            key = work_key(self.distraction)
            if key:
                self.work_ok.add(key)
            self._answer_distraction_nudge("dismiss")
            away = time.time() - (self.distraction_since or time.time())
            events.write("distraction", self.path, action="work",
                         target=distraction_name(self.distraction), seconds=round(away))
            self.distraction = self.distraction_since = None
            self._refresh_menu()

        def _log_nudge_answer(self, action: str) -> None:
            """Log break/dismiss/phone for the active nudge, then clear it."""
            if self.nudge_active:
                events.write("nudge", self.path, **nudge_record(action, self.nudge_staged))
            self.nudge_active = False
            self.nudge_staged = False

        def on_start_break(self, _item) -> None:
            self._log_nudge_answer("break")
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
            if self.island:
                self.back_prompt = True   # island asks; no modal
                self._refresh_menu()
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
            self._log_nudge_answer("dismiss")
            self._refresh_menu()

        def on_phone(self, _item) -> None:
            phone = _load("phone")
            try:
                rec = phone.record_manual(source="button", path=self.path)
            except Exception:
                rec = events.write("phone", self.path, app="unknown", source="button")
            self.on_phone_record(rec)
            self._log_nudge_answer("phone")
            self._refresh_menu()

        # ---- island buttons ---------------------------------------------
        def on_island(self, action: str) -> None:
            if action.startswith("start:"):
                self.start_session(int(action.split(":", 1)[1]))
            elif action == "break" and self.session_start and self.distraction:
                self.on_distraction_break()
            elif action == "break" and self.session_start and self.break_end is None:
                self.on_start_break(None)
            elif action == "fine":
                self.on_fine(None)
            elif action == "phone" and self.session_start:
                self.on_phone(None)
            elif action == "stop":
                self.stop_session()
            elif action == "back":
                self.back_prompt = False
                self.on_back(None)
            elif action == "not_yet":
                self.back_prompt = False
            elif action == "im_back":
                self.on_im_back(None)
            elif action == "is_work":
                self.on_this_is_work(None)
            self._refresh_menu()

        # ---- phone setup ------------------------------------------------
        def on_open_docs(self, _item) -> None:
            subprocess.run(["open", "-e", str(config.ROOT / "docs" / "phone-setup.md")], check=False)

        def on_copy_link(self, _item) -> None:
            subprocess.run(["pbcopy"], input=phone_link().encode(), check=False)
            notify("Loophole", "Phone link copied",
                   "Paste it into the Get Contents of URL action. If your iPhone uses "
                   "the same Apple ID, you can paste it straight on the phone.")

        def on_open_settings(self, _item) -> None:
            env = config.ROOT / ".env"
            if not env.exists():
                notify("Loophole", "No settings file", "Run  bash setup.sh  first.")
                return
            subprocess.run(["open", "-e", str(env)], check=False)
            notify("Loophole", "Settings", "Save the file, then quit and restart Loophole.")

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
                   "Test ping sent. During a session, the 'Last phone' line in "
                   "this menu shows it within ~10 s.")

        # ---- demo helpers -----------------------------------------------
        def on_demo_score(self, _item) -> None:
            engine = _load("engine")
            if not engine or not hasattr(engine, "score_now"):
                return
            with self._lock:
                sid = self._current_session   # score belongs to the session at click time
            def run():
                try:
                    self.on_score(engine.score_now(path=self.path), session=sid)
                except Exception as e:
                    print(f"[loophole] score_now failed: {e!r}")
            threading.Thread(target=run, daemon=True).start()

        def on_demo_nudge(self, _item) -> None:
            if self.session_start and self.break_end is None:
                # Staged for the audience: mark it so stats can leave it out.
                self.show_nudge(staged=True)
                self._refresh_menu()

        def on_demo_distraction(self, _item) -> None:
            if self.session_start and self.break_end is None and not self.distraction:
                self.show_distraction({"app": "Google Chrome", "domain": "youtube.com"},
                                      staged=True)

        # ---- drawing ----------------------------------------------------
        def _refresh_menu(self) -> None:
            now = time.time()
            in_session = self.session_start is not None
            on_break = self.break_end is not None

            if on_break:
                self.title = f"{BREAK_ICON} {format_countdown(self.break_end - now)}"
            elif in_session:
                self.title = DOTS[colour_for(self.score, nudge_active=self.nudge_active)]
            else:
                self.title = IDLE_DOT

            if in_session:
                self.i_focused.title = (f"Focused {format_minutes(self.focused_seconds())}"
                                        f" of {self.session_minutes} min")
            else:
                self.i_focused.title = "Not in a session"
            self.i_momentum.title = self.view.get("momentum", "Momentum: waiting for data")
            likely = self.view.get("likely") or []
            if colour_for(self.score, nudge_active=self.nudge_active) == "green":
                likely = []   # same rule as the island: hide while in the flow
            self.i_likely.title = "Likely next: " + (", ".join(likely) if likely else "-")
            self.i_break.title = last_break_text(self.last_break, now)
            with self._lock:
                last_phone = self._last_phone
            self.i_phone.title = last_phone_text(last_phone, now)
            self.i_reason.title = self.view.get("reason", "")
            self.i_memory.title = self.view.get("memory_status", "Pickup memory: -")
            self.i_pace.title = self.view.get("pace_text") or "Pace: -"

            for item in (self.i_momentum, self.i_likely, self.i_break, self.i_memory, self.i_pace,
                         self.phone_item, self.stop_item):
                item.hidden = not in_session
            self.i_reason.hidden = not (in_session and self.view.get("reason"))
            for item in (self.n_header, self.n_break, self.n_fine):
                item.hidden = not self.nudge_active
            self.back.title = f"Back to {self.work_app}" if self.work_app else "Back to work"
            self.back.hidden = not (in_session and not on_break and self.work_app
                                    and self.last_break is not None)
            self.start_menu.hidden = in_session

            if self.island:
                self.island.update(island_view(
                    in_session=in_session, on_break=on_break,
                    colour=colour_for(self.score, nudge_active=self.nudge_active),
                    nudge_active=self.nudge_active, back_prompt=self.back_prompt,
                    focused_s=self.focused_seconds(), session_min=self.session_minutes,
                    remaining_s=(self.session_start + self.session_minutes * 60 - now
                                 if in_session else 0),
                    break_left_s=(self.break_end - now) if on_break else 0,
                    bars=self.view.get("bars") or [], likely=likely,
                    reason=self.view.get("reason", ""), work_app=self.work_app,
                    momentum=self.view.get("momentum", ""),
                    memory_text=self.view.get("memory_text", ""),
                    pace_text=self.view.get("pace_text", ""),
                    pace_nudge_text=self.view.get("pace_nudge_text", ""),
                    distraction=(distraction_name(self.distraction)
                                 if self.distraction else None),
                    can_mark_work=bool(self.distraction and work_key(self.distraction)),
                    distraction_pace_text=self.distraction_pace_text))

    return LoopholeApp()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Loophole menu-bar app")
    parser.add_argument("--demo", action="store_true",
                        help=f"stage demo: {DEMO_COOLDOWN // 60} min nudge cooldown, lower thresholds")
    parser.add_argument("--no-island", action="store_true",
                        help="no notch island; use the menu-bar dot and pop-up alerts only")
    args = parser.parse_args(argv)
    if args.demo:
        print("=" * 60)
        print(" DEMO MODE IS ON")
        print(f"  nudge cooldown: {DEMO_COOLDOWN // 60} min (normal {config.NUDGE_COOLDOWN // 60} min)")
        print("  nudges on any close pickup-memory match "
              f"(normal: {config.PRECURSOR_MIN_EPISODES}+ pickups and {config.PRECURSOR_MIN_LIFT}x the usual pickup rate)")
        print("  extra menu items: 'Demo: score now', 'Demo: show nudge now'")
        print("=" * 60)
    lock = single_instance_lock(config.EVENTS_FILE)
    if lock is None:
        raise SystemExit(f"[loophole] another Loophole is already writing to {config.EVENTS_FILE}. "
                         "Quit it first (two copies would log every event and pickup twice).")
    print(f"[loophole] writing events to {config.EVENTS_FILE}")
    if config.API_KEY:
        print("[loophole] BehaviorGPT: using your own UNBOXAI_API_KEY")
    elif config.PROXY_URL:
        print(f"[loophole] BehaviorGPT: using the shared proxy {config.PROXY_URL}")
    else:
        print("[loophole] BehaviorGPT is off: no proxy and no API key, so 'Likely next' "
              "stays empty. Set LOOPHOLE_PROXY_URL or UNBOXAI_API_KEY in .env.")
    if config.NTFY_TOPIC:
        print(f"[loophole] phone link for the iPhone Shortcut: {phone_link()}")
    else:
        print("[loophole] no NTFY_TOPIC in .env, so phone pickups are off. Run: bash setup.sh")
    build_app(demo=args.demo, island=not args.no_island).run()


def single_instance_lock(events_file: Path):
    """Hold an exclusive lock next to the log for as long as this process runs.
    Returns the open lock file (keep a reference), or None if another process
    holds it. The OS drops the lock when the process dies, so a crash never
    leaves a stale lock."""
    import fcntl
    lock_path = Path(events_file).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = lock_path.open("w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


if __name__ == "__main__":
    main()
