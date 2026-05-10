# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added

- `tools/backtest.py` — four-scenario economic backtest covering the last year of observations. Runs the model at P90 (and seasonal Px) against historical inputs, computes monthly export revenue, grid buyback shortfall cost, and opportunity gap vs a perfect hindsight model. Outputs `tools/backtest_report.html` (gitignored; regenerate with `.venv/Scripts/python -m tools.backtest`).
- `DECISIONS.md` — backtest findings and winter deployment decision: model is not worth deploying June–August until a winter-specific fix or GloBird overnight charging is in place. Sep–May is solidly positive (~$102 net over 9 months in the full-charge scenario).

## [1.4.0] — 2026-05-11

### Added

- **Warm boundary zone (17–19°C)** — split from the old heating zone, now uses an empirical percentile table (P50=4.76, P75=6.00, P90=6.99, P95=8.05 kWh) rather than OLS regression. Investigation showed no weather signal (temperature, humidity, wind, Solcast, indoor temp, temp swing) explains consumption variance in this band; the variance is driven by human behaviour. Stratified test violation rate: 0% for this zone.
- `bom_temp_afternoon_max` dataset column — `MAX(max)` of BOM temperature sensor over 12:00–18:00 prior day (afternoon peak before the 6pm decision). Migration `004_add_afternoon_temp.sql` (schema v1.3.0 → v1.4.0). Added as part of investigating warm-boundary errors; retained as a useful feature candidate for future model iterations.
- `ts_12_prior` timestamp in `DayWindows` — 12:00 local prior day, used for afternoon temperature window.
- `warm_boundary_p50/p75/p90/p95` fields in `ModelConfig` and `config.yaml`.

### Changed

- Model renamed from three-zone to four-zone. `PredictResult.zone` now includes `"warm_boundary"` as a valid value.
- Solar credit removed from the export formula. After evaluation, adding `solcast × 0.21` to the formula produced an 86.6% safety violation rate; a capped variant reduced this but not to the ≤5% target with the data available. Conservative decision: no solar credit until live operation data allows proper evaluation. Solcast continues to be used as a cloud-cover proxy in the heating OLS model.
- `PredictResult.solar_forecast_wh` field removed.
- `DECISIONS.md` updated: three-zone entry superseded by four-zone entry; solar credit decision locked; open decision entry resolved.
- `tools/predictor.html` updated to four-zone model: warm boundary zone added, zone routing updated, percentile lookup corrected.
- `tools/nodered-flow.json` updated to four-zone model: WARM constant added, zone logic split at 17°C, empirical table handling unified for warm_boundary and mild zones.

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
