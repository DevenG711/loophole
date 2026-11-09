"""Settings shared by every part. Change thresholds here, nowhere else."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

API_KEY = os.getenv("UNBOXAI_API_KEY")
CATALOG_ID = os.getenv("LOOPHOLE_CATALOG_ID", "cat_35a0afc5ae400da9f74d27e2")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")
USER = os.getenv("LOOPHOLE_USER", "me")

DATA_DIR = ROOT / "data"
EVENTS_FILE = DATA_DIR / f"events-{USER}.jsonl"

# How often things run (seconds)
SAMPLE_EVERY = 2        # tracker: app, tab, input counters
PHONE_POLL_EVERY = 10   # phone listener: ntfy.sh
SCORE_EVERY = 30        # when + where scores

# "Where" score (BehaviorGPT)
HISTORY_LEN = 8         # last N events sent to the model
RANK_LIMIT = 60         # ask for the whole catalog, then find the first d_ item
TOP_N = 10              # share of distraction items in the top N
TITLE_DOMAINS = ("youtube.com", "reddit.com")  # send page titles, not app names
HISTORY_MAX_AGE = 15 * 60   # drop app/phone events older than this from the history
# A phone pickup leaves the history sooner: BehaviorGPT ranks any distraction in
# the history at #1, so a pickup 12 min ago kept saying "TikTok next" while you
# worked. Pickups do cluster within a few minutes, so keep them that long.
PHONE_HISTORY_MAX_AGE = 5 * 60
# BehaviorGPT ranks these high for almost any history (seen on real data), so
# they are skipped when finding the first distraction. Tuned on one session.
STICKY_DISTRACTIONS = {"d_hulu", "d_spotify_pod", "d_pinterest"}

# Nudge rule
STALL_THRESHOLD = 1.0   # z-score-like; tuned on morning data
RANK_THRESHOLD = 10     # "where is high" for evals and the amber dot (pre-registered)
NUDGE_RANK_THRESHOLD = 3  # stricter cut used only by the live nudge rule
ACTIVE_WITHIN = 10      # user typed/clicked in the last N seconds
RETURN_GAP_S = 60       # an input gap this long is a pause...
RETURN_GRACE_S = 90     # ...and for this long after it ends, a stall does not count

# "Settled": staying on one app/site while still touching the computer looks
# like reading (an article, docs, an AI agent's output), not stalling. While
# settled, slow typing and scrolling without typing do not count as a stall.
SETTLED_DWELL_S = 45      # on the same app/site at least this long
SETTLED_MAX_IDLE_S = 90   # and some input (any kind) within this many seconds
# Apps/sites where no input for minutes is normal (calls). Lower-case.
SETTLED_APPS = {"zoom.us", "zoom", "microsoft teams", "facetime", "webex"}
SETTLED_DOMAINS = {"meet.google.com", "zoom.us", "teams.microsoft.com"}
# Typing rates count only "active" input steps: some input within this many s.
ACTIVE_STEP_IDLE_S = 10
MIN_ACTIVE_S = 20         # need this much active time before key_drop counts
NUDGE_COOLDOWN = 10 * 60   # set by the user 2026-11-08 (was 20 min, never tested)
BREAK_MINUTES = 5

# Pickup pace (loophole/pace.py), added 2026-11-08 by user decision, after
# seeing data (post-hoc, PROTOCOL.md deviation 14). Personal numbers from your
# own past pickup episodes (phone records before now only; never "when").
#   usual gap  median time between your recent pickups
#   cooldown   time between nudges = your usual gap, kept in [MIN, MAX];
#              NUDGE_COOLDOWN until there are PACE_MIN_GAPS gaps
#   pace nudge recent pickups >= max(PACE_HIGH_MIN_COUNT,
#              PACE_HIGH_FACTOR x what your usual gap predicts for the window)
PACE_WINDOW_S = 10 * 60          # "pickups in the last 10 min"
PACE_MIN_GAPS = 5                # gaps needed before "usual" means anything
PACE_HISTORY_EPISODES = 30       # most recent pickups used for "usual"
PACE_MAX_GAP_S = 60 * 60         # a longer gap is time away, not your pace
PACE_HIGH_FACTOR = 2.0
PACE_HIGH_MIN_COUNT = 3
NUDGE_COOLDOWN_MIN = 3 * 60
NUDGE_COOLDOWN_MAX = 20 * 60

# Phone listener
PHONE_TEST_TAG = "loophole_test"   # ntfy tag on "Send test ping" messages
PHONE_BACKFILL_MAX_S = 12 * 3600   # on start, fetch missed pickups up to this far back
DEMO_COOLDOWN = 3 * 60             # seconds between nudges in --demo mode

# Labels: phone records within this many seconds of an episode's FIRST record
# are one pickup episode (no chaining: since 2026-11-08 review, a record more
# than PICKUP_MERGE_S after the first starts a new episode, so a long run of
# pings cannot hide re-pickups; PROTOCOL.md deviation).
PICKUP_MERGE_S = 120

# Pickup memory (loophole/precursor.py): what the 5 min before each real
# pickup looked like, and whether now looks like that. It is the ONLY thing
# that triggers a nudge (nudge.decide); "when" and "where" are inputs to it. Memory, not a trained
# model: no weights, nothing tuned on labels. Thresholds set before looking.
PRECURSOR_LOOKBACK_S = 5 * 60   # window described for each moment
PRECURSOR_LEAD_S = 60           # snapshot taken this long before the pickup
PRECURSOR_GRID_S = 30           # past moments sampled this often (base rate)
PRECURSOR_TARGET_S = (30, 180)  # "followed by a pickup" = one in (t+30 s, t+3 min]
PRECURSOR_MATCH_DIST = 1.0      # RMS distance (robust z units) that counts as "like"
# Both count USABLE pickups: ones with laptop activity before them (see below).
# From 5 the memory may nudge, but until 15 every answer is marked an early,
# less accurate guess (user decision 2026-11-08; PROTOCOL.md deviation 12).
PRECURSOR_MIN_EPISODES = 5        # say nothing (no nudge, no amber) before this many
PRECURSOR_CONFIDENT_EPISODES = 15  # below this, answers are labelled "early guess"
PRECURSOR_MIN_LIFT = 2.0        # and only if matched moments led to a pickup 2x the usual rate
# ...and those matched moments were followed by at least this many DIFFERENT
# pickups: grid moments 30 s apart before one pickup are one event, not three.
PRECURSOR_MIN_FOLLOWED_EPISODES = 3
# The lift needs at least this many matched past moments (moments that looked
# like the templates "now" matched). Was a hard-coded 3; raised to 5 on
# 2026-11-08 after review, because 3 moments made the lift swing on 1 pickup.
# A deviation (PROTOCOL.md).
PRECURSOR_MIN_MATCHED_MOMENTS = 5
# A pickup is a usable template (something "now" is compared with) only if the
# laptop was in use at its snapshot: idle < SETTLED_MAX_IDLE_S there, and at
# least this many active seconds of input in the 5 min before it. Pickups
# fetched later from ntfy (made while the app was closed) have no such data.
# Every real pickup still counts as an outcome ("was this moment followed by a
# pickup?"); dropping some would make the base rate look lower than it is.
PRECURSOR_TEMPLATE_MIN_ACTIVE_S = MIN_ACTIVE_S
# Tag only (never a filter): a template whose window shows us testing the
# pipeline. Rule fixed 2026-11-08 before looking at tagged counts. Window:
# [pickup - PRECURSOR_LEAD_S - PRECURSOR_LOOKBACK_S, min(pickup + 60 s, t)),
# widened 2026-11-08 after review to catch testing right at the pickup; never
# reads records at or after the time t being scored (PROTOCOL.md deviation).
DEV_TESTING_AFTER_S = 60
DEV_TESTING_DOMAINS = ("ntfy.sh", "localhost", "127.0.0.1")
DEV_TESTING_TITLE_WORD = "loophole"

# BehaviorGPT "pull" (where_score). Item scores are unnormalised but
# deterministic; we z-score each ranking (subtract its mean, divide by its sd).
#   gap   = mean z of d_* items minus mean z of f_* items (how strongly the
#           whole ranking leans to distraction; no single sticky item decides)
#   pull  = per-item z minus the same item's z after plain work histories;
#           the top d_* item is "the app pulling at you" if pull >= this:
WHERE_PULL_MIN_Z = 1.5
WHERE_REFERENCE_HISTORIES = [   # plain work, averaged once and cached
    ["f_vscode", "f_terminal", "f_github", "f_vscode", "f_stackoverflow", "f_vscode", "f_terminal", "f_vscode"],
    ["f_vscode", "f_chatgpt", "f_vscode", "f_chatgpt", "f_vscode", "f_github", "f_vscode", "f_chatgpt"],
    ["f_gmail", "f_slack", "f_calendar", "f_gdocs", "f_slack", "f_gmail", "f_gdocs", "f_vscode"],
    ["f_pydocs", "f_vscode", "f_terminal", "f_mdn", "f_vscode", "f_claude", "f_vscode", "f_terminal"],
]
