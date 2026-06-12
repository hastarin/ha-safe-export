# DATASET.md

## Overview

The dataset is a SQLite database with one row per **morning date** — the date corresponding to the 11am endpoint of an overnight window. Each row aggregates measurements from **6:00pm prior day → 11:00am morning date**, all in Australian local time (Australia/Melbourne).

This 17-hour window covers:

- The peak export period (6–9pm)
- Overnight battery discharge
- Morning solar ramp up to 11am
- It deliberately ends at 11am because that is the start of the GloBird free-power window (once that provider is active), where battery can be charged from the grid free of charge

The dataset records what _actually_ happened in each window. The downstream model uses this history to predict how much can be safely exported in future windows.

## Window definition

Australia uses two timezones depending on the date:

- **AEDT** (UTC+11): first Sunday of October → first Sunday of April
- **AEST** (UTC+10): first Sunday of April → first Sunday of October

**Always compute boundaries with `zoneinfo.ZoneInfo("Australia/Melbourne")`, not by hardcoding offsets.**

| Boundary                      | Local time      | Purpose                   |
| ----------------------------- | --------------- | ------------------------- |
| Window start                  | 18:00 prior day | Aggregations begin at 6pm |
| Window end                    | 11:00 row date  | Aggregations end at 11am  |
| `max_soc_prev_daylight` start | 06:00 prior day | Daylight peak detection   |
| `max_soc_prev_daylight` end   | 18:00 prior day | (exclusive)               |

Windows that straddle a DST transition will be 16 or 18 hours long instead of 17. This is correct behaviour — the script should tolerate it.

## HA hourly bucket convention

Buckets are labeled by their **start** timestamp. A bucket with `start_ts = 18:00` covers the period `[18:00, 19:00)`. Therefore:

- "Value at 6pm on a chart" ≈ `mean` of the **17:00 bucket** (the value to the left of the 6pm tick on a chart, i.e. the average over the hour leading up to 6pm)
- "Value at 11am on a chart" ≈ `mean` of the **10:00 bucket**
- A window aggregation includes all buckets where `18:00 prior ≤ start_ts ≤ 10:00 row date` (these are the buckets fully contained in `[18:00, 11:00)`)

For cumulative-sum sensors (`sum` column): the value stored in bucket `start_ts = T` is the cumulative meter reading at the **end** of that bucket, i.e. at time `T+1h`. To read the cumulative value **at** a boundary hour H, query the bucket labelled `H−1h`. Therefore window energy (18:00 prior → 11:00 row date) is:

```text
window_energy = sum_at(start_ts = 10:00 row date) − sum_at(start_ts = 17:00 prior day)
```

## Source sensors

| Purpose                         | Sensor                                              | Native unit  | Method                            | Available from |
| ------------------------------- | --------------------------------------------------- | ------------ | --------------------------------- | -------------- |
| Battery state of charge         | `sensor.byd_battery_box_premium_hv_state_of_charge` | %            | `mean` / `min` / `max` per bucket | 2023-11-27     |
| Solar generation                | `sensor.solarnet_power_photovoltaics`               | W            | `SUM(MAX(mean, 0)) × 1h → Wh`     | 2023-11-27     |
| Consumption (QA only)           | `sensor.solarnet_power_load`                        | W (negative) | `SUM(ABS(mean)) × 1h → Wh`        | 2023-11-27     |
| Grid import (cumulative)        | `sensor.smart_meter_63a_1_real_energy_consumed`     | Wh           | delta of `sum`                    | 2023-11-27     |
| Grid export (cumulative)        | `sensor.smart_meter_63a_1_real_energy_produced`     | Wh           | delta of `sum`                    | 2023-11-27     |
| Battery charged (cumulative)    | `sensor.battery_energy_charged`                     | Wh           | delta of `sum`                    | 2023-11-27     |
| Battery discharged (cumulative) | `sensor.battery_energy_discharged`                  | Wh           | delta of `sum`                    | 2023-11-27     |
| Outdoor temperature             | `sensor.netatmo_outdoor_temperature`                | °C           | `min` / `mean` per bucket         | (≥ 2023-11-27) |
| Indoor temperature              | `sensor.netatmo_indoor_temperature`                 | °C           | `mean` per bucket                 | (≥ 2023-11-27) |
| Guests overnight                | configured in `config.yaml` (`sensors.guests`)      | number       | `MAX(mean) over window > 0.5 → 1` | varies         |

Note: the guests column is not yet used by the model — it is stored for future use.

### External weather station

Configure your weather station sensors in `config.yaml` under `sensors.weather_*`. Any HA weather integration providing the sensors below works (BOM via Bureau of Meteorology integration, Met.no, etc.).

| Purpose         | Config key                   | Native unit | Method                                            |
| --------------- | ---------------------------- | ----------- | ------------------------------------------------- |
| Temperature     | `sensors.weather_temp`       | °C          | `MIN(min)` / `AVG(mean)` / `MAX(max)` over window |
| Feels-like temp | `sensors.weather_feels_like` | °C          | `MIN(min)` over window                            |
| Rain since 9am  | `sensors.weather_rain`       | mm          | `MAX(CAST(state AS REAL))` over window            |
| Wind speed      | `sensors.weather_wind`       | km/h        | `AVG(mean)` over window                           |
| Gust speed      | `sensors.weather_gust`       | km/h        | `MAX(max)` over window                            |
| Humidity        | `sensors.weather_humidity`   | %           | `AVG(mean)` / `MAX(max)` over window              |

Note: the rain sensor stores values in `state` only (`mean`/`min`/`max` are NULL in HA statistics). Use `MAX(CAST(state AS REAL))` to get the peak rain gauge reading over the window.

### Solcast PV forecast

| Purpose       | Sensor                                         | Native unit | Method                                               | Available from |
| ------------- | ---------------------------------------------- | ----------- | ---------------------------------------------------- | -------------- |
| Tomorrow's PV | `sensor.solcast_pv_forecast_forecast_tomorrow` | kWh         | `state` at 17:00 bucket prior day, \* 1000 to get Wh | 2024-10-17     |

Note: the Solcast sensor stores values in `state` only (`mean` is NULL). Read `state` at `start_ts = ts(prior_day, 17)` to get the forecast value that would be visible at the 6pm decision time. NULL for rows before 2024-10-17.

### Median indoor temperature

| Purpose           | Sensor                      | Native unit | Method                  | Available from |
| ----------------- | --------------------------- | ----------- | ----------------------- | -------------- |
| Multi-room median | `sensor.median_temperature` | °C          | `AVG(mean)` over window | 2024-01-08     |

NULL for rows before 2024-01-08.

### Median indoor humidity

| Purpose           | Sensor                   | Native unit | Method                  | Available from |
| ----------------- | ------------------------ | ----------- | ----------------------- | -------------- |
| Multi-room median | `sensor.median_humidity` | %           | `AVG(mean)` over window | 2024-01-08     |

NULL for rows before 2024-01-08.

### Overnight forecast inputs (live-flow counterparts)

| Purpose                  | Sensor                                  | Native unit | Method                       | Available from |
| ------------------------ | --------------------------------------- | ----------- | ---------------------------- | -------------- |
| Forecast overnight temp  | `sensor.overnight_forecast_temp_mean`   | °C          | point value at 6pm local     | ~2026-06-01    |
| Forecast overnight humid | `sensor.overnight_forecast_humidity_mean` | %         | point value at 6pm local     | ~2026-06-01    |

These are the **forecast** counterparts to `bom_temp_mean` / `bom_humidity_mean` (which are
BOM **actuals** over the 6pm–11am window). They are the actual inputs the live Node-RED flow
reads at 6pm to make the export decision — a Truganina hourly forecast averaged over the same
6pm–11am window by an HA template sensor. **The two sources can differ by several °C and flip
the export decision — never substitute one for the other** (see CLAUDE.md gotcha #6 and
`docs/analysis/LIVE_INTEGRATION.md`).

Read mechanics: the value is taken from the statistics bucket labeled **18:00 local on the
prior day** (the 6pm decision point, matching the `bom_temp_mean` window convention — each row
is keyed by its 11am-endpoint morning date). If that exact bucket is missing, extraction falls
back to the most recent earlier bucket within **3 hours** (no older than 15:00 local); beyond
that the column is `NULL`. NULL for all rows before the `overnight_forecast_*` sensors began
recording to long-term statistics (the recorder/`state_class` fixes landed in the 2026-05-31
audit; the first cleanly-readable prior-evening value is for the morning of 2026-06-01).

Extraction is **forecast-only** — the economic backtest still scores on `bom_temp_mean`. A
forecast-scored backtest scenario is deferred to the next retrain, once more nights accumulate.

For sensors only available from a later date, the corresponding column is `NULL` for earlier rows (do not zero-fill).

## Output schema

The canonical DDL lives in `src/schema.sql`. Summary:

```sql
CREATE TABLE daily_observations (
    date TEXT PRIMARY KEY,                  -- 'YYYY-MM-DD' (the 11am-endpoint date)
    provider TEXT NOT NULL,                 -- 'ea' | 'amber' | 'globird'
    guests INTEGER,                         -- 0/1, NULL if before 2026-03-08
    absence_period INTEGER NOT NULL,        -- 0/1
    data_gap INTEGER NOT NULL DEFAULT 0,   -- 0/1: known sensor outage; energy columns unreliable

    soc_at_6pm REAL,                        -- %
    min_soc_overnight REAL,                 -- %
    max_soc_prev_daylight REAL,             -- %
    soc_at_11am REAL,                       -- %

    min_outdoor_temp REAL,                  -- °C
    avg_indoor_temp REAL,                   -- °C

    bom_temp_min REAL,                      -- °C, MIN(min) over 6pm–11am window
    bom_temp_mean REAL,                     -- °C, AVG(mean) over 6pm–11am window
    bom_feels_like_min REAL,                -- °C, MIN(min) over 6pm–11am window
    bom_rain_max REAL,                      -- mm, MAX(state) over 6pm–11am window
    bom_wind_mean REAL,                     -- km/h, AVG(mean) over 6pm–11am window
    bom_gust_max REAL,                      -- km/h, MAX(max) over 6pm–11am window
    solcast_forecast_tomorrow_wh INTEGER,   -- Wh, state at 17:00 prior day * 1000; NULL before Oct 2024
    median_indoor_temp REAL,                -- °C, AVG(mean) over 6pm–11am window; NULL before Jan 2024
    bom_temp_max REAL,                      -- °C, MAX(max) over 6pm–11am window
    bom_temp_afternoon_max REAL,            -- °C, MAX(max) over 12:00–18:00 prior day
    bom_humidity_mean REAL,                 -- %, AVG(mean) over 6pm–11am window
    bom_humidity_max REAL,                  -- %, MAX(max) over 6pm–11am window
    median_indoor_humidity REAL,            -- %, AVG(mean) over 6pm–11am window; NULL before Jan 2024
    forecast_temp_mean REAL,                -- °C, forecast at 6pm local (prior eve); NULL before ~Jun 2026
    forecast_humidity_mean REAL,            -- %, forecast at 6pm local (prior eve); NULL before ~Jun 2026

    solar_wh_before_11am INTEGER,           -- Wh
    consumption_wh INTEGER,                 -- Wh, balance-derived (primary)
    consumption_wh_load INTEGER,            -- Wh, raw integration (QA only)
    grid_import_wh INTEGER,                 -- Wh
    grid_export_wh INTEGER,                 -- Wh
    battery_charged_wh INTEGER,             -- Wh
    battery_discharged_wh INTEGER,          -- Wh
    evening_grid_export_wh INTEGER,         -- Wh, grid export over 6–9pm peak; proxy for battery-to-grid export

    curtailment_likely INTEGER NOT NULL,    -- 0/1

    extracted_at TEXT NOT NULL,             -- ISO8601 UTC
    extraction_version TEXT NOT NULL        -- e.g. '1.1.0'
);

CREATE INDEX idx_provider ON daily_observations(provider);
CREATE INDEX idx_absence ON daily_observations(absence_period);
CREATE INDEX idx_data_gap ON daily_observations(data_gap);

CREATE TABLE extraction_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- Stored keys: schema_version, last_full_extraction, source_db_path,
--              globird_start_date ('2026-05-05')
```

## Column computation

| Column                         | Formula                                                                                                                                                |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `soc_at_6pm`                   | `byd_soc.mean` where `start_ts = 17:00 prior day local`                                                                                                |
| `min_soc_overnight`            | `MIN(byd_soc.min)` over buckets `18:00 prior ≤ start_ts ≤ 10:00 row date`                                                                              |
| `max_soc_prev_daylight`        | `MAX(byd_soc.max)` over buckets `06:00 ≤ start_ts < 18:00 prior day`                                                                                   |
| `soc_at_11am`                  | `byd_soc.mean` where `start_ts = 10:00 row date local`                                                                                                 |
| `min_outdoor_temp`             | `MIN(outdoor.min)` over the window                                                                                                                     |
| `avg_indoor_temp`              | `AVG(indoor.mean)` over the window                                                                                                                     |
| `bom_temp_min`                 | `MIN(weather_temp.min)` over the window                                                                                                                |
| `bom_temp_mean`                | `AVG(weather_temp.mean)` over the window                                                                                                               |
| `bom_temp_max`                 | `MAX(weather_temp.max)` over the window                                                                                                                |
| `bom_temp_afternoon_max`       | `MAX(weather_temp.max)` over **12:00–18:00 prior day** (afternoon peak before the 6pm decision)                                                        |
| `bom_feels_like_min`           | `MIN(weather_feels_like.min)` over the window                                                                                                          |
| `bom_rain_max`                 | `MAX(CAST(weather_rain.state AS REAL))` over the window                                                                                                |
| `bom_wind_mean`                | `AVG(weather_wind.mean)` over the window                                                                                                               |
| `bom_gust_max`                 | `MAX(weather_gust.max)` over the window                                                                                                                |
| `solcast_forecast_tomorrow_wh` | `int(solcast.state * 1000)` where `start_ts = 17:00 prior day`. **NULL** before 2024-10-17.                                                            |
| `median_indoor_temp`           | `AVG(median_temperature.mean)` over the window. **NULL** before 2024-01-08.                                                                            |
| `bom_humidity_mean`            | `AVG(weather_humidity.mean)` over the window                                                                                                           |
| `bom_humidity_max`             | `MAX(weather_humidity.max)` over the window                                                                                                            |
| `median_indoor_humidity`       | `AVG(median_humidity.mean)` over the window. **NULL** before 2024-01-08.                                                                               |
| `forecast_temp_mean`           | `overnight_forecast_temp_mean.mean` at the 18:00-local prior-day bucket (3h fallback). Forecast, not actuals. **NULL** before ~2026-06-01.             |
| `forecast_humidity_mean`       | `overnight_forecast_humidity_mean.mean` at the 18:00-local prior-day bucket (3h fallback). Forecast, not actuals. **NULL** before ~2026-06-01.         |
| `solar_wh_before_11am`         | `SUM(MAX(pv.mean, 0))` over buckets in window (Wh; mean x 1h)                                                                                          |
| `consumption_wh_load`          | `SUM(ABS(load.mean))` over buckets in window (Wh) — QA only                                                                                            |
| `grid_import_wh`               | `consumed.sum @ 10:00 row date − consumed.sum @ 17:00 prior` (reads cumulative at 11:00 minus 18:00)                                                   |
| `grid_export_wh`               | `produced.sum @ 10:00 row date − produced.sum @ 17:00 prior`                                                                                           |
| `battery_charged_wh`           | `charged.sum @ 10:00 row date − charged.sum @ 17:00 prior`                                                                                             |
| `battery_discharged_wh`        | `discharged.sum @ 10:00 row date − discharged.sum @ 17:00 prior`                                                                                       |
| `evening_grid_export_wh`       | `produced.sum @ 20:00 prior − produced.sum @ 17:00 prior` (export over 6–9pm peak; proxy for battery-to-grid export)                                   |
| `consumption_wh`               | `solar_wh_before_11am + grid_import_wh + battery_discharged_wh − grid_export_wh − battery_charged_wh`                                                  |
| `curtailment_likely`           | `1 if max_soc_prev_daylight ≥ 99 else 0`                                                                                                               |
| `guests`                       | `1 if MAX(guests_sensor.mean over window) > 0.5 else 0`. **NULL** if the guests sensor has no data for the window. Sensor configured in `config.yaml`. |

All energy values are stored as **integer Wh**. Round to nearest whole Wh.
SoC and temperature values are stored as **REAL** with 1 decimal place of precision.

> **Temperature source ≠ the live flow's temperature source.** `bom_temp_mean` (and the other `bom_*` columns) are BOM weather-station **actuals**. The live Node-RED predictor does **not** read these — at inference time it reads `sensor.overnight_forecast_temp_mean`, a template sensor averaging the **Truganina hourly _forecast_** over the same 6pm–11am window. Same window definition, different source (forecast vs actual, different provider). They can differ by several °C, which is enough to change the model's zone and flip the export decision. Do not treat `bom_temp_mean` as a proxy for what the live flow saw. See `docs/DECISIONS.md` and `docs/analysis/LIVE_INTEGRATION.md`.

## Provider period logic

Provider periods are configured in `config.yaml` as an ordered list. Each entry has a `name` and a `start_date`; the provider for any given row date is the last entry whose `start_date` ≤ that date.

```yaml
providers:
  - name: "provider_a"
    start_date: "2023-01-01"
  - name: "provider_b"
    start_date: "2024-06-01"
```

The first provider's `start_date` also defines the earliest date the extraction will process. The `globird_start_date` key in `extraction_meta` is set from the config's `globird` provider entry if present.

## Special period flags

### Absence period

Absence periods are configured in `config.yaml` as a list of date ranges:

```yaml
absence_periods:
  - start: "2024-09-01"
    end: "2024-10-15"
```

Rows whose date falls within any configured absence period get `absence_period = 1`. All other rows: `0`. Consumption during absence periods is abnormal (occupant absent) and should be excluded from model training, but rows are kept in the dataset for completeness.

### Data gap

`data_gap = 1` marks rows where a known sensor outage makes the energy columns unreliable. The rows are kept in the dataset for chronological completeness but must be excluded from model training (`WHERE data_gap = 0`).

Known gap dates are configured in `config.yaml`:

```yaml
data_gap_dates:
  - "2024-03-15"
  - "2024-03-16"
```

New gaps are added to the config and the extraction script run with `--from <date>` to backfill. The extraction script also warns automatically when a large energy imbalance coincides with near-zero battery throughput (despite a significant SOC swing) or zero solar before 11am — both are reliable indicators of missing sensor data rather than normal integration noise.

### Curtailment likely

Set to 1 when the battery hit at least 99% during the prior day's daylight period (06:00–18:00). On these days, solar export to grid was likely throttled by the inverter once the battery filled, so `solar_wh_before_11am` understates true solar potential.

## Incremental extraction

The extraction script is incremental:

1. Open dataset DB (create from `schema.sql` if it doesn't exist)
2. Read `MAX(date) FROM daily_observations`; default to `2023-11-28` if empty
3. For each date from `MAX(date) + 1` through yesterday:
   - Verify the HA DB has data covering the full window for that date
   - Skip the date with a warning if any required sensor is missing data
   - Otherwise compute the row and `INSERT OR REPLACE INTO daily_observations`
4. Update `extraction_meta` with `last_full_extraction = now()` (ISO8601 UTC)

Today's row is always skipped — its window is incomplete until 11am tomorrow.

A `--rebuild` flag drops and re-extracts all rows. Use this when methodology changes.

A `--from YYYY-MM-DD` flag re-extracts from a specific date forward. Use this for partial backfills.

## Validation samples

These three rows are encoded as test fixtures in `tests/fixtures.py`. The extraction script must reproduce them exactly (within the tolerances in CLAUDE.md).

### Feb 7, 2026 (AEDT) — full battery, sunny

| Column                         | Expected                                     |
| ------------------------------ | -------------------------------------------- |
| `provider`                     | `amber`                                      |
| `absence_period`               | 0                                            |
| `guests`                       | NULL (sensor doesn't exist until 2026-03-08) |
| `soc_at_6pm`                   | 100.0                                        |
| `min_soc_overnight`            | 73.9                                         |
| `max_soc_prev_daylight`        | 100.0                                        |
| `soc_at_11am`                  | 98.9                                         |
| `min_outdoor_temp`             | 17.6                                         |
| `avg_indoor_temp`              | 21.7                                         |
| `bom_temp_min`                 | 15.3                                         |
| `bom_temp_mean`                | 17.2                                         |
| `bom_temp_max`                 | 22.1                                         |
| `bom_temp_afternoon_max`       | 23.5                                         |
| `bom_feels_like_min`           | 14.1                                         |
| `bom_rain_max`                 | 0.0                                          |
| `bom_wind_mean`                | 10.3                                         |
| `bom_gust_max`                 | 32.0                                         |
| `solcast_forecast_tomorrow_wh` | 43973                                        |
| `median_indoor_temp`           | 22.7                                         |
| `solar_wh_before_11am`         | 9612                                         |
| `consumption_wh_load`          | 4949                                         |
| `grid_import_wh`               | 24                                           |
| `grid_export_wh`               | 3635                                         |
| `battery_charged_wh`           | 3600                                         |
| `battery_discharged_wh`        | 3399                                         |
| `evening_grid_export_wh`       | 1812                                         |
| `consumption_wh` (balance)     | 5800                                         |
| `curtailment_likely`           | 1                                            |

### Mar 20, 2026 (AEDT) — cloudy, deep discharge

| Column                         | Expected |
| ------------------------------ | -------- |
| `provider`                     | `amber`  |
| `absence_period`               | 0        |
| `guests`                       | 0        |
| `soc_at_6pm`                   | 63.2     |
| `min_soc_overnight`            | 20.0     |
| `max_soc_prev_daylight`        | 64.4     |
| `soc_at_11am`                  | 25.1     |
| `min_outdoor_temp`             | 16.8     |
| `avg_indoor_temp`              | 23.2     |
| `bom_temp_min`                 | 15.8     |
| `bom_temp_mean`                | 17.0     |
| `bom_temp_max`                 | 18.6     |
| `bom_temp_afternoon_max`       | 19.4     |
| `bom_feels_like_min`           | 15.0     |
| `bom_rain_max`                 | 0.4      |
| `bom_wind_mean`                | 8.2      |
| `bom_gust_max`                 | 24.0     |
| `solcast_forecast_tomorrow_wh` | 34888    |
| `median_indoor_temp`           | 22.3     |
| `solar_wh_before_11am`         | 2779     |
| `consumption_wh_load`          | 6624     |
| `grid_import_wh`               | 768      |
| `grid_export_wh`               | 6        |
| `battery_charged_wh`           | 1360     |
| `battery_discharged_wh`        | 5090     |
| `evening_grid_export_wh`       | 0        |
| `consumption_wh` (balance)     | 7271     |
| `curtailment_likely`           | 0        |

### Jul 17, 2025 (AEST) — winter, depleted, ea period

| Column                         | Expected                        |
| ------------------------------ | ------------------------------- |
| `provider`                     | `ea`                            |
| `absence_period`               | 0                               |
| `guests`                       | NULL (sensor doesn't exist yet) |
| `soc_at_6pm`                   | 58.7                            |
| `min_soc_overnight`            | 6.5                             |
| `max_soc_prev_daylight`        | 65.7                            |
| `soc_at_11am`                  | 6.5                             |
| `min_outdoor_temp`             | 10.0                            |
| `avg_indoor_temp`              | 19.4                            |
| `bom_temp_min`                 | 9.2                             |
| `bom_temp_mean`                | 10.7                            |
| `bom_temp_max`                 | 12.8                            |
| `bom_temp_afternoon_max`       | 15.0                            |
| `bom_feels_like_min`           | 5.4                             |
| `bom_rain_max`                 | 0.0                             |
| `bom_wind_mean`                | 17.4                            |
| `bom_gust_max`                 | 41.0                            |
| `solcast_forecast_tomorrow_wh` | 8789                            |
| `median_indoor_temp`           | 19.2                            |
| `solar_wh_before_11am`         | 1007                            |
| `consumption_wh_load`          | 13040                           |
| `grid_import_wh`               | 6731                            |
| `grid_export_wh`               | 1                               |
| `battery_charged_wh`           | 0                               |
| `battery_discharged_wh`        | 5806                            |
| `evening_grid_export_wh`       | 1                               |
| `consumption_wh` (balance)     | 13543                           |
| `curtailment_likely`           | 0                               |

These three samples cover both DST regimes (AEST and AEDT), both providers active during validation, full and depleted battery states, sunny and cloudy days, and curtailment / no-curtailment.

## Coverage and gaps

| Period                            | Behaviour                                                            |
| --------------------------------- | -------------------------------------------------------------------- |
| Before 2023-11-27                 | No data; skip                                                        |
| 2023-11-28 → first available date | First complete window                                                |
| Configured absence periods        | Flagged but not excluded (`absence_period = 1`)                      |
| Configured data gap dates         | Flagged but not excluded (`data_gap = 1`); energy columns unreliable |
| Before guests sensor has data     | `guests` is NULL                                                     |
| Once guests sensor is present     | `guests = 1` when the sensor reads > 0.5 for any hour in the window  |
| Today                             | Skipped (window incomplete)                                          |

## Energy balance as a QA signal

Each row's energy balance can be sanity-checked. Define:

```text
imbalance_wh = consumption_wh_load − consumption_wh
```

This indicates how noisy the integrated power-mean was on a given day. Typical magnitudes: ±500 Wh on quiet days, up to ±2000 Wh on rapidly-changing (cloudy/windy) days. The extraction script logs a warning for any row where `|imbalance_wh| > 3000` so it can be investigated. The warning is non-fatal — the row is still written.
