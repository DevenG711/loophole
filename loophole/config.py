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

# Nudge rule
STALL_THRESHOLD = 1.0   # z-score-like; tuned on morning data
RANK_THRESHOLD = 10     # first distraction item at rank <= this
ACTIVE_WITHIN = 10      # user typed/clicked in the last N seconds
NUDGE_COOLDOWN = 20 * 60
BREAK_MINUTES = 5
