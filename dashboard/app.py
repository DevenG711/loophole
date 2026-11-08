"""Loophole dashboard (Streamlit).

Run:     .venv/bin/streamlit run dashboard/app.py
Deploy:  Streamlit Community Cloud, main file = dashboard/app.py (see docs/run.md)

Reads a Loophole events file (JSON lines, format in loophole/events.py)
and draws one session timeline:
  - app strip: green = work, red = distraction
  - "when" line: how stalled you are (engine stall score)
  - "where" line: how close BehaviorGPT thinks you are to drifting
    (closeness = 1 when a distraction is the #1 prediction, 0 when none is
    in the top RANK_LIMIT)
  - orange rules = nudges, purple triangles = phone pickups
  - pickup memory over time (the only nudge trigger since 2026-11-08): how
    many real pickups it has learned from, and the "heads-up" moments where
    it had text (red = nudged, amber = blocked by cooldown / just back)
  - BehaviorGPT pull: where_gap (lean to distraction, z units) and the app
    pulling at you (pull_app), when the score records carry them
Old records without the memory / pull fields still render (blank values).

Thresholds, app lists and catalog names come from the loophole package when
it imports (needs python-dotenv). If it doesn't (e.g. a bare Streamlit Cloud
install), copied fallbacks below keep the page working.
The data helpers are plain functions so tests can import them; the page is
only drawn when Streamlit runs this file as __main__.
"""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_FILE = Path(__file__).resolve().parent / "sample_events.jsonl"
RESULTS_GLOB = str(ROOT / "evaluation" / "results" / "*.json")
# Where to look for events files. Override with LOOPHOLE_DATA_DIR=/some/dir.
DATA_DIR = Path(os.getenv("LOOPHOLE_DATA_DIR", ROOT / "data"))

if str(ROOT) not in sys.path:  # so `loophole` imports when run by streamlit
    sys.path.insert(0, str(ROOT))

# Thresholds: from loophole/config.py when it imports, else these copies.
try:
    from loophole import config as _config
    RANK_LIMIT = _config.RANK_LIMIT
    RANK_THRESHOLD = _config.RANK_THRESHOLD          # eval / amber line
    STALL_THRESHOLD = _config.STALL_THRESHOLD
    NUDGE_RANK_THRESHOLD = getattr(_config, "NUDGE_RANK_THRESHOLD", 3)  # live nudge
except Exception:  # no python-dotenv etc.
    RANK_LIMIT = 60
    RANK_THRESHOLD = 10
    STALL_THRESHOLD = 1.0
    NUDGE_RANK_THRESHOLD = 3

# Catalog id -> friendly name ("d_ytshorts" -> "YouTube Shorts").
try:
    from loophole.where_score import explain as _explain
except Exception:
    try:
        from loophole.catalog import ROW as _ROW

        def _explain(ids):
            return [_ROW[i][0] if i in _ROW else i for i in ids]
    except Exception:
        def _explain(ids):
            return list(ids)

# Sites and apps that count as distractions / neutral. The source of truth is
# loophole/menubar.py (its helpers import without rumps). The copies below are
# an exact copy of menubar's lists, used only if that import fails.
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
# Copy of loophole/menubar.py NEUTRAL_APPS.
NEUTRAL_APPS = {
    "finder", "loginwindow", "python", "python3", "loophole",
    "screensaverengine", "usernotificationcenter", "system settings",
    "notification center", "dock", "spotlight",
}
try:
    from loophole import menubar as _menubar
    DISTRACTION_DOMAINS = tuple(_menubar.DISTRACTION_DOMAINS)
    DISTRACTION_APPS = tuple(_menubar.DISTRACTION_APPS)
    NEUTRAL_APPS = set(_menubar.NEUTRAL_APPS)
except Exception:
    pass

# Pickup memory (loophole/precursor.py). Guarded: the page must load without it.
try:
    from loophole import precursor as _precursor
except Exception:  # missing module, no python-dotenv, etc.
    _precursor = None

SYNTHETIC_BANNER = "SYNTHETIC DATA: not real results"
ALL_SESSIONS = "All sessions"

KIND_COLOURS = {"work": "#2e9e5b", "distraction": "#d64545", "neutral": "#b0b0b0"}


# ---------------------------------------------------------------------------
# Data helpers (pure; tested in tests/test_ui.py)
# ---------------------------------------------------------------------------

def parse_jsonl(text: str) -> list[dict]:
    """Lines of JSON -> list of records. Skips blank and broken lines."""
    records = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and "ts" in rec and "kind" in rec:
            records.append(rec)
    records.sort(key=lambda r: r["ts"])
    return records


def app_kind(rec: dict) -> str:
    """'work', 'distraction' or 'neutral' for an `app` record."""
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


def is_synthetic(name: str | None, records: list[dict] | None = None) -> bool:
    """True for fake/sample files (by name) or records marked synthetic by
    evaluation/fake_data.py. Such data must never be shown as results."""
    n = (name or "").lower()
    if "fake" in n or "sample" in n:
        return True
    return any(r.get("synthetic") for r in (records or []))


def clean_phone_app(name) -> str:
    """'app=snapchat' -> 'Snapchat'. Old Shortcut pings kept the 'app=' prefix."""
    s = str(name or "").strip()
    if s.lower().startswith("app="):
        s = s[4:].strip()
    return s.title() if s else "?"


def is_test_phone(rec: dict) -> bool:
    """Test pings (source == 'test') are not real pickups."""
    return rec.get("kind") == "phone" and rec.get("source") == "test"


def is_staged(rec: dict) -> bool:
    """Nudges staged for the demo carry `staged: true`."""
    return bool(rec.get("staged"))


def top_names(ids) -> list[str]:
    """BehaviorGPT top item ids -> friendly names."""
    if not isinstance(ids, (list, tuple)):
        return []
    return _explain([str(i) for i in ids])


def closeness(rank) -> float:
    """BehaviorGPT rank of the first distraction -> 0..1, higher = closer to
    drifting. Rank 1 -> 1.0, rank RANK_LIMIT -> ~0, no distraction -> 0."""
    if rank is None or pd.isna(rank):
        return 0.0
    rank = max(1, int(rank))
    return max(0.0, 1 - (rank - 1) / RANK_LIMIT)


def split_sessions(records: list[dict]) -> list[list[dict]]:
    """Cut records into sessions using `session` start/stop records.

    A session runs from its start record to its stop record. Older logs have
    no stop records, so a session without one ends at the next start (or at
    the last record of the file). Records outside any session are dropped.
    If the file has no session records at all, the whole file is one session.
    """
    if not any(r["kind"] == "session" for r in records):
        return [records] if records else []
    sessions, current = [], None
    for r in records:
        if r["kind"] == "session" and r.get("action") == "start":
            if current:
                sessions.append(current)
            current = [r]
        elif current is not None:
            current.append(r)
            if r["kind"] == "session" and r.get("action") == "stop":
                sessions.append(current)
                current = None
    if current:
        sessions.append(current)
    return sessions


def session_end_reason(session: list[dict]) -> str:
    """How a session ended: 'stop', or 'no stop record' (cut at the next
    start or the end of the log)."""
    if session and session[-1]["kind"] == "session" and session[-1].get("action") == "stop":
        return "stop"
    return "no stop record"


SCORE_EXTRA_COLS = ["colour", "where_gap", "pull_app", "pull_z", "mem_n", "mem_ready",
                    "mem_text", "mem_lift", "mem_app", "heads_up"]


def score_extras(rec: dict) -> dict:
    """Pickup-memory and BehaviorGPT-pull fields of one score record.
    Old records (before 2026-11-08) have none of them: values are None/False."""
    mem = rec.get("memory") if isinstance(rec.get("memory"), dict) else {}
    pull = rec.get("pull_app")
    text = mem.get("text") or None
    return {"colour": rec.get("colour"),
            "where_gap": _num(rec.get("where_gap")),
            "pull_app": top_names([pull])[0] if pull else None,
            "pull_z": _num(rec.get("pull_z")),
            "mem_n": _num(mem.get("n_episodes")),
            "mem_ready": bool(mem.get("ready")),
            "mem_text": text,
            "mem_lift": _num(mem.get("lift")),
            "mem_app": mem.get("likely_app"),
            "heads_up": bool(text)}


def memory_status(scores: pd.DataFrame) -> dict:
    """Headline numbers for the pickup memory in one view of score records.
    has_memory is False for old logs whose score records carry no memory."""
    if scores.empty or "mem_n" not in scores or scores["mem_n"].isna().all():
        return {"has_memory": False, "n_episodes": None, "ready": False,
                "heads_up": 0, "nudged": 0, "blocked": 0}
    last = scores[scores["mem_n"].notna()].iloc[-1]
    hu = scores[scores["heads_up"]]
    return {"has_memory": True, "n_episodes": int(last["mem_n"]), "ready": bool(last["mem_ready"]),
            "heads_up": len(hu), "nudged": int(hu["nudge"].sum()),
            "blocked": int((~hu["nudge"]).sum())}


def build_frames(records: list[dict]) -> dict[str, pd.DataFrame]:
    """Records of one session -> DataFrames for the timeline.

    apps:   start, end, app, label, kind   (one row per app/tab segment)
    scores: time, when, where_rank, closeness, nudge, reason, top, top_ids
    nudges: time, action, staged
    phones: time, app, source            (test pings left out)
    """
    def t(ts):
        return pd.to_datetime(ts, unit="s")

    end_ts = records[-1]["ts"] if records else 0
    app_recs = [r for r in records if r["kind"] == "app"]
    apps = []
    for i, r in enumerate(app_recs):
        nxt = app_recs[i + 1]["ts"] if i + 1 < len(app_recs) else end_ts
        label = r.get("domain") or r.get("app") or "?"
        apps.append({"start": t(r["ts"]), "end": t(max(nxt, r["ts"] + 1)),
                     "app": r.get("app") or "?", "label": label, "kind": app_kind(r),
                     "seconds": max(0.0, nxt - r["ts"])})
    scores = [{"time": t(r["ts"]), "when": r.get("when"),
               "where_rank": r.get("where_rank"),
               "closeness": closeness(r.get("where_rank")),
               "nudge": bool(r.get("nudge")), "reason": r.get("reason") or "",
               "top": ", ".join(top_names(r.get("top"))) or "-",
               "top_ids": list(r.get("top") or []),
               **score_extras(r)}
              for r in records if r["kind"] == "score"]
    nudges = [{"time": t(r["ts"]), "action": r.get("action", "?"), "staged": is_staged(r)}
              for r in records if r["kind"] == "nudge"]
    phones = [{"time": t(r["ts"]), "app": clean_phone_app(r.get("app")),
               "source": r.get("source") or "?"}
              for r in records if r["kind"] == "phone" and not is_test_phone(r)]
    return {
        "apps": pd.DataFrame(apps, columns=["start", "end", "app", "label", "kind", "seconds"]),
        "scores": pd.DataFrame(scores, columns=["time", "when", "where_rank", "closeness", "nudge",
                                                "reason", "top", "top_ids", *SCORE_EXTRA_COLS]),
        "nudges": pd.DataFrame(nudges, columns=["time", "action", "staged"]).astype({"staged": bool}),
        "phones": pd.DataFrame(phones, columns=["time", "app", "source"]),
    }


def session_summary(records: list[dict]) -> dict:
    """Headline numbers for one session."""
    f = build_frames(records)
    apps = f["apps"]
    work = apps.loc[apps["kind"] == "work", "seconds"].sum()
    distr = apps.loc[apps["kind"] == "distraction", "seconds"].sum()
    tracked = work + distr
    start = next((r for r in records if r["kind"] == "session" and r.get("action") == "start"), None)
    real = f["nudges"][~f["nudges"]["staged"]]  # demo stagings don't count
    actions = real["action"].value_counts().to_dict()
    return {
        "start": pd.to_datetime(records[0]["ts"], unit="s") if records else None,
        "planned_min": start.get("minutes") if start else None,
        "ended": session_end_reason(records),
        "length_min": round((records[-1]["ts"] - records[0]["ts"]) / 60, 1) if records else 0,
        "work_share": round(work / tracked, 2) if tracked else None,
        "distraction_min": round(distr / 60, 1),
        "nudges": int(actions.get("shown", 0)),
        "breaks_taken": int(actions.get("break", 0)),
        "dismissed": int(actions.get("dismiss", 0)),
        "staged_nudges": int(((f["nudges"]["action"] == "shown") & f["nudges"]["staged"]).sum()),
        "phone_pickups": len(f["phones"]),
        "max_stall": round(float(f["scores"]["when"].max()), 2)
        if f["scores"]["when"].notna().any() else None,
    }


def _num(x):
    """float(x), or None for missing / NaN / non-numbers."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _dur(seconds: float) -> str:
    """75 -> '1 min', 30 -> '30 s'."""
    return f"{seconds / 60:.0f} min" if seconds >= 60 else f"{seconds:.0f} s"


def describe_snapshot(f: dict | None) -> str:
    """Snapshot features before a pickup -> one readable line, e.g.
    'on one window 3 min, typing +20/min vs before, 3 switches, last pickup 8 min ago'.
    Missing features are left out."""
    f = f or {}
    parts = []
    dwell = _num(f.get("dwell_s"))
    if dwell is not None:
        parts.append(f"on one window {_dur(dwell)}")
    kt = _num(f.get("key_trend"))
    if kt is not None:
        parts.append(f"typing {kt:+.0f}/min vs before")
    st_ = _num(f.get("scroll_trend"))
    if st_ is not None and round(st_) != 0:
        parts.append(f"scrolling {st_:+.0f}/min vs before")
    sw = _num(f.get("switches_5m"))
    if sw is not None:
        n = int(round(sw))
        parts.append(f"{n} switch{'es' if n != 1 else ''}")
    idle = _num(f.get("idle_s"))
    if idle is not None and idle >= 10:
        parts.append(f"idle {_dur(idle)}")
    stall = _num(f.get("stall"))
    if stall is not None:
        parts.append(f"stall {stall:.1f}" + (" (high)" if stall >= STALL_THRESHOLD else ""))
    pull, pz = f.get("pull_app"), _num(f.get("pull_z"))
    if pull:
        parts.append(f"BehaviorGPT pull: {pull}" + (f" ({pz:+.1f}σ)" if pz is not None else ""))
    since = _num(f.get("min_since_pickup"))
    parts.append(f"last pickup {since:.0f} min ago" if since is not None else "no earlier pickup")
    return ", ".join(parts)


def precursor_rows(records: list[dict]) -> list[dict]:
    """One row per real pickup episode in `records`: time, app opened, and what
    the user was doing just before (precursor.episodes). Test pings are left
    out. Returns [] if loophole.precursor is not available or fails."""
    if _precursor is None or not records:
        return []
    clean = [r for r in records if not is_test_phone(r)]
    try:
        eps = _precursor.episodes(clean, clean[-1]["ts"] + 1)
    except Exception:
        return []
    rows = []
    for e in eps:
        f = e.get("features") or {}
        rows.append({"time": pd.to_datetime(e["ts"], unit="s"),
                     "app": str(e.get("app") or "?").removeprefix("app=").strip() or "?",
                     "before": describe_snapshot(f),
                     **{k: f.get(k) for k in getattr(_precursor, "SNAPSHOT_FEATURES", [])}})
    return rows


def available_files() -> dict[str, Path]:
    """Label -> path for every events file we can find."""
    files = {}
    for p in sorted(glob.glob(str(DATA_DIR / "*.jsonl"))):
        files[Path(p).name] = Path(p)
    if SAMPLE_FILE.exists():
        files["Sample data (dashboard/sample_events.jsonl)"] = SAMPLE_FILE
    return files


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def timeline_chart(frames: dict[str, pd.DataFrame], synthetic: bool = False):
    """App strip + when line + where line, sharing one time axis."""
    import altair as alt

    tag = " (synthetic)" if synthetic else ""
    apps, scores = frames["apps"], frames["scores"].drop(columns=["top_ids"])
    nudges = frames["nudges"]
    shown = nudges[(nudges["action"] == "shown") & ~nudges["staged"]]
    staged = nudges[(nudges["action"] == "shown") & nudges["staged"]]
    phones = frames["phones"]
    width = "container"

    colour = alt.Color("kind:N", title="App type",
                       scale=alt.Scale(domain=list(KIND_COLOURS), range=list(KIND_COLOURS.values())))
    strip = alt.Chart(apps).mark_rect().encode(
        x=alt.X("start:T", title=None), x2="end:T", color=colour,
        tooltip=["app", "label", "kind", alt.Tooltip("start:T", format="%H:%M:%S")],
    ).properties(height=50, width=width, title="Apps (green = work, red = distraction)" + tag)
    if not phones.empty:
        strip += alt.Chart(phones).mark_point(shape="triangle-down", size=150, filled=True,
                                              color="#7b3fe4").encode(
            x="time:T", tooltip=["app", "source", alt.Tooltip("time:T", format="%H:%M:%S")])

    def flags():
        """Orange rules where a real nudge was shown; dashed grey = staged (demo)."""
        layer = None
        if not shown.empty:
            layer = alt.Chart(shown).mark_rule(color="orange", strokeWidth=2).encode(
                x="time:T", tooltip=[alt.Tooltip("time:T", title="nudge", format="%H:%M:%S")])
        if not staged.empty:
            s = alt.Chart(staged).mark_rule(color="grey", strokeWidth=2, strokeDash=[2, 2]).encode(
                x="time:T", tooltip=[alt.Tooltip("time:T", title="staged nudge (demo)",
                                                 format="%H:%M:%S")])
            layer = s if layer is None else layer + s
        return layer

    when = alt.Chart(scores).mark_line(point=True, color="#1f77b4").encode(
        x=alt.X("time:T", title=None), y=alt.Y("when:Q", title="stall"),
        tooltip=["when", "reason", alt.Tooltip("time:T", format="%H:%M:%S")],
    ).properties(height=150, width=width, title="When: stall score (higher = slowing down)" + tag)
    when += alt.Chart(pd.DataFrame({"y": [STALL_THRESHOLD]})).mark_rule(
        strokeDash=[4, 4], color="grey").encode(y="y:Q")

    where = alt.Chart(scores).mark_line(point=True, color="#d64545").encode(
        x=alt.X("time:T", title="time"),
        y=alt.Y("closeness:Q", title="closeness", scale=alt.Scale(domain=[0, 1])),
        tooltip=["where_rank", "closeness",
                 alt.Tooltip("top:N", title="BehaviorGPT top 3"),
                 alt.Tooltip("time:T", format="%H:%M:%S")],
    ).properties(height=150, width=width,
                 title="Where: closeness to drifting (from BehaviorGPT rank; higher = closer)" + tag)
    where += alt.Chart(pd.DataFrame({"y": [closeness(RANK_THRESHOLD)]})).mark_rule(
        strokeDash=[4, 4], color="grey").encode(y="y:Q")

    if not shown.empty or not staged.empty:
        strip, when, where = strip + flags(), when + flags(), where + flags()
    return alt.vconcat(strip, when, where).resolve_scale(x="shared", color="independent")


def memory_chart(frames: dict[str, pd.DataFrame], synthetic: bool = False):
    """Pickup memory over time: real pickups learned (step line), heads-up
    moments (memory had text; red = nudged, amber = blocked), purple ticks =
    pickups. None if no score record carries a memory."""
    import altair as alt

    scores = frames["scores"].drop(columns=["top_ids"])
    have = scores[scores["mem_n"].notna()]
    if have.empty:
        return None
    tag = " (synthetic)" if synthetic else ""
    base = alt.Chart(have).mark_line(interpolate="step-after", color="#555").encode(
        x=alt.X("time:T", title=None),
        y=alt.Y("mem_n:Q", title="pickups learned"),
        tooltip=[alt.Tooltip("mem_n:Q", title="pickups learned"), "mem_ready",
                 alt.Tooltip("time:T", format="%H:%M:%S")],
    ).properties(height=130, width="container",
                 title="Pickup memory: pickups learned and heads-up moments" + tag)
    hu = have[have["heads_up"]].assign(
        status=lambda d: d["nudge"].map({True: "nudged", False: "blocked (cooldown / away)"}))
    if not hu.empty:
        base += alt.Chart(hu).mark_point(size=120, filled=True).encode(
            x="time:T", y="mem_n:Q",
            color=alt.Color("status:N", title="Heads-up",
                            scale=alt.Scale(domain=["nudged", "blocked (cooldown / away)"],
                                            range=["#d64545", "#e8a33d"])),
            tooltip=["status", alt.Tooltip("mem_text:N", title="memory said"),
                     alt.Tooltip("mem_lift:Q", title="lift"),
                     alt.Tooltip("mem_app:N", title="likely app"),
                     alt.Tooltip("time:T", format="%H:%M:%S")])
    phones = frames["phones"]
    if not phones.empty:
        base += alt.Chart(phones).mark_rule(color="#7b3fe4", strokeDash=[2, 2]).encode(
            x="time:T", tooltip=["app", alt.Tooltip("time:T", format="%H:%M:%S")])
    return base


def pull_chart(frames: dict[str, pd.DataFrame], synthetic: bool = False):
    """BehaviorGPT pull over time: where_gap line (lean to distraction, z units)
    with the pulling app as a label. None if no score record has where_gap."""
    import altair as alt

    scores = frames["scores"].drop(columns=["top_ids"])
    have = scores[scores["where_gap"].notna()]
    if have.empty:
        return None
    tag = " (synthetic)" if synthetic else ""
    chart = alt.Chart(have).mark_line(point=True, color="#8a5a00").encode(
        x=alt.X("time:T", title="time"),
        y=alt.Y("where_gap:Q", title="where_gap (z)"),
        tooltip=[alt.Tooltip("where_gap:Q", format=".2f"), "pull_app",
                 alt.Tooltip("pull_z:Q", format="+.1f"),
                 alt.Tooltip("time:T", format="%H:%M:%S")],
    ).properties(height=150, width="container",
                 title="BehaviorGPT pull: lean to distraction (higher = pulled harder)" + tag)
    chart += alt.Chart(pd.DataFrame({"y": [0.0]})).mark_rule(
        strokeDash=[4, 4], color="grey").encode(y="y:Q")
    labelled = have[have["pull_app"].notna()]
    if not labelled.empty:
        chart += alt.Chart(labelled).mark_text(dy=-10, fontSize=10, color="#8a5a00").encode(
            x="time:T", y="where_gap:Q", text="pull_app:N")
    return chart


def memory_section(st, frames: dict[str, pd.DataFrame], synthetic: bool = False,
                   tag: str = "") -> None:
    """Pickup memory status + BehaviorGPT pull for the chosen session(s)."""
    st.subheader("Pickup memory (the nudge trigger)" + tag)
    scores = frames["scores"]
    m = memory_status(scores)
    if not m["has_memory"]:
        st.caption("These score records have no pickup-memory fields (logged before "
                   "2026-11-08, or the memory failed). Nothing to show.")
    else:
        c = st.columns(4)
        c[0].metric("Pickups learned", m["n_episodes"])
        c[1].metric("Ready", "yes" if m["ready"] else "still learning")
        c[2].metric("Heads-up moments", m["heads_up"])
        c[3].metric("Nudged / blocked", f"{m['nudged']} / {m['blocked']}")
        chart = memory_chart(frames, synthetic)
        if chart is not None:
            st.altair_chart(chart, width="stretch")
        hu = scores[scores["heads_up"]]
        if not hu.empty:
            with st.expander("What the memory said"):
                st.dataframe(hu[["time", "colour", "nudge", "mem_lift", "mem_app", "mem_text"]],
                             hide_index=True)
    chart = pull_chart(frames, synthetic)
    if chart is None:
        st.caption("No BehaviorGPT pull (where_gap) in these score records.")
    else:
        st.altair_chart(chart, width="stretch")
        st.caption("where_gap = BehaviorGPT's mean distraction score minus mean focus score, "
                   "in z units. Labels = the app pulling hardest vs a fixed reference, when "
                   "it is strong enough.")


def where_readout(st, scores: pd.DataFrame, tag: str = "") -> None:
    """BehaviorGPT's top 3 for one scored moment (default: the latest)."""
    have = scores[scores["top_ids"].map(len) > 0]
    st.markdown(f"**Where BehaviorGPT thinks you're heading**{tag}")
    if have.empty:
        st.caption("No BehaviorGPT predictions in this view.")
        return
    labels = [t.strftime("%H:%M:%S") for t in have["time"]]
    if len(labels) > 1:
        i = labels.index(st.select_slider("Moment", options=labels, value=labels[-1]))
    else:
        i = 0
    row = have.iloc[i]
    names = top_names(row["top_ids"])
    st.write("  \n".join(f"{n}. {name}" for n, name in enumerate(names, 1)))
    rank = row["where_rank"]
    st.caption(f"At {labels[i]}: first time-waster at rank "
               f"{int(rank) if pd.notna(rank) else 'none in range'}.")


def precursor_section(st, records: list[dict], tag: str = "") -> None:
    """'What came before each pickup': the pickup memory's snapshots."""
    st.subheader("What came before each pickup" + tag)
    if _precursor is None:
        st.caption("Pickup memory is not available (loophole.precursor did not import).")
        return
    rows = precursor_rows(records)
    if not rows:
        st.caption("No real phone pickups in this view (test pings are left out).")
        return
    st.caption("Each pickup, and what you were doing about a minute before it. "
               "Small numbers from one person: a pattern to check, not a result.")
    df = pd.DataFrame(rows)
    main_cols = ["time", "app", "before"] + [c for c in ("stall", "where_gap", "pull_app", "pull_z")
                                             if c in df.columns]
    st.dataframe(df[main_cols], hide_index=True)
    with st.expander("All snapshot numbers"):
        st.dataframe(df, hide_index=True)


def privacy_note(st) -> None:
    st.markdown("""
**What leaves your laptop**

- **To BehaviorGPT (UnboxAI API):** the last few app or site names you used
  (for YouTube and Reddit, the page title), plus the names of phone apps you
  opened in the last 5 minutes. Nothing else.
- **To ntfy.sh:** only phone pickups, as "which app" + "when", on your own
  topic. The topic has no password: anyone who knows it can read your
  pickups or post fake ones, so use a long random topic and keep it secret.
- **Never sent anywhere:** what you type. The tracker counts key presses and
  clicks; it never records which keys.
- **Stored on your laptop only:** the full address and title of each browser
  tab you use (including private windows), in `data/` (git-ignored).
- **This dashboard:** the timeline shows only the file you pick or upload. On
  Streamlit Cloud the bundled file is fake sample data. The Results tab shows
  every file in `evaluation/results/`; the ones made from real logs are
  git-ignored, so a deployed copy only has what you commit on purpose.
""")


def results_page(st) -> None:
    files = sorted(glob.glob(RESULTS_GLOB))
    if not files:
        st.info("No evaluation results yet. They will show here once "
                "`evaluation/results/*.json` exists.")
        return
    for p in files:
        st.subheader(Path(p).stem)
        try:
            data = json.loads(Path(p).read_text())
        except Exception as e:
            st.warning(f"Could not read {Path(p).name}: {e}")
            continue
        # Flat dict of numbers -> metric tiles; anything else -> table or raw JSON.
        if isinstance(data, dict) and data and all(
                isinstance(v, (int, float)) for v in data.values()):
            cols = st.columns(min(4, len(data)))
            for i, (k, v) in enumerate(data.items()):
                cols[i % len(cols)].metric(k, round(v, 3) if isinstance(v, float) else v)
        elif isinstance(data, list) and data and all(isinstance(r, dict) for r in data):
            st.dataframe(pd.DataFrame(data))
        else:
            st.json(data)


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="Loophole", page_icon="🟢", layout="wide")
    st.title("Loophole")
    st.caption("Knows when you stall and where you'll drift.")

    # ---- pick a file ----
    files = available_files()
    upload = st.sidebar.file_uploader("Upload an events file (.jsonl)", type=["jsonl", "json", "txt"])
    if upload is not None:
        records, source = parse_jsonl(upload.getvalue().decode("utf-8", "replace")), upload.name
    elif files:
        label = st.sidebar.selectbox("Events file", list(files))
        records, source = parse_jsonl(files[label].read_text()), label
    else:
        records, source = [], None

    synthetic = is_synthetic(source, records)
    tag = " (synthetic)" if synthetic else ""
    if synthetic:
        st.error(f"**{SYNTHETIC_BANNER}.** `{source}` is made-up data "
                 "(evaluation/fake_data.py or the bundled sample). Use it to see how the "
                 "page works, never as evidence.")

    tab_timeline, tab_results, tab_privacy = st.tabs(["Timeline", "Results", "Privacy"])

    with tab_timeline:
        if not records:
            st.info("No events yet. Upload a .jsonl file, or run a session with the "
                    "menu-bar app so `data/events-<user>.jsonl` exists.")
        else:
            sessions = split_sessions(records)
            st.sidebar.caption(f"{source}: {len(records)} records, {len(sessions)} session(s)")

            # Per-session summary table
            summary = pd.DataFrame([session_summary(s) for s in sessions])
            summary.index = [f"Session {i + 1}" for i in range(len(sessions))]
            st.subheader("Sessions" + tag)
            st.dataframe(summary)

            # One session's timeline, or all of them
            options = list(summary.index) + [ALL_SESSIONS]
            pick = st.selectbox("Show timeline for", options, index=len(sessions) - 1)
            chosen = records if pick == ALL_SESSIONS else sessions[options.index(pick)]
            s = session_summary(chosen)
            c = st.columns(5)
            c[0].metric("Length", f"{s['length_min']} min")
            c[1].metric("Work share", f"{int(s['work_share'] * 100)}%" if s["work_share"] is not None else "-")
            c[2].metric("Nudges", s["nudges"])
            c[3].metric("Breaks taken", s["breaks_taken"])
            c[4].metric("Phone pickups", s["phone_pickups"])

            frames = build_frames(chosen)
            if frames["apps"].empty and frames["scores"].empty:
                st.info("This session has no app or score records.")
            else:
                st.altair_chart(timeline_chart(frames, synthetic), width="stretch")
                st.caption("Orange lines = nudges shown. Grey dotted lines = staged demo "
                           "nudges (not counted). Purple triangles = phone pickups. "
                           f"Dashed lines = thresholds (stall {STALL_THRESHOLD}, "
                           f"where rank {RANK_THRESHOLD}). Since 2026-11-08 the live nudge "
                           "fires only when the pickup memory speaks up (below); stall and "
                           "BehaviorGPT are inputs to it.")
                where_readout(st, frames["scores"], tag)
                memory_section(st, frames, synthetic, tag)
            precursor_section(st, chosen, tag)
            with st.expander("Nudges and phone pickups"):
                st.dataframe(frames["nudges"])  # `staged` column marks demo stagings
                st.dataframe(frames["phones"])

    with tab_results:
        results_page(st)

    with tab_privacy:
        privacy_note(st)


if __name__ == "__main__":
    main()
