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
NUDGE_COOLDOWN = 20 * 60
BREAK_MINUTES = 5

# Phone listener
PHONE_TEST_TAG = "loophole_test"   # ntfy tag on "Send test ping" messages
PHONE_BACKFILL_MAX_S = 12 * 3600   # on start, fetch missed pickups up to this far back
DEMO_COOLDOWN = 3 * 60             # seconds between nudges in --demo mode

# Labels: phone records closer than this are one pickup episode.
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
PRECURSOR_MIN_EPISODES = 5      # say nothing before this many real pickups
PRECURSOR_MIN_LIFT = 2.0        # and only if matched moments led to a pickup 2x the usual rate

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
