# Focus Guard

**Your keyboard tells us *when* you're about to break. BehaviorGPT tells us *where* you'll go.**

Built for Sundai Club Hack #148, "Behavioral Models: Predict the next action". Team of 2. Demo at 20:00.

## The problem

You're working, you slow down, and a few minutes later you're on your phone or YouTube. Blockers only react after you open the app. Focus Guard notices the slowdown first and offers a real break instead.

## What the user sees

1. **Setup (5 min).** Install the Mac menu-bar app. On the iPhone, add one Shortcut that pings the laptop when Instagram, TikTok, YouTube, X, Reddit or Snapchat opens ([docs/phone-setup.md](docs/phone-setup.md)).
2. **Start a session.** Click the menu-bar dot → Start focus session (25 / 50 / 90 min).
3. **While working.** Nothing pops up. The dot changes colour:
   - green: working well
   - amber: slowing down, or heading toward a time-waster
   - red: both, so a nudge is coming
4. **Nudge** (at most once per 20 min): "You've slowed down, and Instagram looks like your next move. Take a 5-minute break instead?" The buttons are **Start break**, **I'm fine** and **I grabbed my phone**.
5. **Break timer.** A countdown runs, then "Back to VS Code?" brings your work app back.
6. **Web dashboard.** A timeline of the session: apps used, the slowdown score, where BehaviorGPT thought you were heading, nudges, and real phone pickups.

## How it works

| Part | What it does | File |
|---|---|---|
| Tracker | Every 2 s: the front app, the browser tab URL and title, idle time, and counts of keys, clicks and scrolls. It never records what you type. It needs no Mac permissions, apart from a one-time prompt to read browser tabs. | `focusguard/tracker.py` |
| Phone listener | The iPhone Shortcut posts the app name to ntfy.sh. The laptop checks every 10 s. A menu button is the backup. | `focusguard/phone.py` |
| "When" score | Runs on the laptop. Looks at idle time, typing speed vs your last 10 min, scrolling with no typing, typing bursts followed by long pauses, app switching, and time since your last break. | `focusguard/when_score.py` |
| "Where" score | Sends the last 8 events (apps, YouTube/Reddit titles, phone opens) to BehaviorGPT. It returns how close a time-waster is in the ranking. | `focusguard/where_score.py` |
| Nudge rule | Nudges when the stall score is high, a time-waster ranks in the top 10, you were active a moment ago, and there's been no nudge for 20 min. | `focusguard/nudge.py` |
| Engine | Runs both scores every 30 s and logs the result. | `focusguard/engine.py` |
| Menu-bar app | Shows the dot, the nudge and the break timer. | `focusguard/menubar.py` |
| Dashboard | Streamlit app, deployed online. | `dashboard/app.py` |
| Evaluation | The honest tests below. | `evaluation/` |

Shared settings are in `focusguard/config.py`. Every part reads and writes one log format, described in `focusguard/events.py` (`data/events-<user>.jsonl`).

**What leaves the laptop:** app names, YouTube and Reddit page titles, and phone app names. They go to BehaviorGPT (with `register_event=False`) and to ntfy.sh. Nothing you type is recorded.

## What we already tested (2026-11-08)

- **BehaviorGPT and work vs distraction.** With our 60-item app catalog (`cat_35a0afc5ae400da9f74d27e2`):
  - coding history: the first time-waster is ranked about 19th
  - iMessage or Hacker News: about 7th
  - YouTube or social history: 0
- **Page titles.** On the same site, tutorial titles score like coding (0.16), and prank or vlog titles score 0.74. App names alone can't tell them apart.
- **Phone opens.** A recent phone app open in the history moves the first time-waster from about 23rd to 1st.
- **What BehaviorGPT can't see.** It does **not** see stalling, time gaps or time of day. Fake "idle" items moved the rank no more than random items did. That's why the "when" score is computed locally. We say so on stage.

## How we test it honestly

The full rules are in [evaluation/PROTOCOL.md](evaluation/PROTOCOL.md). We commit to them before collecting data.

1. **Headline result: titles.** About 70 real titles from our own browser histories. We label each on-task or off-task *before* seeing any scores. Three methods compete: a site rule, a keyword rule, and BehaviorGPT. We report accuracy and AUC.
2. **Second result, "early sign": stall before pickup.**
   - The target is a real phone pickup 30 s to 3 min ahead.
   - We only use data from before that moment, and only score moments when the person was active.
   - It must beat two simple rules: "seconds idle" and "time since last pickup".
   - With 2 people we expect about 10–20 pickups, so this is not proof.

## Schedule

| Time | Person A | Person B |
|---|---|---|
| Now | Start the tracker on both laptops | Set up the phone Shortcut on both phones |
| Afternoon | Scores, nudge, menu-bar app | Label titles; test scripts |
| 16:30 | Feature freeze | Deploy the dashboard |
| 17:00 | Data cutoff; run the tests together | |
| 18:00–20:00 | Slides, backup video, rehearse | |

## Demo (2–3 min)

1. The problem, in one sentence.
2. Tutorial vs prank on YouTube. The site rule flags the tutorial, and our line stays green.
3. Replay a real afternoon: typing drops, the score rises, and a real phone pickup follows.
4. The results table, including one miss and one slide on "what BehaviorGPT can't see".
5. A live, staged nudge. We say on stage that it's staged.
