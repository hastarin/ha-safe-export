# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Fixed

- **`load_config` raises `ValueError` on missing required fields, instead of a raw `KeyError`.** Every required key in the `battery`, `sensors`, and `model` sections, plus the top-level `timezone` and `providers` keys, now goes through a small `_require`/`_require_section` helper that raises `ValueError(f"{path}: missing required key {section}.{key}")` (or `missing required section {key}` for an absent block), naming the config file and the dotted key path. Optional keys (`solcast`, `guests`, `median_*`, `forecast_*`, `absence_periods`, `data_gap_dates`) are unaffected. Makes the `load_config` docstring's existing claim ("Raises ValueError on missing required fields") true. New tests cover a missing scalar key, a missing section, and the existing valid-example-config control case.
- **`extract.py`'s incremental extraction boundary used the machine-local clock instead of the configured timezone.** `yesterday = date.today() - timedelta(days=1)` in `extract_all` computed the extraction upper bound from the host's local date rather than `cfg.timezone`, an inconsistency with the project's otherwise strict timezone discipline. Harmless on a Melbourne-local box; would extract an incomplete window or lag a day if run on a UTC machine (e.g. a container). Replaced with `datetime.now(cfg.timezone).date() - timedelta(days=1)`.

### Added

- **`tests/test_sync.py`** — coefficient-parity test guarding against drift between the hand-copied coefficient sets: `tools/nodered-flow.json`'s "Four-zone model" function node vs `tests/conftest.py`'s `test_cfg` fixture, the ladder in `tools/nodered-flow.json` vs `CONFIDENCE_SCALE` in `src/model.py`, and (when `config/config.yaml` exists locally) `test_cfg` vs the real config. Fails with a message naming the drifted key on any digit mismatch; skips cleanly where `config.yaml` is absent (CI/forks).
- **CI workflow** (`.github/workflows/ci.yml`): runs `ruff check .` and `pytest` on every push to `main` and on pull requests (Python 3.11, `ubuntu-latest`). Extraction fixture tests and other tests requiring gitignored personal data skip cleanly in this environment rather than failing.
- CI status badge in `README.md`.
- **`tests/test_backtest.py`** — hand-computed unit coverage for `tools/backtest.py`'s pure economics functions (`season`, `seasonal_confidence`, `one_year_before`, `adjusted_soc`, `baseline_trough_soc`, `accum_night`, `_capture`), including the export-caused-breach and already-breached-baseline shortfall-attribution cases. Closes audit finding T4 (issue #8). Tests pin `BATTERY_WH`/`HARD_FLOOR_FRAC`/`SOFT_FLOOR_FRAC`/`EXPORT_RATE`/`BUYBACK_RATE` via monkeypatch rather than relying on module defaults, since `main()` mutates the first three from `config.yaml`.
- **`tests/test_windows.py`** — unit tests for `src/windows.py`'s DST handling (Australia/Melbourne): a normal winter (AEST) and summer (AEDT) date, spring-forward (2025-10-05, 16-hour window / 15-hour `ts_18_prior`→`ts_10_today` gap) and fall-back (2026-04-05, 18-hour window / 17-hour gap), ordering invariants, and an assertion that the boundary hours never coincide with the 02:00-03:00 transition window. Expected timestamps are computed with a fixed-offset `datetime.timezone`, independent of the `zoneinfo` path under test.

### Removed

- **`tools/predictor.html`** — unused for months; deleted. Shrinks the model-coefficient sync surface from three hand-synced copies to two (`src/model.py` + `tools/nodered-flow.json`). `README.md`, `CLAUDE.md`, and `DECISIONS.md` updated to describe the two-copy sync rule.

## [1.6.0] — 2026-07-03

Version alignment release: `__version__` and the package version now both track the dataset schema version (1.6.0), and `pyproject.toml` reads the version dynamically from `src/__init__.py`.
Everything below shipped incrementally between 2026-05-11 and 2026-06-21.

### Fixed

- **Cum-delta window boundary off-by-one** in `src/extract.py`. The four cumulative-sum energy columns (`grid_import_wh`, `grid_export_wh`, `battery_charged_wh`, `battery_discharged_wh`) were read at `sum @ 18:00 prior − sum @ 11:00`, which spans `19:00 prior → 12:00 today` because HA stores the `sum` for bucket `T` as the reading at `T+1h`. Corrected to `sum @ 17:00 prior − sum @ 10:00`. Missed the 18:00–19:00 hour and wrongly included 11:00–12:00 (significant under GloBird midday charging — one night showed 2,584 Wh grid import vs ~43 Wh actual). `windows.py`, `DATASET.md`, and `DECISIONS.md` updated; `ts_11_today` removed from `DayWindows`; `extraction_meta.schema_version` corrected to 1.4.0. Dataset rebuilt; all three validation fixtures re-verified.

### Added

- **`forecast_temp_mean` + `forecast_humidity_mean` dataset columns** (schema 1.5.0 → 1.6.0, migration `006_add_forecast_inputs.sql`). The live-flow forecast inputs (`overnight_forecast_temp_mean` / `overnight_forecast_humidity_mean`), read at the 6pm decision point (the 18:00-local prior-day bucket, matching the `bom_temp_mean` window convention; 3h fallback if that bucket is missing). These are the **forecast** counterparts to the BOM **actuals** in `bom_temp_mean` / `bom_humidity_mean` — the two sources can differ by several °C and flip the export decision, so they are kept as separate columns and never coalesced. NULL before the sensors began recording to long-term statistics (the recorder/`state_class` fixes from the 2026-05-31 audit; first readable row is the morning of 2026-06-01). Extraction-only: the backtest still scores on `bom_temp_mean`; a forecast-scored scenario is deferred to the next retrain. Two new optional sensors in `config.yaml`; `config.example.yaml`, `tests/conftest.py`, `tests/fixtures.py` (golden fixtures assert NULL), and a new recent-night extraction test updated. Dataset rebuilt (11 nights populated). A spot-check measured a ~1.73 °C mean-abs forecast-vs-actual gap (~1 kWh/°C in the heating zone) over the first 12 nights — confirming the mismatch is material.
- **`docs/analysis/LIVE_INTEGRATION.md`** — documents the Phase 3 live deployment surface: the five HA sensors the Node-RED flow reads as model inputs, the full `grid_export_*` execution chain (Node-RED → `input_text` → target → `script.grid_export_start` → battery), the multiple non-agreeing SoC floors, and the long-term-`statistics` recording requirement for auditability. Captures findings from a 2026-05-31 live-database investigation.
- `tools/retrain.py` — refits the four-zone model from the dataset DB (held-out validation + proposed `config.yaml` block). Requires the new optional `tools` dependency group (numpy).
- **`evening_grid_export_wh` dataset column** (schema 1.4.0 → 1.5.0, migration `005_add_evening_grid_export.sql`). Grid-export `sum` delta over the 6–9pm peak (`produced.sum @ 20:00 − @ 17:00 prior`; `ts_20_prior` added to `DayWindows`). A proxy for deliberate battery-to-grid export, used by the backtest to reconstruct the no-export overnight SoC trough (real exports depress `min_soc_overnight`). Caveat: includes any PV→grid in summer evenings.

### Changed

- **Model retrained (2026-05-22)** after the cum-delta fix, on 858 trainable nights. New coefficients/percentiles/buffers in `config.yaml` (and `tests/conftest.py` test reference). Heating R² 0.77 → 0.83; cooling R² 0.37 → 0.52; P95 buffers shrank (heating 3.562 → 2.649, cooling 3.136 → 2.431) as the boundary fix removed spurious 11:00–12:00 noise. Held-out violation rate 0.8% heating / 0.0% cooling. `confidence_scale` in `src/model.py` recomputed (0.31/0.58/0.87 → 0.33/0.58/0.88; negligible drift).
- **Zone bands reviewed and retained at 17/19/21 °C.** The retrain made the mild (19–21 °C) table sit above the warm-boundary (17–19 °C) table; a 1 °C consumption profile confirmed this is correct (the minimum sits in 17–19 °C; 19–21 °C is the cooling shoulder). "mild" is a documented misnomer; bands unchanged. See new DECISIONS.md entry.
- `pyproject.toml` — added optional `tools` dependency group (numpy) for dev/analysis scripts; runtime deps unchanged.
- **`tools/nodered-flow.json` and `tools/predictor.html` resynced** to the retrained coefficients/percentiles/buffers. The Node-RED confidence buffer-scale ladder was aligned to `model.py` (`{0.50: 0.33, 0.75: 0.58, 0.90: 0.88, 0.95: 1.00}`; previously P50=0.00, P90=0.87) so the flow faithfully mirrors the canonical model as the stand-in for the eventual HA integration. Requires re-import into Node-RED to take effect. CLAUDE.md documents the three-copy sync rule + redeploy requirement.
- **`tools/nodered-flow.json` default output switched P90 → P50** (`msg.payload`, log line, and `needed_kwh`); all four levels still reported in the helper JSON. Reflects P50 as the best-capturing level under the new backtest metric. Deployment confidence remains Open — this is a live test, not a locked policy. Requires re-import into Node-RED.
- **Backtest: consumption-prediction accuracy (drift monitor).** New report section with an inline-SVG chart of each night's residual (actual − predicted, P50 central estimate) plus a 14-night rolling-mean line and zero reference, and mean residual over the last 14/30/90 nights. Surfaces prediction drift over time: below zero ⇒ model over-predicting (conservative); above zero ⇒ under-predicting (retrain or move to P75). No new dependencies (hand-built SVG).
- **Backtest: rolling 12-month window.** `BACKTEST_END` = dataset's last date, `BACKTEST_START` = one year prior (set in `main()`), so the window no longer creeps as data is extracted. Subtitle shows the true 12-month span and how many days were served by the absence prior-year proxy (reaches 0 once the window moves past the absence period).
- **Backtest metric reworked to the SoC-trough method (backtest v3).** Evaluates each export decision against the actual overnight SoC trough (`min_soc_overnight`), reconstructing the no-export baseline by adding back `evening_grid_export_wh` + the full-charge adjustment. "Perfect" drains to a soft floor (hard + 10 pts); shortfall is charged only for the *incremental* breach below the *hard* floor. Battery capacity + floor now read from `config.yaml` (were hardcoded). HTML summary tables sorted by net capture descending. Findings: model is safe-but-conservative; zero floor breaches over the recent 14 days. See the DECISIONS.md "Backtest v3" entry.

- `tools/backtest.py` — reworked from four scenarios to nine. Actual-SoC scenarios dropped (GloBird overnight charging makes full-charge the operating reality). Added naive baselines (3-day rolling average, 7-day rolling average, seasonal fixed dataset medians) to benchmark model value. Added fixed-confidence model variants at P75 and P50. Added seasonal aggressive variant (P95 winter / P75 shoulder / P50 summer).
- `tools/backtest_report.html` / `tools/backtest_report.json` — backtest now outputs both HTML and JSON. Summary table excludes winter (Jun–Aug) as structurally loss-making across all scenarios. "Efficiency" column replaced with **net capture** = `net ÷ perfect_net`, which accounts for the $0.28/kWh buyback vs $0.15/kWh export rate asymmetry. Net capture colour thresholds calibrated to non-winter range: ≥65% green, ≥55% amber, <55% red.
- `DECISIONS.md` — two new entries: (1) baseline comparison findings and net capture metric rationale; (2) deployment confidence level decision: start at P75 in September 2026, evaluate P50 after one full shoulder/summer season. Documents why intermediate confidence levels (P60/P65/P70) are not worth adding until live data resolves the P75 vs P50 question (percentile table entries only at P50/P75/P90/P95; values between them snap to the nearest entry).
- **Live temp-source mismatch documented** (2026-05-31 investigation). New `DECISIONS.md` entry: the backtest is a model-quality benchmark, not a live-performance predictor, because the live flow reads a Truganina **forecast** temp (`sensor.overnight_forecast_temp_mean`) while the dataset/backtest use BOM **actuals** (`bom_temp_mean`) — same 6pm–11am window, different source; bias unmeasurable until post-fix overlap accumulates (Open). `DATASET.md` gains a temperature-source warning; `CLAUDE.md` gains gotcha #6 (the five live-flow input sensors must be in long-term `statistics`; three were silently unrecorded — `recorder:` exclude glob + a missing `state_class` — and were fixed in HA config on 2026-05-31).

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
