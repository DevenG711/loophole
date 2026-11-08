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
