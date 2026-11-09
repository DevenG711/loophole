#!/bin/bash
# Loophole setup. Run it once from the Loophole folder:  bash setup.sh
# Safe to run again: it keeps your settings file (.env) and your data.
set -euo pipefail
cd "$(dirname "$0")"

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m%s\033[0m\n' "$*"; exit 1; }

[ "$(uname)" = "Darwin" ] || fail "Loophole only runs on macOS."

# 1. Install Python packages into .venv -------------------------------------
bold "1/4  Installing Loophole (takes a minute the first time)"
if command -v uv >/dev/null 2>&1; then
  uv sync --quiet   # uv also downloads Python 3.11+ if you don't have it
else
  PY=""
  for c in python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1 &&
       "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
      PY="$(command -v "$c")"; break
    fi
  done
  if [ -z "$PY" ]; then
    fail "Loophole needs Python 3.11 or newer, and this Mac doesn't have it.
Easiest fix: install uv (it brings its own Python), then run setup again:
    curl -LsSf https://astral.sh/uv/install.sh | sh
    bash setup.sh
Or install Python from https://www.python.org/downloads/macos/"
  fi
  echo "Using $PY"
  [ -x .venv/bin/python ] || "$PY" -m venv .venv
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -e .
fi
.venv/bin/python -c "import loophole.menubar" || fail "Install failed. Scroll up for the error."

# 2. Settings file ----------------------------------------------------------
bold "2/4  Settings file (.env)"
if [ -f .env ]; then
  echo "Keeping your existing .env."
else
  cp .env.example .env
  echo "Made .env from .env.example."
fi
# Fill in a random phone topic and your user name if they are still empty.
.venv/bin/python - <<'PY'
import getpass, re, secrets
from pathlib import Path

env = Path(".env")
text = env.read_text()

def value(key):
    m = re.search(rf"^{key}=(.*)$", text, re.M)
    return m.group(1).strip() if m else None

def put(key, val):
    global text
    if re.search(rf"^{key}=", text, re.M):
        text = re.sub(rf"^{key}=.*$", f"{key}={val}", text, count=1, flags=re.M)
    else:
        text += f"\n{key}={val}\n"

topic = value("NTFY_TOPIC")
if not topic or topic == "loophole-change-me":
    put("NTFY_TOPIC", "loophole-" + secrets.token_hex(8))
    print("Picked a random phone topic for you.")
if value("LOOPHOLE_USER") in (None, "", "me"):
    user = re.sub(r"[^a-z0-9-]", "", getpass.getuser().lower()) or "me"
    put("LOOPHOLE_USER", user)
env.write_text(text)
PY

# 3. Let macOS show Loophole's notifications --------------------------------
bold "3/4  Notifications"
/usr/libexec/PlistBuddy -c 'Add :CFBundleIdentifier string "loophole"' .venv/bin/Info.plist \
  >/dev/null 2>&1 || true
echo "Done. (The first time a nudge appears, macOS may ask to allow notifications: click Allow.)"

# 4. Phone link -------------------------------------------------------------
bold "4/4  Your phone link"
TOPIC="$(grep -E '^NTFY_TOPIC=' .env | head -1 | cut -d= -f2-)"
LINK="https://ntfy.sh/$TOPIC"
printf '%s' "$LINK" | pbcopy
cat <<EOF
This is the link your iPhone Shortcut sends to. It's private to you:

    $LINK

It's on your clipboard now. If your iPhone uses the same Apple ID, you can
paste it on the phone. You can copy it again later from the Loophole menu:
Phone setup > Copy phone link.

Phone setup steps: docs/phone-setup.md (or README.md, "Phone setup").
EOF

bold "All set. Start Loophole with:"
echo "    bash start.sh"
echo
echo "A grey dot shows up in your menu bar. Click it > Start focus session."
