# Focus Guard evaluation protocol (pre-registered)

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
