# Frontdesk leads engine

Builds a database of small businesses most likely to buy the Frontdesk AI receptionist (~$389/mo). Every exported
lead has verified contact data, a score, and "why this lead" reasons that cite stored evidence.

Read first: `docs/MASTER-SPEC.md` (the spec), `docs/ICP.md` (who we target), `docs/DECISIONS.md` (why things are the way they are).

## Operating principles (non-negotiable)

1. **Quality over quantity.** Every exported lead needs a verified phone, a score, and reasons that cite stored evidence.
   No fabrication. No guessed emails (only addresses published on the business's own site).
2. **Efficiency: cheap and wide first, expensive and narrow last** (spec §2 funnel). Never run an LLM or per-record paid API
   on the full universe. Cache everything (LLM calls by content hash). Jobs are idempotent and resumable. Every run has a hard
   cost cap. Report projected cost before any large run.
3. **Conversion engineering.** Find the biggest leak in the funnel, fix that first, change one variable at a time, measure
   outcomes. Score = Pain x Value x Reachability x Fit; zero reachability or fit = dead lead.
4. **Compliance and terms are hard gates.** Respect robots.txt, site terms, rate limits and source licenses. Stop on blocks; never
   evade bot protection. **Never scrape Google Search/Maps pages.** Suppression is permanent.

## Workflow

One stage at a time (spec §9): explore -> plan -> **wait for approval** -> implement with tests -> verify -> report
(done / tested / untested / gaps / open questions) -> **wait for "verified"**. Commit after each green stage. Ask before adding scope.

Current status: **Stage 1 done, awaiting "verified"** (US universe pilot: plumbing, HVAC, dental; 20k in Postgres).
Reports: `docs/reports/taxonomy-<release>.md`, `docs/reports/stage1-pilot-<release>.md`. Next: lead-explorer UI plan.

## Commands

All commands run from `leads/`. Requires [uv](https://docs.astral.sh/uv/) and Python 3.12 (uv fetches it if missing).

```bash
# install (creates .venv from uv.lock)
uv sync

# local Postgres: either docker (port 5433) ...
docker compose up -d leads-db
# ... or an existing Postgres 16: create role `leads` (createdb) and DBs frontdesk_leads, frontdesk_leads_test,
# then point LEADS_DATABASE_URL / TEST_DATABASE_URL at it.
cp .env.example .env            # edit if your Postgres differs

# test (DB tests skip unless TEST_DATABASE_URL is set; each creates and drops its own database)
uv run pytest
TEST_DATABASE_URL=postgresql://leads:leads@localhost:5433/frontdesk_leads_test uv run pytest

# lint + format + typecheck (all must pass before commit)
uv run ruff check .
uv run ruff format --check .     # `uv run ruff format .` to fix
uv run mypy                      # strict

# run
uv run leads config validate     # validate all YAML, print categories, LLM cost projection, config hash
uv run leads db check            # connect to LEADS_DATABASE_URL
uv run leads db migrate          # apply pending migrations (idempotent)

# Stage 1: T0 universe from Overture (open data, $0). Default categories = config pilot_sample.
uv run leads universe plan       # what would be read/written (reads only cached file footers)
uv run leads universe pilot --dry-run
uv run leads universe pilot      # census -> extract -> build -> load -> report (each step idempotent)
uv run leads universe census|extract|build|load|report [--categories plumbing,hvac] [--force]
```

Stage 1 data flow (all under `data/`, gitignored):
`raw/overture/<release>/manifest.json` (file bboxes) -> `census_*.parquet` -> `extract_US_<codes-hash>.parquet`
-> `derived/stage1/<release>/built_US_<cats>_<config-hash>.parquet` (EVERY input row, with `exclusion` / `dup_of`)
-> Postgres `businesses`/`phones`/`emails` (pilot sample only). A config change gives a new config hash, so a new
built file; nothing stale is reused. A first census + extract reads ~2 min from Overture's public bucket.

One-liner before every commit:
`uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`

## Layout

```
config/global.yaml            release pin, calling-hours guard rail, exclusions, vendor names, chain/dedupe
                              thresholds, directory/platform domains, pilot sample sizes, watch list
config/scoring.yaml           dimension weights, tiers, funnel fractions, signal weights
config/models.yaml            LLM model names, effort, prices, token estimates (cost projection)
config/categories/*.yaml      one file per category (file name == slug)
src/leads_engine/settings.py  env-based runtime settings (LEADS_* vars, .env)
src/leads_engine/config_loader.py  strict pydantic validation of config/ + config hash
src/leads_engine/signals.py   the ONLY list of signal names; config/reasons/openers must use these
src/leads_engine/log.py       structlog JSON logging; bind_run(run_id, stage) at job start
src/leads_engine/db.py        Postgres connect + migration runner; DuckDB for Parquet
src/leads_engine/cli.py       `leads` command (+ universe_commands.py)
src/leads_engine/runs.py      every job records a `runs` row; hard budget stop
src/leads_engine/universe/    overture.py (remote read), normalize.py (pure), build.py (exclude/chain/dedupe/
                              sample), load.py (Postgres upsert), report.py, pipeline.py (paths + steps)
migrations/NNN_*.sql          applied in order, each in a transaction, tracked in schema_migrations
data/                         raw/derived bulk files (Parquet). Gitignored. Never commit data.
```

## Rules for changing things

- **Model names are config, never hardcoded.** Change `config/models.yaml` or set `LEADS_SCORING_MODEL` / `LEADS_OPENER_MODEL`.
  An env override changes the model but not the prices used for projection, so update the YAML prices too when you switch models for real.
- **Config changes:** bump `version` in that file. `config_hash` changes automatically and every run records it.
- **New signal:** add it to `signals.py` first (with dimension, tier, opener_eligible), then reference it in config.
- **New category:** add `config/categories/<slug>.yaml`, keep ranks 1..N with no gaps, and remove it from `watch_list`.
- **Safety defaults:** `LEADS_DRY_RUN=true` and `LEADS_RUN_BUDGET_USD=0` by default. A run spends money only when both are changed.
- **Secrets** go in env vars only (never YAML, never git). Add them in the stage that first needs them.
- **Migrations** are append-only: never edit an applied file; add a new numbered one.
- **Chain rules:** `chain_name_patterns` match the START of the normalized name; `chain_name_contains` match anywhere.
  Don't add generic-sounding brands ("Gentle Dental"): the website-domain rule catches real chain locations and the
  name would also hit independents. Review the chain table in the Stage 1 report after any change.
- **Overture release** is pinned in `global.yaml`. Bumping it requires `leads universe census` and re-confirming codes.
- **Tests that need fixtures** (saved HTML etc.) go in `tests/fixtures/`. Never hit the network in tests.

## Never

- Scrape Google Search or Google Maps pages (use official APIs within terms, or open bulk datasets).
- Guess emails by pattern, or collect personal (non-business) emails.
- Run an LLM or a paid per-record API over the whole universe.
- Export a record that failed the compliance gate, or delete or modify a `suppression` row.
- Put a reason or opener in front of a user that doesn't trace to stored evidence.
