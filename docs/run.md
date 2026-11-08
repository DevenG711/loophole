# How to run

All commands run from the repo root and use the project's virtualenv.

## Menu-bar app

```bash
.venv/bin/python -m focusguard.menubar          # normal
.venv/bin/python -m focusguard.menubar --demo   # stage demo
```

- A grey dot appears in the menu bar. Click it, then **Start focus session** (25/50/90 min).
- During a session the dot is green, amber or red. Click it to see time focused,
  momentum, "Likely next", last break, **I grabbed my phone** and **Stop session**.
- `--demo` prints `DEMO MODE IS ON`. It shortens the nudge cooldown to 60 s, lowers
  the thresholds, and adds two menu items: **Demo: score now** and **Demo: show nudge now**.
- Events go to `data/events-<FOCUSGUARD_USER>.jsonl`.

First-time setup on a Mac:

1. Copy `.env.example` to `.env` and fill in `UNBOXAI_API_KEY` and `NTFY_TOPIC`.
2. Allow notifications from Python. rumps needs a bundle id next to the Python binary:
   ```bash
   /usr/libexec/PlistBuddy -c 'Add :CFBundleIdentifier string "rumps"' .venv/bin/Info.plist
   ```
   Without it, the app falls back to `osascript` notifications, which also work.
3. The tracker needs **Accessibility** / **Input Monitoring** permission for your
   terminal (System Settings > Privacy & Security). Browser tab reading asks for
   **Automation** permission the first time.
4. Phone pickups: see [phone-setup.md](phone-setup.md), then use
   **Phone setup > Send test ping** in the menu.

## Dashboard (local)

```bash
.venv/bin/streamlit run dashboard/app.py
```

It lists every `data/*.jsonl` file plus `dashboard/sample_events.jsonl` (if present).
You can also upload a `.jsonl` file in the sidebar. To read files from another folder:
`FOCUSGUARD_DATA_DIR=/path/to/folder .venv/bin/streamlit run dashboard/app.py`.

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
