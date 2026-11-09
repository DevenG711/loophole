# Loophole evaluation protocol (pre-registered)

We commit this file before we collect or label any data. If we change a rule
later, we say so in the demo.

## Claim 1 (headline): on-task vs off-task titles on the same site

- **Data:** about 70 YouTube and Reddit page titles from our own browsers
  (`export_titles.py`, random sample, last 14 days). Each person may delete
  private rows first. Rows are never deleted after scores are seen.
- **Labels:** each owner labels only their own titles, `on` or `off`
  (was this for work/study?), **before** running any scorer.
- **Methods:**
  (a) domain rule: youtube/reddit/tiktok/etc. = off-task;
  (b) keyword rule: "tutorial", "how to", "lecture", "docs", "course" = on;
  "prank", "vlog", "reaction", "funny", "compilation", "drama" = off;
  else the domain rule;
  (c) BehaviorGPT via the app's own `where_score.score()`: 7 fixed work events
  (VS Code, GitHub, VS Code, Stack Overflow, Python docs, Terminal, VS Code),
  then the title as `ExternalItem(title, brand=domain)`.
- **BehaviorGPT score:** primary = rank of the first distraction item (0-based,
  lower = more off-task); secondary = share of distraction items in the top 10.
  Accuracy thresholds are the app's: rank <= 10 = off; share >= 0.5 = off.
- **Report:** accuracy and AUC with 95% bootstrap CIs, n, and the paired AUC
  difference (BehaviorGPT rank minus keyword rule) with its CI.

## Claim 2 (suggestive only): a stall comes before a phone pickup

- **Label:** a `phone` record. Target = a pickup in (t+30 s, t+3 min].
- **Moments:** every 30 s, after a 10-minute warm-up, only when there was
  input within 10 s before t.
- **No peeking:** features use only records with ts < t. The "when" score
  never reads phone records. "Where" may read phone records before t, as the
  live app does.
- **Baselines:** seconds idle; time since last pickup. If a baseline's AUC is
  below 0.5 we flip it (generous to the baseline).
- **Methods:** stall score (when), where rank, the app's nudge rule. Optional:
  logistic regression on when-features, trained on the first half of each
  person's day and tested on the second half, plus leave-one-person-out.
- **Report:** n, AUC (95% CI from resampling 10-minute blocks), false alarms
  per hour at a matched alert rate (the nudge rule's rate, or 5% if it fires on
  under 1% of moments), share of pickups caught 30 s or more ahead, and median
  lead time. We call the result **suggestive**: small n, two people, and our
  own nudges may change behavior.

Synthetic data (`fake_data.py`, files named `*fake*`) only tests the code.
We never report numbers from it as results.

## Deviations (added 2026-11-08, after seeing some real data)

These changes were made **after** the protocol above and after looking at our
own log (`data/events-deven.jsonl`, one person, one afternoon). They are
post-hoc. The evals report the pre-registered rule as the main row and show
the tuned rule next to it, labelled "tuned in-sample on <files>".

1. **Sticky distractions (where rank).** The live rank skips
   `STICKY_DISTRACTIONS` (Hulu, Spotify/podcasts, Pinterest), which
   BehaviorGPT ranks high for almost any history. Chosen from one session.
   Pre-registered rank = first `d_*` item, no skip (`skip_sticky=False`).
2. **Live nudge threshold.** The live nudge uses rank <= `NUDGE_RANK_THRESHOLD`
   (3). The pre-registered threshold `RANK_THRESHOLD` (10) is unchanged and is
   what the evals' main rows use.
3. **"Just returned" rule.** After an input gap of `RETURN_GAP_S`, the live
   app treats the next `RETURN_GRACE_S` as not active (`active_recent` is
   False), so a stall right after a pause does not count. The pre-registered
   moment set (input within 10 s) is unchanged; eval_pickups reports how many
   moments this rule removes and also the metrics on only the moments where
   the live nudge could fire.
4. **Where history clean-up.** The where history ignores plumbing apps and
   sites (Finder, System Settings, ntfy.sh, sign-in pages, browsers with no
   tab) and drops events older than 15 min (`HISTORY_MAX_AGE`), except the
   newest one.
5. **Pickup labels.**
   - Phone records with `source: "test"` are test pings and are not pickups.
   - Phone records less than `PICKUP_MERGE_S` (120 s) apart are one pickup
     episode, timed at its first record. Counts and labels use episodes.
   - `--skip-pickups HH:MM-HH:MM` drops phone records in a local time range.
     We use it only for Shortcut set-up testing, and we say so when we report.
     The script warns when an episode has 3+ different apps within 60 s. In
     our log, 13:34:49–13:36:49 (Snapchat, TikTok, Instagram, YouTube in 49 s,
     old `app=` format) looks like set-up testing.
   - Nudge records with `staged: true` (demo stagings) are ignored.
6. **Stall threshold.** `STALL_THRESHOLD` (1.0) was set on morning data from
   the same person, so the nudge rule is not out-of-sample either way.

With one person and a few pickup episodes, none of the pickup numbers are more
than suggestive.
7. **Pickup memory (post-hoc).** `loophole/precursor.py` keeps a snapshot of
   the 5 minutes before each real pickup episode and scores a moment by how
   much it looks like those snapshots. eval_pickups adds a row "pickup memory
   (post-hoc, leave-future-out)". At each moment t it uses only records with
   ts < t, so it only knows pickups that already happened (leave-future-out by
   construction). It sees the same pickups as the labels: no test pings, no
   `--skip-pickups` ranges. The idea came **after** looking at the pickups in
   `data/events-deven.jsonl`, so this row is post-hoc, not a pre-registered
   method. The thresholds (`PRECURSOR_*` in `config.py`) were set before we
   first ran this row on any data, and we will not tune them on this file.
   The pre-registered rows, the moment set and the matched alert rate are
   unchanged.
8. **Moments right after a nudge.** A nudge can change what the user does next.
   eval_pickups counts the moments within 3 min after a real (not staged)
   nudge was shown and also reports every row with those moments left out.
9. **Live nudge rule is now memory-only (2026-11-08, user decision, post-hoc).**
   The live app nudges only when the pickup memory (`precursor.match`) has
   text for this moment (plus the cooldown and "away / just returned" checks).
   The "when" stall score and BehaviorGPT's "where" no longer trigger a nudge
   on their own; they are inputs the memory matches on (`MATCH_FEATURES`:
   `dwell_s`, `key_trend`, `scroll_trend`, `switches_5m`, `stall`,
   `where_gap`). This change was decided **after** seeing the pickup numbers
   in `data/events-deven.jsonl`, so it is post-hoc.
   - eval_pickups: the live-rule row is now "nudge rule [live: memory text,
     post-hoc]". At each moment t it calls `precursor.match` on records with
     ts < t only (leave-future-out by construction), with the same filtered
     pickups as the labels. The pre-registered nudge row is unchanged. The
     previous live rule (`nudge.decide_when_where`) keeps its own row, still
     labelled as tuned in-sample.
   - The memory thresholds (`PRECURSOR_*` in `config.py`) were fixed before
     the memory was first evaluated, and we do not tune them on this file.
   - `where_gap` (BehaviorGPT's z-scored mean distraction score minus mean
     focus score) and the pull measures (`pull_app`, `pull_z`, cutoff
     `WHERE_PULL_MIN_Z`) were designed from a 23-call live experiment on our
     own histories, after the protocol was written. eval_pickups adds a
     post-hoc row "BehaviorGPT pull (where_gap, cache only)": it reads cached
     answers only (no API calls) and reports how many moments have one.
   - The matched alert rate is still the pre-registered nudge rule's rate.
10. **Time-trend nulls and memory leak fixes (2026-11-08, post-hoc).** A
    review found that the pickup memory's AUC matched raw clock time: pickups
    got more frequent later in the session, and the memory scores 0 until the
    first pickup exists. So:
    - eval_pickups adds two baseline rows, "elapsed time" (seconds since the
      first input) and "pickups so far", and a table with only moments after
      the person's first pickup. A method that does not beat these is not
      predicting pickups.
    - The memory's `where_gap` now comes from a history of laptop `app`
      records only. Before, phone records from the last 5 min were in it, and
      BehaviorGPT puts any phone app at #1, so `where_gap` partly encoded
      "you just picked up" (against the memory's own rule). The live "where"
      rank and the "BehaviorGPT pull" row are unchanged (they still see phone
      records, as pre-registered). Moments whose laptop-only history is not in
      the cache have no `where_gap`; the memory then compares on the other 5
      features.
    - Phone records with source "button" ("Grabbed phone") are not memory
      episodes: they usually answer a nudge. They are still labels for the
      pre-registered rows (the protocol label is "a `phone` record"). The
      2026-11-08 log has none.
    - A caveat on the 2026-11-08 data: in several pickups the ntfy.sh page or
      the Loophole dashboard was open within seconds to minutes, so some
      pickups were probably us testing the pipeline, not natural pickups. We
      did not drop them (that would be a label rule chosen after looking), and
      we report it instead.

**Hypothesis for future data (registered 2026-11-08, before collecting it):**
before a phone pickup the user is *settled* on one window with typing at or
above their recent baseline (for example, waiting on an AI agent to finish),
**not** stalled. In the snapshot terms: long `dwell_s`, few `switches_5m`,
`key_trend` >= 0, stall score below `STALL_THRESHOLD`. Where it came from: n = 6
pickup episodes, one person, one afternoon, found after looking at the data. We
test it only on data recorded after this date, with the same pickup-memory
row and the stall-score row side by side. If it holds, the stall idea ("a
stall comes before a pickup") is the wrong shape for this user.
11. **Titles run (2026-11-08).** Claim 1 ran on 14 labelled titles (one
    person), not about 70. The rows are in `evaluation/titles_candidates.csv`,
    labelled before scoring, and kept out of git because they are real watch
    history (`--titles evaluation/titles_candidates.csv`). With n = 14 every
    CI is wide; we report it as a pilot, not a result.
12. **Pickup memory gates (2026-11-08, decided by debate, stricter only).**
    Every change here except the 5-pickup minimum (user decision, unchanged
    from the original value) can only make the memory say less; none was chosen to
    improve a number (the live rule's text fired at 0 eval moments before and
    after).
    - `PRECURSOR_MIN_EPISODES` stays 5 but now counts *usable* pickups.
      (It was briefly 15; the user set it back to 5 the same day.) From 5 to
      14 usable pickups (`PRECURSOR_CONFIDENT_EPISODES` = 15) every answer is
      labelled an early, less accurate guess; the rule itself is the same.
      Counting only usable pickups is still stricter than before.
    - **Usable (a template):** at the snapshot time (pickup minus
      `PRECURSOR_LEAD_S`) idle is under `SETTLED_MAX_IDLE_S`, and the 5 min
      before it hold at least `PRECURSOR_TEMPLATE_MIN_ACTIVE_S` (= `MIN_ACTIVE_S`,
      20 s) of active input. Pickups fetched from ntfy after the fact (the app
      was closed) have no such data. Every real pickup still counts as an
      **outcome** (base rate, "followed", and the eval labels, unchanged).
    - The memory's text also needs the matched-and-followed past moments to
      lead to at least `PRECURSOR_MIN_FOLLOWED_EPISODES` (3) different pickups:
      grid moments 30 s apart before one pickup are one event.
    - The amber dot uses the same gate (it shows only when the memory has text).
    - **Pre-registered tag, `dev_testing` (tag only, never a filter):** a
      pickup whose 5-min snapshot window (ending at the snapshot time, plus the
      page open at its start) contains an `app` record with domain `ntfy.sh`,
      `localhost` or `127.0.0.1`, or a title containing "loophole"
      (case-insensitive). Rule written before looking at tagged counts. On data
      recorded after 2026-11-08, eval rows may report "templates with
      `dev_testing` = false only" as a pre-registered sensitivity row.
13. **Bug-fix pass (2026-11-08, ~16:00–16:50, after a code review; post-hoc).**
    Each change below fixes a bug found by reading the code. None was chosen
    to improve a number, but several change what features, labels and the
    memory mean, so all numbers from before this pass are superseded. The eval
    was re-run on `data/events-deven.jsonl` right after.
    - **Labels.** Phone records with source `button` or `hotkey` are no longer
      labels in any row (they usually answer a nudge). This replaces deviation
      10's "still labels for the pre-registered rows". Dropped counts are
      reported per source (none in this log).
    - **Episodes.** A phone record joins an episode only if it is within
      `PICKUP_MERGE_S` (120 s) of the episode's *first* record. Before,
      records chained with no limit and hid re-pickups. Same rule in the eval
      and the memory.
    - **Typing/scroll rates** (stall score and memory trends). A tracker step
      longer than 10 s adds neither counts nor active time. Before, its time
      was capped but all its keys counted, so rates jumped after any gap.
      Also applies to `scroll_no_type` and `burst_pause_count`.
    - **Breaks.** A tracker gap of 4+ min is a break only if idle is >= 4 min
      after it or no keys, clicks or scrolls were added across it.
    - **Switches and dwell.** `IGNORE_APPS` are skipped, a browser record with
      no tab (failed read) is not a new place, and switches per minute count
      over the same span they are divided by.
    - **Where history.** It always keeps the newest laptop app record that
      maps to an item, not the newest raw record (a phone record or Finder
      could push out the app you were in). `where_share` skips sticky items
      when the rank does (`skip_sticky`); the pre-registered share is unchanged.
    - **Pickup memory.**
      - The lift counts only past moments similar to the templates "now"
        matched, not moments similar to any template. The base rate is
        unchanged.
      - "Now" is never compared with a template whose snapshot is within
        `PRECURSOR_LOOKBACK_S` of t (the pickup that just happened).
      - Trends with too little active time are missing (None), not 0.
      - The lift needs >= 5 matched moments (`PRECURSOR_MIN_MATCHED_MOMENTS`),
        raised from a hard-coded 3. This is stricter only.
      - `dev_testing` window is now [pickup - 6 min, min(pickup + 60 s, t)),
        so testing right at the pickup is tagged. Still a tag, never a filter.
        eval_pickups now reports the tagged count and a sensitivity row (memory
        with dev_testing pickups' positives left out).
    - **Nudge records.** "Nudge shown" is written only by the menu bar when a
      nudge really appears; the engine records only its decision. Staged
      nudges, including every `--demo` mode nudge, are logged `staged: true`
      and do not start the live cooldown. Logs before this pass hold
      engine-written "shown" records that may not have been displayed (the 3
      in the 2026-11-08 log came from the old rule).
    - **Eval.** In subset tables, "caught" counts only pickups inside that
      subset's scored range. `pickups.json` records which log it was built
      from and when.
14. **Pickup pace and personal cooldown (2026-11-08, user decision, post-hoc).**
    Added after seeing that the user picks up often (median about 6.5 min
    between pickup episodes in `data/events-deven.jsonl`), so the memory's
    2x lift almost never passes. `loophole/pace.py`, settings `PACE_*` and
    `NUDGE_COOLDOWN_MIN/MAX` in `config.py`.
    - **Usual gap:** median time between the user's last 30 pickup episodes
      that started before the 10-min window (same episode rule as the memory;
      gaps of 60+ min are time away and skipped). Unknown until 5 gaps.
    - **Personal cooldown:** time between nudges = the usual gap, kept in
      [3, 20] min; 10 min (`NUDGE_COOLDOWN`) while it is unknown.
    - **Pace nudge:** a second live trigger next to the memory text. It fires
      when episodes in the last 10 min >= max(3, ceil(2 x 10 min / usual gap)),
      with the same "away / just returned" and cooldown checks. The nudge says
      the count and the usual pace. It is meant to make the user aware of
      their pace, not to predict a pickup.
    - It reads only phone records with ts < t and never feeds `when_score`.
    - eval_pickups: the live-rule row is now "memory text or pace", and a
      post-hoc row "pickups in last 10 min (pace)" scores the count. The
      settings were chosen before this row was first run. On the 2026-11-08
      log the pace was high at 1 of 231 moments, which was not an active
      moment, so the live rule still fired at 0 eval moments. We will not
      tune these settings on this file.
15. **Pickup memory silence diagnosed (2026-11-08, post-hoc, no threshold
    change).** The memory was "ready" all evening but never spoke (memory
    text on 0 of ~560 live score ticks). We checked whether that is a defect
    or a real absence of signal, on `data/events-deven.jsonl`. Numbers at the
    first check (log to ~21:20); a rerun at 21:46, with one more pickup, moved
    each by at most 0.01-0.2 and changed no conclusion.
    - **Pickups:** 56 episodes, 22 usable as templates, 14 of those tagged
      dev_testing. Most unusable pickups were filled in from ntfy while the
      app was off (about 16:50-18:28 and 19:39-21:16), so there was no laptop
      state before them; the rest had the laptop idle at the snapshot (rerun:
      27 app off, 8 idle).
    - **Base rate:** 118 of 459 past moments were followed by a pickup in
      30-180 s = 0.257, so the highest possible lift is about 3.9. A 2x lift
      is reachable in principle.
    - **No signal in laptop state:** AUC of nearest-template distance 0.535
      (templates within 5 min left out). Followed rate by distance band
      [0, .5), [.5, .75), [.75, 1): lift 1.06 / 0.94 / 1.13. Single-feature
      AUCs 0.45-0.55 (dwell_s, key_trend, scroll_trend, switches_5m, stall,
      where_gap, idle). Leaving out dev_testing templates (the deviation 12
      sensitivity row) gives the same AUC (0.537 at the rerun).
    - **Live ticks:** lift median about 1.0, max 1.58, 0 ticks at or above
      2.0 (rerun: max 1.79, still 0).
    - **Templates are broad:** most templates match 25-48% of all moments
      (a few, from unusual moments, 4-13%). 90% of moments are within the
      match distance of some template.
    - **Wider windows don't help:** they raise the base rate. With 30-600 s it
      is 0.69, so the highest possible lift drops to 1.45 and 2x becomes
      impossible.
    - **Not changed, on purpose:** `PRECURSOR_MIN_LIFT`, `PRECURSOR_MATCH_DIST`,
      `PRECURSOR_TARGET_S`, `PRECURSOR_MIN_FOLLOWED_EPISODES` and the
      dev_testing handling (tag, never a filter). With AUC near 0.5 any
      change that makes the memory speak would fire on noise, and any value
      picked now would be fitted after looking at this file.
    - **Changed (no gate):** `precursor.match` now returns `status`
      ("learning", "no_match", "no_pattern", "pattern"), a plain
      `status_text` (e.g. "no pattern yet ... 0.97x the usual rate (it needs
      2x)"), `match_share` (matched / known past moments) and `followed_rate`
      (matched_followed / matched). They use the same counters as the lift,
      from moments whose outcome is known before t, so the silence is visible
      instead of looking broken.
    - **Demo text cap (stricter only):** `match_text` (demo mode only) is set
      only when `match_share` <= 1 / `PRECURSOR_MIN_LIFT`. This is a math
      fact, not a tuned number: lift = (mf/m)/(F/K) <= K/m = 1/match_share,
      so when the matched moments cover more than half of all moments a 2x
      lift is impossible and "This looks like 14:44" only means "you are at
      your laptop". It uses no labels and can only hide text. On the rerun
      replay it hid match_text on 32 of 324 matched ticks.
    - eval_pickups prints a "memory signal check" block with these numbers
      (post-hoc diagnostic, evaluation only, never feeds the live rule).
    - **Limits:** one user, one day, 22 usable pickups (8 not dev_testing). A weak pattern of about 1.5x could stay hidden at this n.
      Keeping the 2x gate lets the memory speak if a real pattern shows up
      later. For this user, live nudges come from the pickup pace and the
      break reminder, not from the memory.
