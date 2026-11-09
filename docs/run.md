# How to run

All commands run from the repo root and use the project's virtualenv.

## Menu-bar app

```bash
.venv/bin/python -m loophole.menubar          # normal
.venv/bin/python -m loophole.menubar --demo   # stage demo
.venv/bin/python -m loophole.menubar --no-island  # menu-bar dot + popups only
```

- A black "notch island" sits at the top centre of the screen, on every Space and
  over full-screen apps. Collapsed, it shows the status dot and time left (around the
  hardware notch on MacBooks). Hover it to open the details and buttons. Nudges and
  "Back to <app>?" open the island instead of a popup and stay open until you answer.

- A grey dot appears in the menu bar. Click it, then **Start focus session** (25/50/90 min).
- During a session the dot is green, amber or red. Amber means you are stalling, or
  a distraction is very likely next and you are half-way to a stall. Red means a
  nudge is on screen. Click the dot to see time focused, momentum, "Likely next",
  last break, last phone pickup, **I grabbed my phone** and **Stop session**.
- With `--no-island`, a nudge opens a popup with three buttons: take a break, **I'm fine**, or
  **I picked up my phone**. The same choices stay in the menu until you answer.
- Use **Quit Loophole** in the menu to quit. It logs the session stop first.
- `--demo` prints `DEMO MODE IS ON`. It shortens the nudge cooldown to 3 min, lowers
  the thresholds, and adds two menu items: **Demo: score now** and **Demo: show nudge now**.
  Nudges from **Demo: show nudge now** are logged with `"staged": true`.
- Events go to `data/events-<LOOPHOLE_USER>.jsonl`.

First-time setup on a Mac: run `bash setup.sh` (see [README.md](../README.md)). It does
steps 1 and 2 below for you, then `bash start.sh` starts the app. By hand:

1. Copy `.env.example` to `.env` and set `NTFY_TOPIC` to a long random name.
   `UNBOXAI_API_KEY` can stay empty: `config.SHARED_API_KEY` is used then.
2. Allow notifications from Python. rumps needs a bundle id in an `Info.plist` next to
   the Python binary. Run this once after making the virtualenv (it is not in git):
   ```bash
   /usr/libexec/PlistBuddy -c 'Add :CFBundleIdentifier string "loophole"' .venv/bin/Info.plist
   ```
   Without it, rumps notifications fail and the app falls back to `osascript`
   notifications, which are easy to miss. Nudges also open a popup, so they still show.
3. Keyboard and mouse counts need no permission. Browser tab reading asks for
   **Automation** permission the first time a browser is in front during a session.
   If you missed it: System Settings > Privacy & Security > Automation, then turn
   on the browser under your terminal (or Cursor/VS Code).
4. Phone pickups: see [phone-setup.md](phone-setup.md). Start a session, then use
   **Phone setup > Send test ping**. The **Last phone** line should show
   "Test (test ping)" within about 10 s. Test pings are logged with `source: "test"`,
   not as real pickups. When the app starts it also fetches pickups it missed while it
   was closed (up to 12 h back).

## Dashboard (local)

```bash
.venv/bin/streamlit run dashboard/app.py
```

It lists every `data/*.jsonl` file plus `dashboard/sample_events.jsonl` (if present).
You can also upload a `.jsonl` file in the sidebar. To read files from another folder:
`LOOPHOLE_DATA_DIR=/path/to/folder .venv/bin/streamlit run dashboard/app.py`.

Tabs: **Timeline** (per-session table and the app strip / when / where chart),
**Results** (reads `evaluation/results/*.json`), **Privacy**.

## Deploy the dashboard to Streamlit Community Cloud

1. Make sure `dashboard/sample_events.jsonl` exists (made by the fake-data generator).
   Real logs in `data/` are git-ignored, so they are never pushed.
2. Add `dashboard/requirements.txt` with only what the dashboard needs. Cloud reads
   this file before the root `pyproject.toml`/`uv.lock`, so it skips the Mac-only
   packages (rumps, pyobjc) and the model client:
   ```
   streamlit>=1.38
   pandas>=2.0
   altair>=5
   ```
3. Push the repo to GitHub (public, or private with Cloud access).
4. Go to https://share.streamlit.io, click **Create app**, pick the repo and branch,
   and set **Main file path** to `dashboard/app.py`. In **Advanced settings** choose
   Python 3.11 or newer. No secrets are needed.
5. Click **Deploy**. Later pushes redeploy automatically.
