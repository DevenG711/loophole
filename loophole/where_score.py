"""The "where" score: BehaviorGPT ranks the 60-item catalog given your last
HISTORY_LEN app/site/phone events, which says where you are heading.

Signals we return:
  rank   0-based position of the first distraction (d_*) item. Low = drifting.
         Coding is about 19, gateway apps (iMessage, Hacker News) about 7,
         a YouTube/social history is 0.
  share  fraction of d_* items in the top TOP_N.
  gap    mean z of d_* item scores minus mean z of f_* (z = per-ranking
         z-score). Work ~ -1.3, one Instagram ~ -0.7, all social ~ +0.8.
  pull   per d_* item: z now minus its mean z after plain work histories.
         The top one is "the app pulling at you" if >= WHERE_PULL_MIN_Z.

Public functions:
  to_items(records, t)          -> list of behaviorgpt View items
  score(records, t, client)     -> {"rank", "share", "top", "items_sent", ...}
  explain(top_ids)              -> friendly names for the UI
  cached_signal(records, t)     -> gap/pull_* from the cache only (no API)
  signal_from_specs(specs, c)   -> same, for specs you already have
  load_cache()                  -> the cache dict (re-read only on change)
"""
import hashlib
import json
import time
from pathlib import Path

from .catalog import ROW, is_distraction
from .config import (API_KEY, CATALOG_ID, DATA_DIR, HISTORY_LEN, HISTORY_MAX_AGE,
                     PHONE_HISTORY_MAX_AGE, RANK_LIMIT,
                     STICKY_DISTRACTIONS, TITLE_DOMAINS, TOP_N,
                     WHERE_PULL_MIN_Z, WHERE_REFERENCE_HISTORIES)

CACHE_FILE = DATA_DIR / "where_cache.json"
API_TIMEOUT_S = 10.0   # seconds to wait for one BehaviorGPT answer

# ---------------------------------------------------------------------------
# Mapping tables. Keys are lower-case.
# ---------------------------------------------------------------------------

# Mac app names (as shown in the menu bar).
APP_NAMES = {
    # focus
    "code": "f_vscode", "visual studio code": "f_vscode", "cursor": "f_vscode",
    "vscodium": "f_vscode", "zed": "f_vscode", "sublime text": "f_vscode",
    "terminal": "f_terminal", "iterm2": "f_terminal", "iterm": "f_terminal",
    "warp": "f_terminal", "ghostty": "f_terminal", "alacritty": "f_terminal",
    "kitty": "f_terminal", "wezterm": "f_terminal",
    "github desktop": "f_github",
    "xcode": "f_xcode", "simulator": "f_xcode",
    "pycharm": "f_pycharm", "pycharm ce": "f_pycharm", "intellij idea": "f_pycharm",
    "webstorm": "f_pycharm", "datagrip": "f_pycharm",
    "jupyterlab": "f_jupyter", "jupyter": "f_jupyter",
    "postman": "f_postman", "docker desktop": "f_docker", "docker": "f_docker",
    "notion": "f_notion", "obsidian": "f_notion", "notes": "f_notion",
    "microsoft word": "f_gdocs", "pages": "f_gdocs",
    "numbers": "f_gsheets", "microsoft excel": "f_excel",
    "figma": "f_figma", "linear": "f_linear",
    "chatgpt": "f_chatgpt", "claude": "f_claude",
    "calendar": "f_calendar", "fantastical": "f_calendar",
    "mail": "f_gmail", "microsoft outlook": "f_gmail", "spark": "f_gmail",
    "slack": "f_slack", "zoom.us": "f_zoom", "zoom": "f_zoom",
    "microsoft teams": "f_zoom",
    # distraction
    "messages": "d_imessage", "facetime": "d_imessage",
    "whatsapp": "d_whatsapp", "discord": "d_discord",
    "spotify": "d_spotify_pod", "podcasts": "d_spotify_pod",
    "steam": "d_steam", "netflix": "d_netflix", "robinhood": "d_robinhood",
    "chess": "d_chess",
}

# Mac bundle ids (more reliable than names when present).
BUNDLES = {
    "com.microsoft.vscode": "f_vscode", "com.todesktop.230313mzl4w4u92": "f_vscode",
    "com.apple.terminal": "f_terminal", "com.googlecode.iterm2": "f_terminal",
    "dev.warp.warp-stable": "f_terminal", "com.mitchellh.ghostty": "f_terminal",
    "com.github.githubclient": "f_github",
    "com.apple.dt.xcode": "f_xcode",
    "com.jetbrains.pycharm": "f_pycharm", "com.jetbrains.pycharm.ce": "f_pycharm",
    "com.postmanlabs.mac": "f_postman", "com.docker.docker": "f_docker",
    "notion.id": "f_notion", "md.obsidian": "f_notion",
    "com.figma.desktop": "f_figma", "com.linear": "f_linear",
    "com.openai.chat": "f_chatgpt", "com.anthropic.claudefordesktop": "f_claude",
    "com.apple.ical": "f_calendar", "com.apple.mail": "f_gmail",
    "com.tinyspeck.slackmacgap": "f_slack", "us.zoom.xos": "f_zoom",
    "com.microsoft.excel": "f_excel", "com.microsoft.word": "f_gdocs",
    "com.apple.mobilesms": "d_imessage", "net.whatsapp.whatsapp": "d_whatsapp",
    "com.hnc.discord": "d_discord", "com.spotify.client": "d_spotify_pod",
    "com.valvesoftware.steam": "d_steam",
}

# Web domains. Matching is by suffix and the longest match wins, so
# "console.aws.amazon.com" beats "amazon.com".
DOMAINS = {
    # focus
    "github.com": "f_github", "gist.github.com": "f_github", "gitlab.com": "f_github",
    "stackoverflow.com": "f_stackoverflow", "stackexchange.com": "f_stackoverflow",
    "docs.python.org": "f_pydocs", "python.org": "f_pydocs", "pypi.org": "f_pydocs",
    "developer.mozilla.org": "f_mdn",
    "atlassian.net": "f_jira", "linear.app": "f_linear",
    "notion.so": "f_notion", "notion.site": "f_notion",
    "docs.google.com": "f_gdocs", "sheets.google.com": "f_gsheets",
    "figma.com": "f_figma", "overleaf.com": "f_overleaf",
    "arxiv.org": "f_arxiv", "scholar.google.com": "f_scholar",
    "chatgpt.com": "f_chatgpt", "chat.openai.com": "f_chatgpt", "claude.ai": "f_claude",
    "console.aws.amazon.com": "f_aws", "aws.amazon.com": "f_aws",
    "postman.com": "f_postman", "hub.docker.com": "f_docker", "docs.docker.com": "f_docker",
    "colab.research.google.com": "f_jupyter",
    "instructure.com": "f_canvas", "khanacademy.org": "f_khan",
    "calendar.google.com": "f_calendar", "mail.google.com": "f_gmail",
    "outlook.office.com": "f_gmail", "outlook.cloud.microsoft": "f_gmail",
    "outlook.live.com": "f_gmail", "slack.com": "f_slack", "zoom.us": "f_zoom",
    # distraction
    "youtube.com": "d_youtube", "youtu.be": "d_youtube",
    "tiktok.com": "d_tiktok", "instagram.com": "d_instagram",
    "x.com": "d_twitter", "twitter.com": "d_twitter",
    "reddit.com": "d_reddit", "facebook.com": "d_facebook",
    "netflix.com": "d_netflix", "twitch.tv": "d_twitch", "hulu.com": "d_hulu",
    "open.spotify.com": "d_spotify_pod",
    "steampowered.com": "d_steam", "steamcommunity.com": "d_steam",
    "discord.com": "d_discord", "amazon.com": "d_amazon",
    "espn.com": "d_espn", "buzzfeed.com": "d_buzzfeed", "9gag.com": "d_9gag",
    "pinterest.com": "d_pinterest", "snapchat.com": "d_snapchat",
    "cnn.com": "d_news", "foxnews.com": "d_news", "bbc.com": "d_news",
    "bbc.co.uk": "d_news", "nytimes.com": "d_news",
    "web.whatsapp.com": "d_whatsapp", "linkedin.com": "d_linkedin",
    "chess.com": "d_chess", "lichess.org": "d_chess",
    "ebay.com": "d_ebay", "zillow.com": "d_zillow", "robinhood.com": "d_robinhood",
    "primevideo.com": "d_primevideo", "news.ycombinator.com": "d_hackernews",
}

# Phone app names (from the phone listener).
PHONE_APPS = {
    "instagram": "d_instagram", "tiktok": "d_tiktok",
    "youtube": "d_youtube_shorts", "x": "d_twitter", "twitter": "d_twitter",
    "reddit": "d_reddit", "snapchat": "d_snapchat",
    "facebook": "d_facebook", "whatsapp": "d_whatsapp", "messages": "d_imessage",
    "imessage": "d_imessage", "discord": "d_discord", "pinterest": "d_pinterest",
    "linkedin": "d_linkedin", "netflix": "d_netflix", "twitch": "d_twitch",
    "spotify": "d_spotify_pod", "robinhood": "d_robinhood", "chess": "d_chess",
}

# Apps that are not real user activity (screen lock, our own app).
IGNORE_APPS = {"loginwindow", "screensaverengine", "loophole", "usernotificationcenter",
               "finder", "system settings", "system preferences", "script editor"}

# Sites that are plumbing, not activity: our own phone listener, browser
# pages, sign-in screens. ("newtab" is kept: it often comes right before a
# distraction.)
IGNORE_DOMAINS = {"ntfy.sh", "history", "login.microsoft.com", "login.microsoftonline.com",
                  "accounts.google.com", "login.live.com"}

# Browsers: with no domain we can't tell what the page is, so skip them.
BROWSER_APPS = {"google chrome", "safari", "arc", "firefox", "brave browser", "microsoft edge"}


# ---------------------------------------------------------------------------
# Records -> items
#
# Internally each item is a small "spec" tuple, which is easy to hash and to
# show in the UI:
#   ("id", "f_vscode")                      a catalog item
#   ("ext", name, brand_or_None)            an ExternalItem
# ---------------------------------------------------------------------------

def _clean_domain(domain: str) -> str:
    d = domain.lower().split(":")[0]
    for prefix in ("www.", "m.", "mobile."):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d


def _match_domain(domain: str) -> str | None:
    """Longest table entry that the domain equals or ends with."""
    best = None
    for key in DOMAINS:
        if domain == key or domain.endswith("." + key):
            if best is None or len(key) > len(best):
                best = key
    return DOMAINS[best] if best else None


def _spec_for_app(rec: dict):
    """One `app` record -> spec, or None to skip it."""
    app = (rec.get("app") or "").strip()
    if app.lower() in IGNORE_APPS:
        return None
    domain = _clean_domain(rec.get("domain") or "")
    url = (rec.get("url") or "").lower()
    title = (rec.get("title") or "").strip()

    if domain in IGNORE_DOMAINS:
        return None
    if not domain and app.lower() in BROWSER_APPS:
        return None

    if domain:
        # YouTube / Reddit: the page title tells a tutorial from a prank video.
        title_domain = next((d for d in TITLE_DOMAINS if domain == d or domain.endswith("." + d)), None)
        if title_domain:
            if "/shorts" in url:
                return ("id", "d_youtube_shorts")
            if title and title.lower() not in ("youtube", "reddit", "reddit - the heart of the internet"):
                return ("ext", title, title_domain)
            return ("id", "d_youtube" if title_domain == "youtube.com" else "d_reddit")
        # A few sites need the URL path.
        if domain == "docs.google.com" and "/spreadsheets" in url:
            return ("id", "f_gsheets")
        if domain == "nytimes.com" and "/games/wordle" in url:
            return ("id", "d_wordle")
        item = _match_domain(domain)
        return ("id", item) if item else ("ext", domain, None)

    bundle = (rec.get("bundle") or "").lower()
    item = BUNDLES.get(bundle) or APP_NAMES.get(app.lower())
    if item:
        return ("id", item)
    return ("ext", app, None) if app else None


def _spec_for_phone(rec: dict):
    if rec.get("source") == "test":  # a test ping from phone setup, not a pickup
        return None
    app = (rec.get("app") or "").strip()
    if app.lower().startswith("app="):  # records saved before phone.py parsed Form bodies
        app = app[4:].strip()
    item = PHONE_APPS.get(app.lower())
    if item:
        return ("id", item)
    return ("ext", app, None) if app else None


def app_records(records: list[dict]) -> list[dict]:
    """Only laptop `app` records: the history the pickup memory's where_gap uses.
    Phone records are left out because any phone app in the history goes to #1,
    so where_gap would mostly say "you picked up in the last 5 min"."""
    return [r for r in records if r.get("kind") == "app"]


def _specs(records: list[dict], t: float) -> list[tuple]:
    """Last HISTORY_LEN specs from app/phone records in the HISTORY_MAX_AGE before t
    (plus the newest record, however old: it is the app you are in now).

    Repeats in a row (same app twice) are merged, so 8 slots hold 8 real moves.
    """
    recs = sorted((r for r in records if r["ts"] < t and (
                       r.get("kind") == "app"
                       or (r.get("kind") == "phone" and r["ts"] >= t - PHONE_HISTORY_MAX_AGE))),
                  key=lambda r: r["ts"])
    # Drop old events, but always keep the newest one: the tracker only writes
    # on a switch, so 20 min in one app means that app is still where you are.
    recs = [r for i, r in enumerate(recs)
            if r["ts"] >= t - HISTORY_MAX_AGE or i == len(recs) - 1]
    specs = []
    for r in recs:
        spec = _spec_for_app(r) if r["kind"] == "app" else _spec_for_phone(r)
        if spec and (not specs or specs[-1] != spec):
            specs.append(spec)
    return specs[-HISTORY_LEN:]


def _to_view(spec):
    from behaviorgpt import ExternalItem, View
    if spec[0] == "id":
        return View(spec[1])
    name, brand = spec[1], spec[2]
    return View(ExternalItem(name, brand=brand) if brand else ExternalItem(name))


def to_items(records: list[dict], t: float) -> list:
    """The History items (View objects) sent to BehaviorGPT for time t."""
    return [_to_view(s) for s in _specs(records, t)]


def _label(spec) -> str:
    """Readable text for one spec: "f_vscode" or "ext:Some title (youtube.com)"."""
    if spec[0] == "id":
        return spec[1]
    return f"ext:{spec[1]}" + (f" ({spec[2]})" if spec[2] else "")


# ---------------------------------------------------------------------------
# Disk cache: history hash -> {id: score} (in API order).
# Old entries are plain ranked id lists: rank/share/top still work from
# them, but gap/pull need scores, so those come back None.
# REFERENCE_KEY holds the mean z-vector after plain work histories.
# ---------------------------------------------------------------------------

REFERENCE_KEY = "__reference_v1__"
REFERENCE_RETRY_S = 300   # after a failed reference fetch, wait this long

def _cache_key(specs) -> str:
    raw = json.dumps([CATALOG_ID, RANK_LIMIT, [list(s) for s in specs]])
    return hashlib.sha1(raw.encode()).hexdigest()


def _load_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


_memo: dict = {}   # str(path) -> ((mtime_ns, size), cache)


def load_cache(path: Path | None = None) -> dict:
    """The cache file as a dict, re-read only when the file changes.
    Treat the result as read-only (it is shared between callers)."""
    path = Path(path or CACHE_FILE)
    try:
        st = path.stat()
    except OSError:
        return {}
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _memo.get(str(path))
    if hit and hit[0] == stamp:
        return hit[1]
    cache = _load_cache(path)
    _memo[str(path)] = (stamp, cache)
    return cache


def _save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache))
    tmp.replace(path)


_client = None


def _default_client():
    """One shared client, made on first use. None if there is no API key."""
    global _client
    if _client is None and API_KEY:
        from behaviorgpt import UnboxAIClient
        # The default read timeout is 120 s, which would freeze scoring (one
        # tick every 30 s). A slow answer is a missed tick, not a hang.
        _client = UnboxAIClient(api_key=API_KEY, market="us", http_timeout=API_TIMEOUT_S)
    return _client


def _ids(entry) -> list[str]:
    """Ranked ids from a cache entry (old list or new {id: score} dict)."""
    return list(entry) if isinstance(entry, (list, dict)) else []


def _summarise(ids: list[str], skip_sticky: bool = True) -> dict:
    # skip_sticky: skip the items BehaviorGPT ranks high for any history (they
    # are noise). This was tuned on our own data after PROTOCOL.md was written;
    # skip_sticky=False is the pre-registered rule (first d_* item, any).
    sticky = STICKY_DISTRACTIONS if skip_sticky else ()
    rank = next((i for i, x in enumerate(ids)
                 if is_distraction(x) and x not in sticky), len(ids))
    top = ids[:TOP_N]
    share = sum(1 for x in top if is_distraction(x)) / TOP_N
    return {"rank": rank, "share": round(share, 3), "top": ids[:3]}


# ---------------------------------------------------------------------------
# Pull signals from item scores (see config.WHERE_PULL_MIN_Z).
# ---------------------------------------------------------------------------

def _zscores(scores: dict) -> dict | None:
    """{id: z} for one ranking, or None if it can't be z-scored."""
    if not isinstance(scores, dict) or len(scores) < 2:
        return None
    vals = [float(v) for v in scores.values()]
    mean = sum(vals) / len(vals)
    sd = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
    if sd < 1e-9:
        return None
    return {k: (float(v) - mean) / sd for k, v in scores.items()}


def _gap(z: dict) -> float | None:
    d = [v for k, v in z.items() if is_distraction(k)]
    f = [v for k, v in z.items() if k.startswith("f_")]
    if not d or not f:
        return None
    return round(sum(d) / len(d) - sum(f) / len(f), 4)


def _reference(cache: dict) -> dict | None:
    ref = cache.get(REFERENCE_KEY)
    if isinstance(ref, dict) and ref.get("catalog") == CATALOG_ID and isinstance(ref.get("z"), dict):
        return ref["z"]
    return None


EMPTY_SIGNAL = {"gap": None, "pull_app": None, "pull_name": None, "pull_z": None, "pulls": []}


def _signal(entry, ref: dict | None) -> dict:
    """gap / pull_* from one cache entry and the reference z-vector."""
    out = dict(EMPTY_SIGNAL, pulls=[])
    z = _zscores(entry)
    if z is None:
        return out
    out["gap"] = _gap(z)
    if ref:
        pulls = sorted(((k, round(v - ref[k], 3)) for k, v in z.items()
                        if is_distraction(k) and k in ref),
                       key=lambda p: -p[1])
        out["pulls"] = pulls[:3]
        if pulls:
            out["pull_z"] = pulls[0][1]
            if pulls[0][1] >= WHERE_PULL_MIN_Z:
                out["pull_app"] = pulls[0][0]
                out["pull_name"] = explain([pulls[0][0]])[0]
    return out


def signal_from_specs(specs, cache: dict) -> dict | None:
    """gap / pull_* for a history (specs) from the cache dict alone (no API).
    None if this history isn't cached with scores."""
    if not specs:
        return None
    entry = cache.get(_cache_key(specs))
    if not isinstance(entry, dict) or _zscores(entry) is None:
        return None
    return _signal(entry, _reference(cache))


def cached_signal(records: list[dict], t: float, cache: dict | None = None,
                  cache_path: Path | None = None) -> dict | None:
    """Like score()'s gap/pull_* keys, for a past moment, from the cache ONLY.
    Never calls the API. Pass `cache` (from load_cache()) when calling often."""
    if cache is None:
        cache = load_cache(cache_path)
    return signal_from_specs(_specs(records, t), cache)


def _ask(client, specs) -> dict:
    """One API call -> {id: score} in API order. Raises on failure."""
    resp = client.complete([_to_view(s) for s in specs], limit=RANK_LIMIT,
                           catalog_id=CATALOG_ID, register_event=False)
    scores = {}
    for item in resp.items:
        if item.id not in scores:
            s = getattr(item, "score", None)
            scores[item.id] = round(float(s), 6) if s is not None else None
    if not scores:
        raise RuntimeError("empty response")
    return scores


_ref_failed: dict = {}   # str(cache_path) -> time of last failed fetch


def _ensure_reference(cache: dict, client, cache_path: Path) -> dict | None:
    """The reference z-vector: from the cache, else 4 API calls (once).
    None if unavailable. Adds it to `cache` (caller saves)."""
    ref = _reference(cache)
    if ref is not None:
        return ref
    if time.time() - _ref_failed.get(str(cache_path), 0) < REFERENCE_RETRY_S:
        return None
    try:
        client = client or _default_client()
        if client is None:
            raise RuntimeError("no API key")
        zs = []
        for hist in WHERE_REFERENCE_HISTORIES:
            z = _zscores(_ask(client, [("id", i) for i in hist]))
            if z is None:
                raise RuntimeError("reference has no scores")
            zs.append(z)
    except Exception:
        _ref_failed[str(cache_path)] = time.time()
        return None
    common = set.intersection(*(set(z) for z in zs))
    ref = {k: round(sum(z[k] for z in zs) / len(zs), 4) for k in common}
    cache[REFERENCE_KEY] = {"catalog": CATALOG_ID, "histories": WHERE_REFERENCE_HISTORIES, "z": ref}
    return ref


def score(records: list[dict], t: float, client=None, cache_path: Path | None = None,
          skip_sticky: bool = True, upgrade: bool = False) -> dict:
    """Ask BehaviorGPT where you are heading. Never raises.

    skip_sticky=True is the live (tuned) rank; False is the pre-registered one.
    The cache stores the item scores in ranked order, so both come from one entry.
    upgrade=True re-asks the API for old cache entries that have no scores.

    Returns {"rank", "share", "top", "items_sent", "source", "error",
             "gap", "pull_app", "pull_name", "pull_z", "pulls"}.
    source is "cache", "api" or "none". On failure rank and share are None;
    gap/pull_* are None when scores or the reference are unavailable.
    """
    specs = _specs(records, t)
    out = {"rank": None, "share": None, "top": [], "items_sent": [_label(s) for s in specs],
           "source": "none", "error": None, **EMPTY_SIGNAL, "pulls": []}
    if not specs:
        out["error"] = "no app history yet"
        return out

    cache_path = cache_path or CACHE_FILE   # read at call time so tests can swap it
    key = _cache_key(specs)
    # Re-parse the file only when it changed (it grows; parsing it every tick
    # adds up). Copy, because load_cache's dict is shared and we may add to it.
    cache = dict(load_cache(cache_path))
    entry = cache.get(key)
    if entry is not None and not (upgrade and isinstance(entry, list)):
        source = "cache"
    else:
        try:
            client = client or _default_client()
            if client is None:
                raise RuntimeError("no API key")
            entry = _ask(client, specs)
        except Exception as e:  # API down, bad key, network: fall back, don't crash
            if entry is not None:   # old list entry: still usable
                return {**out, **_summarise(_ids(entry), skip_sticky), "source": "cache"}
            out["error"] = f"{type(e).__name__}: {e}"
            return out
        cache[key] = entry
        source = "api"

    ref = None
    had_ref = REFERENCE_KEY in cache
    if _zscores(entry) is not None:   # pull needs scores; skip the reference otherwise
        try:
            ref = _ensure_reference(cache, client, cache_path)
        except Exception:
            ref = None
    if source == "api" or (REFERENCE_KEY in cache and not had_ref):
        try:
            _save_cache(cache_path, cache)
        except OSError:
            pass
    return {**out, **_summarise(_ids(entry), skip_sticky), **_signal(entry, ref),
            "source": source}


def explain(top_ids: list[str]) -> list[str]:
    """Friendly names for catalog ids, e.g. "YouTube Shorts"."""
    return [ROW[i][0] if i in ROW else i for i in top_ids]
