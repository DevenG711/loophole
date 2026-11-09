# Loophole

A small Mac menu-bar app that remembers what the minute before each phone
pickup looked like. When your work starts to look like that again, it offers
you a short break before you reach for your phone.

You need: a Mac, and about 10 minutes. An iPhone is optional but it's how
Loophole learns your pickups. You do **not** need an API key.

## Set up (5 minutes)

1. **Unzip** the download. You get a folder called `loophole-main`.
2. **Open Terminal in that folder.** Open the Terminal app, type `cd `
   (with a space after it), drag the `loophole-main` folder onto the Terminal
   window, and press Return.
3. **Run setup:**
   ```bash
   bash setup.sh
   ```
   It installs what Loophole needs, makes your settings file (`.env`), and
   prints your **phone link**. If it says you need Python 3.11, it tells you
   the one command to run first.
4. **Start Loophole:**
   ```bash
   bash start.sh
   ```
   A grey dot appears in your menu bar. Keep the Terminal window open (you can
   minimise it). Closing it quits Loophole.
5. **Set up your iPhone** (optional, 5 minutes): follow
   [docs/phone-setup.md](docs/phone-setup.md). You'll paste your phone link
   into one Shortcuts automation that covers all your distracting apps.

Next time, you only need step 2 and `bash start.sh`.

### Permissions macOS will ask for

- **Notifications:** the first time Loophole nudges you, click **Allow**.
- **Automation (control your browser):** the first time a browser is in front
  during a session, macOS asks if Terminal may control it. Click **OK**.
  Loophole reads the address and title of the tab in front, so it can tell
  YouTube for work from YouTube for fun. If you clicked Don't Allow: System
  Settings > Privacy & Security > Automation, then turn on your browser under
  Terminal.
- Keyboard and mouse counts need no permission. Loophole only counts keys,
  clicks and scrolls. It never records what you type.

## Using it

1. Click the dot > **Start focus session** > pick a length.
2. Work. The dot stays green while nothing looks off.
3. Amber means a warning sign, but Loophole is holding back (it just nudged
   you, or you just came back). Red means a nudge is coming.
4. A nudge offers a break. Pick **Start break**, **I'm fine**, or
   **I grabbed my phone** if you already did.
5. If you picked up your phone and the iPhone Shortcut isn't set up, click
   **I grabbed my phone** in the menu. It still helps.

Loophole starts nudging after it has seen about 5 pickups. Until 15, it says
its warning is an early, less accurate guess.

## Settings

Open the settings file from the menu: **Phone setup** > **Open settings file
(.env)**. Or open `.env` in the Loophole folder with any text editor. After you
save it, quit Loophole (menu > **Quit Loophole**) and run `bash start.sh` again.

| Setting | What it does | Default |
| --- | --- | --- |
| `LOOPHOLE_SESSION_LENGTHS` | Session lengths in the Start menu, in minutes, comma-separated | `25,50,90` |
| `LOOPHOLE_BREAK_MINUTES` | How long a break lasts, in minutes | `5` |
| `LOOPHOLE_USER` | Name used for your log file, `data/events-<name>.jsonl` | your Mac user name |
| `NTFY_TOPIC` | Your private phone channel. Your phone link is `https://ntfy.sh/<NTFY_TOPIC>`. If you change it, update the URL in your iPhone automation | random, made by setup |
| `UNBOXAI_API_KEY` | Leave empty: Loophole reaches BehaviorGPT through its shared proxy, so you need no key. Put your own here if you have one (free at [portal.unboxai.com](https://portal.unboxai.com)) | empty |
| `LOOPHOLE_PROXY_URL` | Only if you run your own proxy ([proxy/README.md](proxy/README.md)) | Loophole's proxy |

Which **phone apps** count is set on the iPhone, not here: it's the apps you
tick in your automation. See [docs/phone-setup.md](docs/phone-setup.md).

Advanced: the nudge thresholds live in [loophole/config.py](loophole/config.py),
and the laptop sites and apps that count as distractions are in
`DISTRACTION_DOMAINS` and `DISTRACTION_APPS` in
[loophole/menubar.py](loophole/menubar.py).

## Something's wrong?

| Problem | Fix |
| --- | --- |
| `setup.sh` says Python 3.11 is needed | Run `curl -LsSf https://astral.sh/uv/install.sh \| sh`, open a new Terminal window, and run `bash setup.sh` again |
| No dot in the menu bar | Look in the Terminal window for an error. On MacBooks with a notch, the dot can hide behind it: quit some other menu-bar apps |
| "Another Loophole is already writing" | Loophole is already running. Use its menu > **Quit Loophole**, or close the other Terminal window |
| "Likely next" is always empty | Check your internet connection. The Terminal window says which BehaviorGPT route it uses when Loophole starts. If you put your own `UNBOXAI_API_KEY` in `.env`, check it |
| Phone pickups don't show up | See "Test it" in [docs/phone-setup.md](docs/phone-setup.md) |
| No notifications | System Settings > Notifications > find Python (or Loophole) and allow it. Nudges still show in the notch and the menu |

## Your data

Everything Loophole logs stays in the `data/` folder on your Mac. What goes
over the internet:

- To BehaviorGPT: the names of the apps and sites you used in the last few
  minutes, and YouTube/Reddit page titles.
- To ntfy.sh: the names of phone apps you open (from your iPhone automation).

To remove Loophole, quit it and delete the folder. Then delete the iPhone
automation in Shortcuts.

## More

- Look back at a session in the dashboard: `.venv/bin/streamlit run dashboard/app.py`
- All run options: [docs/run.md](docs/run.md)
- What we built and how we tested it: [PLAN.md](PLAN.md)
