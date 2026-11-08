# Loophole

**Your keyboard tells us *when* you're about to break. BehaviorGPT tells us *where* you'll go.**

Built for Sundai Club Hack #148, "Behavioral Models: Predict the next action". Team of 2. Demo at 20:00.

## The problem

You're working, you slow down, and a few minutes later you're on your phone or YouTube. Blockers only react after you open the app. Loophole notices the slowdown first and offers a real break instead.

## What the user sees

1. **Setup (5 min).** Install the Mac menu-bar app. On the iPhone, add one Shortcut that pings the laptop when Instagram, TikTok, YouTube, X, Reddit or Snapchat opens ([docs/phone-setup.md](docs/phone-setup.md)).
2. **Start a session.** Click the menu-bar dot → Start focus session (25 / 50 / 90 min).
3. **While working.** The dot changes colour:
   - green: nothing here looks like the minutes before your past pickups
   - amber: the pickup memory recognises this moment, but a nudge is blocked (cooldown, or you just came back)
   - red: a nudge is coming
   Opening a listed laptop distraction (YouTube, LinkedIn, news sites, Messages) shows a "Distraction detected" panel straight away.
4. **Nudge** (at most once per 20 min, only after 5+ real pickups): "This looks like the minutes before your past phone pickups. Instagram looks like your next move. Take a 5-minute break instead?" The buttons are **Start break**, **I'm fine** and **I grabbed my phone**.
5. **Break timer.** A countdown runs, then "Back to VS Code?" brings your work app back.
6. **Web dashboard.** A timeline of the session: apps used, the slowdown score, where BehaviorGPT thought you were heading, nudges, and real phone pickups.

## How it works

| Part | What it does | File |
|---|---|---|
| Tracker | Every 2 s: the front app, the browser tab URL and title, idle time, and counts of keys, clicks and scrolls. It never records what you type. It needs no Mac permissions, apart from a one-time prompt to read browser tabs. | `loophole/tracker.py` |
| Phone listener | The iPhone Shortcut posts the app name to ntfy.sh. The laptop checks every 10 s. A menu button is the backup. | `loophole/phone.py` |
| "When" score | Runs on the laptop. Looks at idle time, typing speed vs your last 10 min, scrolling with no typing, typing bursts followed by long pauses, app switching, and time since your last break. | `loophole/when_score.py` |
| "Where" score | Sends the last 8 events (apps, YouTube/Reddit titles, phone opens) to BehaviorGPT. It returns how close a time-waster is in the ranking. | `loophole/where_score.py` |
| Pickup memory | Keeps a snapshot of the minute before each real pickup and checks whether now looks like one. Matches on the stall score, BehaviorGPT's lean to distraction (from laptop apps only), time on one window, app switching and typing/scrolling trends. Speaks only after 5 pickups, and only if similar past moments led to a pickup at least 2x as often as usual. | `loophole/precursor.py` |
| Nudge rule | Nudges only when the pickup memory speaks up, you were active a moment ago, and there's been no nudge for 20 min. (The first rule, "stall high and a time-waster in the top 10", is kept only as an eval row.) | `loophole/nudge.py` |
| Engine | Runs both scores every 30 s and logs the result. | `loophole/engine.py` |
| Menu-bar app | Shows the dot, the nudge and the break timer. | `loophole/menubar.py` |
| Dashboard | Streamlit app, deployed online. | `dashboard/app.py` |
| Evaluation | The honest tests below. | `evaluation/` |

Shared settings are in `loophole/config.py`. Every part reads and writes one log format, described in `loophole/events.py` (`data/events-<user>.jsonl`).

**What leaves the laptop:** app names, YouTube and Reddit page titles, and phone app names. They go to BehaviorGPT (with `register_event=False`) and to ntfy.sh. Nothing you type is recorded.

## What we already tested (2026-11-08)

- **BehaviorGPT and work vs distraction.** With our 60-item app catalog (`cat_35a0afc5ae400da9f74d27e2`):
  - coding history: the first time-waster is ranked about 19th
  - iMessage or Hacker News: about 7th
  - YouTube or social history: 0
- **Page titles.** On the same site, tutorial titles score like coding (0.16), and prank or vlog titles score 0.74. App names alone can't tell them apart.
- **Phone opens.** A recent phone app open in the history moves the first time-waster from about 23rd to 1st.
- **What BehaviorGPT can't see.** It does **not** see stalling, time gaps or time of day. Fake "idle" items moved the rank no more than random items did. That's why the "when" score is computed locally. We say so on stage.

### What our real data says (2026-11-08, one person, one session, 12 pickups)

- Nothing beat elapsed time. Clock time alone scored AUC 0.73; the pickup memory 0.63; BehaviorGPT's rank 0.50; the stall score 0.36. Pickups got more frequent later on. On moments after the first pickup, the memory drops to 0.45 (chance). See `evaluation/results/pickups.json`.
- Before pickups the user looked *focused*, not stalled: longer on one window, faster typing, fewer app switches.
- Some pickups were probably us testing the phone Shortcut (the ntfy.sh page was open nearby).
- Titles (claim 1): 14 real YouTube/Reddit titles, 9 off-task and 5 on-task. AUC: BehaviorGPT rank 0.64 [0.23, 1.00], keyword rule 0.54, site rule 0.50. BehaviorGPT's accuracy at the pre-registered cut (rank <= 10 = off-task) is only 0.36: most off-task titles land at rank 12-20. See `evaluation/results/titles.json`.
- The live nudge (memory-only) has not fired on real data yet.

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
2. Tutorial vs prank on YouTube. Open a tutorial: the "Distraction detected" panel appears (a site rule can't tell them apart), and one click on **This is work** clears it for that video only. Then show the titles result: on 14 real titles, BehaviorGPT ranked them a bit better than the rules (AUC 0.64 vs 0.54), but n is far too small to call it a win.
3. Replay the real session on the dashboard: what came before each pickup. Say what we found: the user looked *focused*, not stalled, and nothing beat elapsed time.
4. The results table, including one miss and one slide on "what BehaviorGPT can't see".
5. A live, staged nudge. We say on stage that it's staged.
