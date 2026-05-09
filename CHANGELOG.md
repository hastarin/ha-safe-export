# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

## [1.3.0] — 2026-05-09

### Added

- `data_gap` flag on `daily_observations`: rows with known sensor outages are marked `data_gap = 1` rather than deleted, preserving chronological continuity for downstream code. Gaps are configured via `data_gap_dates` in `config.yaml`.
- Migration `003_add_data_gap.sql` (schema v1.2.0 → v1.3.0).
- Interactive browser-based predictor (`tools/predictor.html`) — embeds model coefficients and lets you explore predictions without running Python.
- Node-RED automation flow (`tools/nodered-flow.json`) — runs the three-zone model at 6pm daily and writes results to HA helpers.

### Changed

- `absence_period` column renamed for clarity; provider logic neutralised in `predict()` (provider retained in dataset as stratification variable only).
- Node-RED flow default output switched from P75 to P90.

## [1.2.0] — 2026-05-07

### Added

- Three-zone linear model (`src/model.py`) with `predict()` callable: Heating zone (R²=0.77 with Solcast, R²=0.71 temp-only fallback), Mild zone (empirical percentile table), Cooling zone (R²=0.37 with humidity, improves with more data).
- Held-out test MAE: 1.75 kWh; P95 buffer covers 92% of test residuals.
- `bom_humidity_mean`, `bom_humidity_max`, `median_indoor_humidity` columns — humidity features enabling the cooling model.
- Migration `002_add_humidity.sql` (schema v1.1.0 → v1.2.0).
- `PredictInputs` and `PredictResult` dataclasses; `confidence` parameter (P50/P75/P90/P95).
- Model coefficients externalised to `config.yaml` (`model:` section) — no retraining required to update coefficients.
- 23 model unit and regression tests.

### Changed

- Safe export values now expressed in **Wh** throughout (previously kWh in some outputs).

## [1.1.0] — 2026-05-07

### Added

- BOM weather station columns: `bom_temp_mean`, `bom_temp_max`, `bom_feels_like_mean`, `bom_rain_since_9am`, `bom_wind_mean`, `bom_gust_max`.
- Solcast PV forecast column: `solcast_forecast_tomorrow_wh` (available from 2024-10-17; NULL before).
- Indoor climate columns: `median_indoor_temp`, `max_indoor_temp`.
- Migration `001_add_weather_forecast.sql` (schema v1.0.0 → v1.1.0).
- Background analysis docs: `docs/analysis/ENERGY_ANALYSIS.md` (zone model rationale and statistics) and `docs/analysis/PHASE_1_SCHEMA_UPDATE.md` (sensor coverage log).

## [1.0.0] — 2026-05-06

### Added

- Initial dataset extraction (`src/extract.py`): reads the HA recorder SQLite DB read-only, produces `daily_observations` with one row per night covering the 6pm–11am window.
- Core energy columns: `solar_wh`, `grid_import_wh`, `grid_export_wh`, `battery_charged_wh`, `battery_discharged_wh`, balance-derived `consumption_wh`, and SoC bookends.
- Timezone-aware window math (`src/windows.py`) — all boundaries computed in configured local time and converted to UTC via `zoneinfo`; never hardcoded offsets.
- Incremental extraction with `INSERT OR REPLACE`; `--rebuild` flag for full re-extraction.
- Schema DDL (`src/schema.sql`) with `extraction_meta` version tracking.
- Three validation fixtures (Feb 7 2026, Mar 20 2026, Jul 17 2025) encoded in `tests/fixtures.py`; all pass.
- `Config` dataclass and `load_config()` reading from `config/config.yaml`; `config.example.yaml` template for new installations.
- Provider period history, absence periods, and data gap dates configurable via YAML.
- Design docs: `docs/SPEC.md`, `docs/DATASET.md`, `docs/DECISIONS.md`.
