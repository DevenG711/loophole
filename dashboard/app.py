"""Focus Guard dashboard (Streamlit).

Run:     .venv/bin/streamlit run dashboard/app.py
Deploy:  Streamlit Community Cloud, main file = dashboard/app.py (see docs/run.md)

Reads a Focus Guard events file (JSON lines, format in focusguard/events.py)
and draws one session timeline:
  - app strip: green = work, red = distraction
  - "when" line: how stalled you are (engine stall score)
  - "where" line: how close BehaviorGPT thinks you are to drifting
    (closeness = 1 when a distraction is the #1 prediction, 0 when none is
    in the top RANK_LIMIT)
  - orange rules = nudges, purple triangles = phone pickups

This file has no focusguard imports except the dependency-free catalog, so it
runs on Streamlit Cloud with only streamlit + pandas + altair installed.
The data helpers are plain functions so tests can import them; the page is
only drawn when Streamlit runs this file as __main__.
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_FILE = Path(__file__).resolve().parent / "sample_events.jsonl"
RESULTS_GLOB = str(ROOT / "evaluation" / "results" / "*.json")
# Where to look for events files. Override with FOCUSGUARD_DATA_DIR=/some/dir.
DATA_DIR = Path(os.getenv("FOCUSGUARD_DATA_DIR", ROOT / "data"))

# Same numbers as focusguard/config.py. Copied (not imported) because config
# needs python-dotenv and a .env file, which Streamlit Cloud doesn't have.
RANK_LIMIT = 60
RANK_THRESHOLD = 10
STALL_THRESHOLD = 1.0

# Same lists as focusguard/menubar.py: sites and apps that count as distractions.
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
NEUTRAL_APPS = {"finder", "loginwindow", "python", "python3", "focus guard",
                "screensaverengine", "usernotificationcenter", "dock"}

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


def closeness(rank) -> float:
    """BehaviorGPT rank of the first distraction -> 0..1, higher = closer to
    drifting. Rank 1 -> 1.0, rank RANK_LIMIT -> ~0, no distraction -> 0."""
    if rank is None or pd.isna(rank):
        return 0.0
    rank = max(1, int(rank))
    return max(0.0, 1 - (rank - 1) / RANK_LIMIT)


def split_sessions(records: list[dict]) -> list[list[dict]]:
    """Cut records into sessions using `session` start/stop records.

    Records outside any session are dropped. If the file has no session
    records at all, the whole file is one session.
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


def build_frames(records: list[dict]) -> dict[str, pd.DataFrame]:
    """Records of one session -> DataFrames for the timeline.

    apps:   start, end, app, label, kind   (one row per app/tab segment)
    scores: time, when, where_rank, closeness, nudge, reason
    nudges: time, action
    phones: time, app, source
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
               "nudge": bool(r.get("nudge")), "reason": r.get("reason") or ""}
              for r in records if r["kind"] == "score"]
    nudges = [{"time": t(r["ts"]), "action": r.get("action", "?")}
              for r in records if r["kind"] == "nudge"]
    phones = [{"time": t(r["ts"]), "app": r.get("app") or "?",
               "source": r.get("source") or "?"}
              for r in records if r["kind"] == "phone"]
    return {
        "apps": pd.DataFrame(apps, columns=["start", "end", "app", "label", "kind", "seconds"]),
        "scores": pd.DataFrame(scores, columns=["time", "when", "where_rank", "closeness", "nudge", "reason"]),
        "nudges": pd.DataFrame(nudges, columns=["time", "action"]),
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
    actions = f["nudges"]["action"].value_counts().to_dict()
    return {
        "start": pd.to_datetime(records[0]["ts"], unit="s") if records else None,
        "planned_min": start.get("minutes") if start else None,
        "length_min": round((records[-1]["ts"] - records[0]["ts"]) / 60, 1) if records else 0,
        "work_share": round(work / tracked, 2) if tracked else None,
        "distraction_min": round(distr / 60, 1),
        "nudges": int(actions.get("shown", 0)),
        "breaks_taken": int(actions.get("break", 0)),
        "dismissed": int(actions.get("dismiss", 0)),
        "phone_pickups": len(f["phones"]),
        "max_stall": round(float(f["scores"]["when"].max()), 2)
        if f["scores"]["when"].notna().any() else None,
    }


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

def timeline_chart(frames: dict[str, pd.DataFrame]):
    """App strip + when line + where line, sharing one time axis."""
    import altair as alt

    apps, scores = frames["apps"], frames["scores"]
    nudges = frames["nudges"]
    shown = nudges[nudges["action"] == "shown"]
    phones = frames["phones"]
    width = "container"

    colour = alt.Color("kind:N", title="App type",
                       scale=alt.Scale(domain=list(KIND_COLOURS), range=list(KIND_COLOURS.values())))
    strip = alt.Chart(apps).mark_rect().encode(
        x=alt.X("start:T", title=None), x2="end:T", color=colour,
        tooltip=["app", "label", "kind", alt.Tooltip("start:T", format="%H:%M:%S")],
    ).properties(height=50, width=width, title="Apps (green = work, red = distraction)")
    if not phones.empty:
        strip += alt.Chart(phones).mark_point(shape="triangle-down", size=150, filled=True,
                                              color="#7b3fe4").encode(
            x="time:T", tooltip=["app", "source", alt.Tooltip("time:T", format="%H:%M:%S")])

    def flags():
        """Orange vertical rules where a nudge was shown."""
        return alt.Chart(shown).mark_rule(color="orange", strokeWidth=2).encode(
            x="time:T", tooltip=[alt.Tooltip("time:T", title="nudge", format="%H:%M:%S")])

    when = alt.Chart(scores).mark_line(point=True, color="#1f77b4").encode(
        x=alt.X("time:T", title=None), y=alt.Y("when:Q", title="stall"),
        tooltip=["when", "reason", alt.Tooltip("time:T", format="%H:%M:%S")],
    ).properties(height=150, width=width, title="When: stall score (higher = slowing down)")
    when += alt.Chart(pd.DataFrame({"y": [STALL_THRESHOLD]})).mark_rule(
        strokeDash=[4, 4], color="grey").encode(y="y:Q")

    where = alt.Chart(scores).mark_line(point=True, color="#d64545").encode(
        x=alt.X("time:T", title="time"),
        y=alt.Y("closeness:Q", title="closeness", scale=alt.Scale(domain=[0, 1])),
        tooltip=["where_rank", "closeness", alt.Tooltip("time:T", format="%H:%M:%S")],
    ).properties(height=150, width=width,
                 title="Where: closeness to drifting (from BehaviorGPT rank; higher = closer)")
    where += alt.Chart(pd.DataFrame({"y": [closeness(RANK_THRESHOLD)]})).mark_rule(
        strokeDash=[4, 4], color="grey").encode(y="y:Q")

    if not shown.empty:
        strip, when, where = strip + flags(), when + flags(), where + flags()
    return alt.vconcat(strip, when, where).resolve_scale(x="shared", color="independent")


def privacy_note(st) -> None:
    st.markdown("""
**What leaves your laptop**

- **To BehaviorGPT (UnboxAI API):** the last few app or site names you used
  (for YouTube and Reddit, the page title). Nothing else.
- **To ntfy.sh:** only phone pickups, as "which app" + "when", on your own
  random topic. Anyone who knows the topic can read it, so keep it secret.
- **Never sent anywhere:** what you type. The tracker counts key presses and
  clicks; it never records which keys.
- **This dashboard:** shows only the file you pick or upload. On Streamlit
  Cloud the bundled file is fake sample data. Your real log stays in `data/`
  on your laptop (it is git-ignored).
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

    st.set_page_config(page_title="Focus Guard", page_icon="🟢", layout="wide")
    st.title("Focus Guard")
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
            st.subheader("Sessions")
            st.dataframe(summary)

            # One session's timeline
            pick = st.selectbox("Show timeline for", list(summary.index),
                                index=len(sessions) - 1)
            chosen = sessions[list(summary.index).index(pick)]
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
                st.altair_chart(timeline_chart(frames), width="stretch")
                st.caption("Orange lines = nudges shown. Purple triangles = phone pickups. "
                           "Dashed lines = nudge thresholds.")
            with st.expander("Nudges and phone pickups"):
                st.dataframe(frames["nudges"])
                st.dataframe(frames["phones"])

    with tab_results:
        results_page(st)

    with tab_privacy:
        privacy_note(st)


if __name__ == "__main__":
    main()
