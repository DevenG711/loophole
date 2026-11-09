# Loophole

Hackathon project (Sundai #148, team of 2). Read [PLAN.md](PLAN.md) for what we're building and why.

## Commands

- New-user setup: `bash setup.sh` then `bash start.sh` (what the website's zip steps say; see [README.md](README.md))
- Tests: `.venv/bin/python -m pytest -q` (live API test: `LIVE=1 .venv/bin/python -m pytest -m live`)
- Menu-bar app: `.venv/bin/python -m loophole.menubar` (`--demo` for stage)
- Dashboard: `.venv/bin/streamlit run dashboard/app.py`
- Fake data: `.venv/bin/python evaluation/fake_data.py --out data/events-fake.jsonl --people 2 --hours 4`
- Evals: `evaluation/eval_titles.py`, `evaluation/eval_pickups.py --events data/events-*.jsonl`
- More: [docs/run.md](docs/run.md), [docs/phone-setup.md](docs/phone-setup.md)

## Rules

- All parts share one JSONL log format: [loophole/events.py](loophole/events.py). All thresholds live in [loophole/config.py](loophole/config.py).
- `when` features must never read `phone` records (they are the label), and nothing may read records at or after the time being scored. Tests in `tests/test_eval.py` and `tests/test_scoring.py` guard this.
- BehaviorGPT catalog `cat_35a0afc5ae400da9f74d27e2` = [loophole/catalog.py](loophole/catalog.py) (`f_*` focus, `d_*` distraction). The API key has 1 catalog slot left: don't upload catalogs.
- BehaviorGPT can keep a per-user memory: `client.events.track(user_id, View(item_id), timestamp=...)` stores catalog-item events (not `ExternalItem`s), and `complete(user_id=...)` reads that user's stored events as the history (pass no `history` with it). `register_event` only controls whether a `complete()` call's own search is recorded for that user; it trains nothing. Stateless calls (explicit `history`, no `user_id`) keep `register_event=False`.
- BehaviorGPT cannot see stalling, time gaps or time of day from a stateless `history` (tested). Tested live 2026-11-08 (29 calls, throwaway user ids): once ingested (5–90 s lag; before that it returns a cold-start ranking with `d_tiktok` #1), `complete(user_id)` ranks exactly like a stateless call with the same items: no personal learning, and any `d_*` in the history goes to #1. Timestamps may shift rankings (one call). So per-user tracking is not used; the app's "learn from each pickup" is local ([loophole/precursor.py](loophole/precursor.py)). Stall detection is local; BehaviorGPT only answers "where next".
- The pickup memory ([loophole/precursor.py](loophole/precursor.py)) may read PAST phone records (ts < t) to show time since the last pickup, but never uses them for similarity and never feeds them to `when_score`. Its `where_gap` comes from laptop `app` records only (`where_score.app_records`). "Grabbed phone" button pickups are not memory episodes. Its thresholds are `PRECURSOR_*` in config, set before looking; it stays out of the nudge rule (and the amber dot) until 5+ usable pickups (`PRECURSOR_MIN_EPISODES`), and labels its answers an early, less accurate guess until 15 (`PRECURSOR_CONFIDENT_EPISODES`). Usable = laptop in use before the pickup; every real pickup still counts as an outcome. Pickups tagged `dev_testing` are tagged, never filtered. `match()` also returns `status`/`status_text` (learning, no_match, no_pattern, pattern) so its silence is visible, and hides the demo `match_text` when matched moments cover more than 1/`PRECURSOR_MIN_LIFT` of all moments (a 2x lift is then impossible). On the 2026-11-08 log it found no signal (AUC ~0.54); its gates were deliberately not loosened (PROTOCOL.md deviation 15).
- The pickup pace ([loophole/pace.py](loophole/pace.py)) is the second live nudge trigger (pickups in the last 10 min vs your usual gap) and sets the personal cooldown (usual gap, kept in 3–20 min). It reads only past phone records, uses the memory's episode rule, and never feeds `when_score`. Settings `PACE_*`, `NUDGE_COOLDOWN_MIN/MAX`; PROTOCOL.md deviation 14.
- Never put an API key in the repo. Downloads reach BehaviorGPT through the proxy ([proxy/README.md](proxy/README.md): Vercel function `site/api/rank.js`, Cloudflare backup in `proxy/`, shared rules in `site/api/_proxy.js`; the key is a server secret, allowing only the stateless ranking call on our catalog); `config.PROXY_URL` is used when `.env` has no `UNBOXAI_API_KEY`.
- Never record keystroke contents, only counts.
- `engine.score_now()` appends a `score` record to the file it reads. Don't point it at fake or eval data.
- Anything from `evaluation/fake_data.py` is SYNTHETIC. Never present it as results.
