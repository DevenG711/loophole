# Focus Guard

Hackathon project (Sundai #148, team of 2). Read [PLAN.md](PLAN.md) for what we're building and why.

## Commands

- Tests: `.venv/bin/python -m pytest -q` (live API test: `LIVE=1 .venv/bin/python -m pytest -m live`)
- Menu-bar app: `.venv/bin/python -m focusguard.menubar` (`--demo` for stage)
- Dashboard: `.venv/bin/streamlit run dashboard/app.py`
- Fake data: `.venv/bin/python evaluation/fake_data.py --out data/events-fake.jsonl --people 2 --hours 4`
- Evals: `evaluation/eval_titles.py`, `evaluation/eval_pickups.py --events data/events-*.jsonl`
- More: [docs/run.md](docs/run.md), [docs/phone-setup.md](docs/phone-setup.md)

## Rules

- All parts share one JSONL log format: [focusguard/events.py](focusguard/events.py). All thresholds live in [focusguard/config.py](focusguard/config.py).
- `when` features must never read `phone` records (they are the label), and nothing may read records at or after the time being scored. Tests in `tests/test_eval.py` and `tests/test_scoring.py` guard this.
- BehaviorGPT catalog `cat_35a0afc5ae400da9f74d27e2` = [focusguard/catalog.py](focusguard/catalog.py) (`f_*` focus, `d_*` distraction). The API key has 1 catalog slot left: don't upload catalogs. Always pass `register_event=False`.
- BehaviorGPT cannot see stalling, time gaps or time of day (tested). Stall detection is local; BehaviorGPT only answers "where next".
- Never record keystroke contents, only counts.
- `engine.score_now()` appends a `score` record to the file it reads. Don't point it at fake or eval data.
- Anything from `evaluation/fake_data.py` is SYNTHETIC. Never present it as results.
