# CLAUDE.md

## Project: Home Assistant Battery Export Predictor

You are working on a system that predicts how much energy can be safely exported from a home battery during the 6–9pm peak period each evening, while ensuring sufficient charge remains to carry the home through to 11am the following day.

Installation-specific configuration (battery capacity, sensor names, provider history, absence periods) lives in `config/config.yaml` (gitignored). The example template is `config/config.example.yaml`.

## Current phase

**Phase 3: Home Assistant integration — underway.** Phase 1 (data extraction) and Phase 2 (modelling, `src/model.py` four-zone predictor + `predict()`, economic backtest in `tools/backtest.py`) are both complete. The dataset is at **schema v1.6.0** — Phase 3 work has begun adding the live-flow forecast inputs (`forecast_temp_mean`/`forecast_humidity_mean`, v1.5.0 → v1.6.0) so the dataset can eventually be scored on the same forecast the live flow decides on. Full deployment is deferred to September 2026.

The dataset DB is the contract between Phase 1 and Phase 2; the trained model + `predict()` function is the contract between Phase 2 and Phase 3.

## Read these before writing code

- `docs/SPEC.md` — what we're predicting and the success criteria
- `docs/DATASET.md` — **the canonical data spec.** Sensor mappings, window definitions, column formulas, validation samples
- `docs/DECISIONS.md` — why each design choice was made (do not "improve" these without strong justification and discussion)
- `docs/analysis/` — background analysis docs useful for modelling context: `ENERGY_ANALYSIS.md` (three-zone model selection rationale, statistical findings), `PHASE_1_SCHEMA_UPDATE.md` (sensor coverage and schema evolution log), and `LIVE_INTEGRATION.md` (Phase 3 deployment surface: the five Node-RED model-input sensors, the `grid_export_*` execution chain, and the long-term-statistics recording requirement)

## Critical gotchas

These will cost hours to rediscover. Trust them.

### 1. The HA database is in UTC, not local time

The `start_ts` column in `statistics` is Unix UTC. SQLite's `datetime(ts, 'unixepoch', 'localtime')` modifier silently does nothing because the HA system is configured to UTC. **All window boundaries must be computed in Australian local time and converted to UTC explicitly.**

Use the timezone from `config.yaml` via `cfg.timezone` (a `ZoneInfo` object). Do not hardcode UTC offsets — DST transitions vary by location and you will get this wrong if you assume one or the other.

### 2. Don't trust the consumed-power sensor if it has a short history

The `solarnet_power_load_consumed` sensor only goes back to July 2024. The config uses `solarnet_power_load` instead — same underlying measurement, sign-flipped (consumption is stored as negative), available from system commissioning. Apply `ABS(mean)` to recover the magnitude.

### 3. Don't compute consumption from integrated power

Hourly mean-power integration introduces 5–15% noise vs the energy balance. Compute `consumption_wh` as:

```text
consumption_wh = solar_wh + grid_import_wh + battery_discharged_wh
               − grid_export_wh − battery_charged_wh
```

This is what the HA Energy Dashboard does internally. Keep the integrated value as `consumption_wh_load` in a separate column for QA only.

### 4. Use the PV-only sensor, not the inverter output sensor

On Fronius systems, `sensor.solar_power` includes battery discharge — not pure PV. The config uses `sensor.solarnet_power_photovoltaics` (instantaneous W; integrate `MAX(mean, 0) × 1h` to get Wh). Check the equivalent on other inverter brands.

### 5. Cumulative-sum sensors: use `sum`, not `state`

The `state` column is the raw meter reading; `sum` is the HA-corrected cumulative value (handles meter resets). Always use `sum`. Window energy = `sum(end) − sum(start)`.

### 6. The live flow depends on 5 sensors being in long-term `statistics` — verify before trusting any live audit

The live Node-RED predictor (`tools/nodered-flow.json`) reads exactly **5 input sensors**: `overnight_forecast_temp_mean`, `overnight_forecast_humidity_mean`, `solcast_pv_forecast_forecast_tomorrow`, `byd…state_of_charge`, `byd…soc_minimum`. To reconstruct or backtest what the live system actually decided on a past night, **all five must be in long-term `statistics`** (the `states` table only retains ~8 days).

As of an audit on 2026-05-31, **three were silently not being recorded** and had to be fixed in HA config: the two `overnight_forecast_*` sensors were blocked by a `recorder:` `exclude: entity_globs` rule, and `byd…soc_minimum` was provided by the Fronius integration with **no `state_class`** (making it ineligible for statistics; fixed via `customize:`). Do not assume a sensor is recorded just because it exists — check `statistics_meta` for it. A sensor that exists in `states` but not `statistics`, or that goes `unknown`/`unavailable` at the top of the hour, will leave gaps you cannot recover.

**Also: the live temp input is a _forecast_ (Truganina hourly), not BOM.** The dataset's `bom_temp_mean` is BOM **actuals** over the same 6pm–11am window — a _different source_. Never substitute `bom_temp_mean` for the live flow input when reasoning about what the flow output; they can differ by several °C, which flips the export decision. See `docs/DECISIONS.md` ("model-quality benchmark, not a live-performance predictor") and `docs/analysis/LIVE_INTEGRATION.md`.

## Conventions

- Python 3.11+
- Type hints on all public functions
- `ruff` for lint, `pytest` for tests
- All datetimes are timezone-aware; never use naive datetimes
- SQL parameter binding always (never string interpolation into queries)
- Connect to the HA DB read-only: `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`
- The dataset DB is the project's own SQLite file, separate from the HA DB
- Fix markdown lint errors via the `fix-markdown` skill (committed at `.claude/skills/fix-markdown/`, so it's available in every clone/cloud session), not by hand — never hand-align a table's pipes (models reliably miscount characters against MD060's "aligned" style; the skill's script re-renders tables deterministically instead)
- Windows console is cp1252: a script that `print()`s non-ASCII (e.g. the `α` in a backtest scenario label) raises `UnicodeEncodeError`. The project tools write UTF-8 files fine — this only bites ad-hoc scripts printing to the terminal. Prefix such runs with `PYTHONIOENCODING=utf-8` (the dev box also sets this as a user env var, but don't rely on that being present)
- GitHub operations: prefer the `gh-axi` skill if it's installed. Otherwise fall back to plain `gh`. Otherwise use native GitHub MCP tools if the session has them. Nested/structured JSON bodies that don't fit `gh-axi`'s flat `--field` pairs are the one standing exception — use `gh api ... --input -` for those.
- When fetching an issue or PR body with `gh-axi issue view` / `pr view`, always pass `--full` on the first fetch. The default view truncates the body, and re-fetching without `--full` first just doubles the round-trip for no reason.
- Use the Bash tool (not PowerShell) for shell commands in this project.
- When writing commit messages, NEVER add your agent name as co-author or mention agent involvement.
- When writing or substantially editing long Markdown files (`docs/*.md`), put each full sentence on its own line — preserve normal Markdown structure, but avoid wrapping multiple sentences onto one physical line.
- When fixing a bug in extraction or model logic, reproduce it first: write a failing test (extend `tests/fixtures.py`/`test_extract.py`/`test_model.py`, or add a minimal repro if none of the existing fixtures cover it) before writing the fix, so the fix is proven against the real defect rather than a guess.
- `config/config.yaml` is gitignored because it contains personal sensor names, battery details, and absence-period history. Never print/echo its contents, paste it into a commit message, PR body, or issue, or otherwise let its values leave the local session.
- Route open-ended searches ("find where X is defined", "which files reference Y") through the `Explore` subagent rather than reading/grepping files directly in the main thread. Its results come back as a short summary instead of full file dumps, which keeps the main session's context small — this matters because sessions in this repo run long (extraction, modelling, tests, docs all in one sitting) and unnecessary full-file reads are what drives context/token usage up, not skills or agents themselves. Reserve direct Read/Grep for when you already know the exact file and location.
- Prefer minimal, scoped edits. Do not over-engineer, add subagents, or edit correct sentences unless explicitly asked.

## Git Workflow

- After merging or closing a PR, never push further changes to that branch; apply follow-up edits fresh on `main` and delete the stale branch.
- Never use heredocs for commit messages; use `-m` flags or a temp file to avoid stray characters leaking in.

## Testing

Three known-good validation fixtures (Feb 7 2026, Mar 20 2026, Jul 17 2025) are documented in `docs/DATASET.md § Validation samples` and encoded in `tests/fixtures.py`. The extraction script must reproduce these values exactly when run against the user's HA database. Tolerances: ±0.1 for percentages and temperatures, ±1 Wh for energies.

A passing run of `pytest` is the bar for any change to extraction logic. Before committing, run lint and the full test suite; only commit when both pass.

## Common commands

```bash
# Run all tests
.venv/Scripts/python -m pytest

# Incremental extraction (append new days since last run)
.venv/Scripts/python -m src.extract data/home-assistant_v2.db

# Full rebuild of the dataset DB
.venv/Scripts/python -m src.extract data/home-assistant_v2.db --rebuild

# Economic backtest (outputs tools/backtest_report.html and tools/backtest_report.json)
.venv/Scripts/python -m tools.backtest
```

## Repository structure

See [Project structure](README.md#project-structure) in `README.md` for the annotated file tree.

Note on `src/migrations/`: these are a historical record and are **not** auto-applied. `schema.sql` is the source of truth; `--rebuild` recreates the DB from it. They exist only for hand-upgrading an existing old DB in place.

## Incremental behaviour

The extract script must be incremental:

1. On startup, ensure the dataset DB exists (create from `schema.sql` if not).
2. Read `MAX(date) FROM daily_observations`. Default to `2023-11-28` (first complete window after commissioning) if empty.
3. Compute and `INSERT OR REPLACE` rows from `MAX(date) + 1` through yesterday (today's window is incomplete).
4. Update `extraction_meta` with `last_full_extraction = now()`.

Provide a `--rebuild` flag that drops and re-extracts all rows. Useful when methodology changes.

## Changelog

`CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format. Update it whenever:

- The dataset schema version changes (new migration added)
- Model coefficients are retrained and updated in `config.yaml`
- A new tool or integration artifact is added
- A significant bug fix lands in extraction or model logic

Add the new entry under `## [Unreleased]` at the top; move it to a dated version heading when tagging a release.

**Version bumping:** the single source of truth is `__version__` in `src/__init__.py` (`pyproject.toml` reads it dynamically; convention: it tracks the dataset schema version). Bump it in the same commit as any behavioural change to extraction or model logic — it is stamped into every dataset row as `extraction_version`, and a stale value makes rows unattributable to the logic that produced them.

## Model coefficients are duplicated in two places — keep them in sync

The four-zone model is implemented **twice**. Any change to coefficients, percentile tables, or buffers must be applied to both or they silently diverge:

1. `src/model.py` + `config/config.yaml` — the canonical Python predictor (the Phase 2→3 contract). `config.yaml` holds the numbers; `tests/conftest.py` carries a synced copy used by the tests.
2. `tools/nodered-flow.json` — the **live** 6pm export automation. Embeds coefficients inline in the "Four-zone model" function node.

Retrain with `tools/retrain.py` (needs the `tools` extra — `pip install -e ".[tools]"`, numpy), review, then update `config.yaml`, `tests/conftest.py`, and `nodered-flow.json` together. `retrain.py`'s output includes a consumption-floor check (`MIN_CONSUMPTION_KWH`, see `docs/DECISIONS.md` "Consumption-floor clamp on OLS zones") — if it reports the floor would bind on any historical night, the fit is suspect; investigate before deploying.

Sync between `config.yaml`/`tests/conftest.py`, `nodered-flow.json`, and the `model.py` confidence ladder is enforced by `tests/test_sync.py`.

**Redeploy after editing these:** changing `nodered-flow.json` in the repo does NOT update what is running. You must re-import the flow into Node-RED for changes to take effect.

`tools/nodered-flow.json` is intended to **faithfully mirror `src/model.py`** — it is the low-cost stand-in for the eventual Home Assistant integration, so it should reproduce the canonical model exactly (coefficients, percentile tables, buffers, and the confidence buffer-scale ladder `{0.50: 0.33, 0.75: 0.58, 0.90: 0.88, 0.95: 1.00}`). Do not let it drift into its own operating policy; if behaviour needs to change, change `model.py` and propagate.

## When in doubt, ask

Don't make these changes without discussion:

- Modifying the schema of `daily_observations`
- Switching to a different source sensor
- Changing how a column is computed
- Adding new "convenience" columns or features that weren't asked for
- Changing the window boundaries

Sensor names come from `config.yaml` — never hardcode them. If a configured sensor is not found in the HA DB, surface a clear error rather than silently substituting.
