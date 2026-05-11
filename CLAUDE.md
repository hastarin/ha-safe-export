# CLAUDE.md

## Project: Home Assistant Battery Export Predictor

You are working on a system that predicts how much energy can be safely exported from a home battery during the 6–9pm peak period each evening, while ensuring sufficient charge remains to carry the home through to 11am the following day.

Installation-specific configuration (battery capacity, sensor names, provider history, absence periods) lives in `config/config.yaml` (gitignored). The example template is `config/config.example.yaml`.

## Current phase

**Phase 2: Modelling — complete.** Phase 1 (data extraction) is complete at schema v1.4.0. Phase 2 is complete — `src/model.py` implements a four-zone predictor with a `predict()` function. Economic backtesting is done (`tools/backtest.py`); deployment is deferred to September 2026.

Phase 3 (Home Assistant integration) comes next. The dataset DB is the contract between Phase 1 and Phase 2; the trained model + `predict()` function is the contract between Phase 2 and Phase 3.

## Read these before writing code

- `docs/SPEC.md` — what we're predicting and the success criteria
- `docs/DATASET.md` — **the canonical data spec.** Sensor mappings, window definitions, column formulas, validation samples
- `docs/DECISIONS.md` — why each design choice was made (do not "improve" these without strong justification and discussion)
- `docs/analysis/` — background analysis docs useful for modelling context: `ENERGY_ANALYSIS.md` (three-zone model selection rationale, statistical findings) and `PHASE_1_SCHEMA_UPDATE.md` (sensor coverage and schema evolution log)

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

## Conventions

- Python 3.11+
- Type hints on all public functions
- `ruff` for lint, `pytest` for tests
- All datetimes are timezone-aware; never use naive datetimes
- SQL parameter binding always (never string interpolation into queries)
- Connect to the HA DB read-only: `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`
- The dataset DB is the project's own SQLite file, separate from the HA DB

## Testing

Three known-good validation fixtures (Feb 7 2026, Mar 20 2026, Jul 17 2025) are documented in `docs/DATASET.md § Validation samples` and encoded in `tests/fixtures.py`. The extraction script must reproduce these values exactly when run against the user's HA database. Tolerances: ±0.1 for percentages and temperatures, ±1 Wh for energies.

A passing run of `pytest` is the bar for any change to extraction logic.

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

```text
ha-safe-export/
├── CLAUDE.md
├── README.md
├── config/
│   ├── config.example.yaml  # template — copy to config.yaml and fill in your values
│   └── config.yaml          # gitignored; your actual sensor names and history
├── docs/
│   ├── SPEC.md
│   ├── DATASET.md
│   ├── DECISIONS.md
│   └── analysis/
│       ├── ENERGY_ANALYSIS.md       # zone model rationale and statistical findings
│       └── PHASE_1_SCHEMA_UPDATE.md # sensor coverage and schema evolution log
├── src/
│   ├── __init__.py      # defines __version__
│   ├── config.py        # Config dataclass + load_config()
│   ├── extract.py       # build/refresh the dataset DB
│   ├── schema.sql       # canonical DDL for the dataset DB
│   ├── model.py         # four-zone predictor + predict()
│   ├── windows.py       # timezone-aware window math
│   └── migrations/      # incremental schema updates (applied automatically on startup)
│       ├── 001_add_weather_forecast.sql   # v1.0.0 → v1.1.0
│       ├── 002_add_humidity.sql           # v1.1.0 → v1.2.0
│       ├── 003_add_data_gap.sql           # v1.2.0 → v1.3.0
│       └── 004_add_afternoon_temp.sql     # v1.3.0 → v1.4.0
├── tests/
│   ├── __init__.py
│   ├── conftest.py      # shared test fixtures (test Config)
│   ├── fixtures.py      # expected values from DATASET.md
│   ├── test_extract.py
│   └── test_model.py
├── tools/
│   ├── backtest.py          # economic backtest; outputs backtest_report.html/.json
│   ├── nodered-flow.json    # Node-RED flow; runs predict() at 6pm, writes to HA helpers
│   └── predictor.html       # browser-based model explorer (no Python required)
├── data/                # gitignored; holds the dataset DB
├── CHANGELOG.md         # version history; update after schema or model changes
└── pyproject.toml
```

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

## When in doubt, ask

Don't make these changes without discussion:

- Modifying the schema of `daily_observations`
- Switching to a different source sensor
- Changing how a column is computed
- Adding new "convenience" columns or features that weren't asked for
- Changing the window boundaries

Sensor names come from `config.yaml` — never hardcode them. If a configured sensor is not found in the HA DB, surface a clear error rather than silently substituting.
