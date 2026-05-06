# DATASET.md

## Overview

The dataset is a SQLite database with one row per **morning date** — the date corresponding to the 11am endpoint of an overnight window. Each row aggregates measurements from **6:00pm prior day → 11:00am morning date**, all in Australian local time (Australia/Melbourne).

This 17-hour window covers:
- The peak export period (6–9pm)
- Overnight battery discharge
- Morning solar ramp up to 11am
- It deliberately ends at 11am because that is the start of the GloBird free-power window (once that provider is active), where battery can be charged from the grid free of charge

The dataset records what *actually* happened in each window. The downstream model uses this history to predict how much can be safely exported in future windows.

## Window definition

Australia uses two timezones depending on the date:
- **AEDT** (UTC+11): first Sunday of October → first Sunday of April
- **AEST** (UTC+10): first Sunday of April → first Sunday of October

**Always compute boundaries with `zoneinfo.ZoneInfo("Australia/Melbourne")`, not by hardcoding offsets.**

| Boundary | Local time | Purpose |
|---|---|---|
| Window start | 18:00 prior day | Aggregations begin at 6pm |
| Window end | 11:00 row date | Aggregations end at 11am |
| `max_soc_prev_daylight` start | 06:00 prior day | Daylight peak detection |
| `max_soc_prev_daylight` end | 18:00 prior day | (exclusive) |

Windows that straddle a DST transition will be 16 or 18 hours long instead of 17. This is correct behaviour — the script should tolerate it.

## HA hourly bucket convention

Buckets are labeled by their **start** timestamp. A bucket with `start_ts = 18:00` covers the period `[18:00, 19:00)`. Therefore:

- "Value at 6pm on a chart" ≈ `mean` of the **17:00 bucket** (the value to the left of the 6pm tick on a chart, i.e. the average over the hour leading up to 6pm)
- "Value at 11am on a chart" ≈ `mean` of the **10:00 bucket**
- A window aggregation includes all buckets where `18:00 prior ≤ start_ts ≤ 10:00 row date` (these are the buckets fully contained in `[18:00, 11:00)`)

For cumulative-sum sensors (`sum` column): the value at `start_ts = 18:00` is the cumulative reading immediately at the start of bucket `[18:00, 19:00)`. Therefore window energy is:

```
window_energy = sum_at(start_ts = 11:00 row date) − sum_at(start_ts = 18:00 prior day)
```

## Source sensors

| Purpose | Sensor | Native unit | Method | Available from |
|---|---|---|---|---|
| Battery state of charge | `sensor.byd_battery_box_premium_hv_state_of_charge` | % | `mean` / `min` / `max` per bucket | 2023-11-27 |
| Solar generation | `sensor.solarnet_power_photovoltaics` | W | `SUM(MAX(mean, 0)) × 1h → Wh` | 2023-11-27 |
| Consumption (QA only) | `sensor.solarnet_power_load` | W (negative) | `SUM(ABS(mean)) × 1h → Wh` | 2023-11-27 |
| Grid import (cumulative) | `sensor.smart_meter_63a_1_real_energy_consumed` | Wh | delta of `sum` | 2023-11-27 |
| Grid export (cumulative) | `sensor.smart_meter_63a_1_real_energy_produced` | Wh | delta of `sum` | 2023-11-27 |
| Battery charged (cumulative) | `sensor.battery_energy_charged` | Wh | delta of `sum` | 2023-11-27 |
| Battery discharged (cumulative) | `sensor.battery_energy_discharged` | Wh | delta of `sum` | 2023-11-27 |
| Outdoor temperature | `sensor.netatmo_outdoor_temperature` | °C | `min` / `mean` per bucket | (≥ 2023-11-27) |
| Indoor temperature | `sensor.netatmo_indoor_temperature` | °C | `mean` per bucket | (≥ 2023-11-27) |
| Guests overnight | `sensor.hastguests` | bool-as-num | `MAX(mean) over window > 0.5 → 1` | 2026-03-08 |

For sensors only available from a later date, the corresponding column is `NULL` for earlier rows (do not zero-fill).

## Output schema

The canonical DDL lives in `src/schema.sql`. Summary:

```sql
CREATE TABLE daily_observations (
    date TEXT PRIMARY KEY,                  -- 'YYYY-MM-DD' (the 11am-endpoint date)
    provider TEXT NOT NULL,                 -- 'ea' | 'amber' | 'globird'
    guests INTEGER,                         -- 0/1, NULL if before 2026-03-08
    hospital_period INTEGER NOT NULL,       -- 0/1
    
    soc_at_6pm REAL,                        -- %
    min_soc_overnight REAL,                 -- %
    max_soc_prev_daylight REAL,             -- %
    soc_at_11am REAL,                       -- %
    
    min_outdoor_temp REAL,                  -- °C
    avg_indoor_temp REAL,                   -- °C
    
    solar_wh_before_11am INTEGER,           -- Wh
    consumption_wh INTEGER,                 -- Wh, balance-derived (primary)
    consumption_wh_load INTEGER,            -- Wh, raw integration (QA only)
    grid_import_wh INTEGER,                 -- Wh
    grid_export_wh INTEGER,                 -- Wh
    battery_charged_wh INTEGER,             -- Wh
    battery_discharged_wh INTEGER,          -- Wh
    
    curtailment_likely INTEGER NOT NULL,    -- 0/1
    
    extracted_at TEXT NOT NULL,             -- ISO8601 UTC
    extraction_version TEXT NOT NULL        -- e.g. '1.0.0'
);

CREATE INDEX idx_provider ON daily_observations(provider);
CREATE INDEX idx_hospital ON daily_observations(hospital_period);

CREATE TABLE extraction_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- Stored keys: schema_version, last_full_extraction, source_db_path,
--              globird_start_date (NULL until cutover)
```

## Column computation

| Column | Formula |
|---|---|
| `soc_at_6pm` | `byd_soc.mean` where `start_ts = 17:00 prior day local` |
| `min_soc_overnight` | `MIN(byd_soc.min)` over buckets `18:00 prior ≤ start_ts ≤ 10:00 row date` |
| `max_soc_prev_daylight` | `MAX(byd_soc.max)` over buckets `06:00 ≤ start_ts < 18:00 prior day` |
| `soc_at_11am` | `byd_soc.mean` where `start_ts = 10:00 row date local` |
| `min_outdoor_temp` | `MIN(outdoor.min)` over the window |
| `avg_indoor_temp` | `AVG(indoor.mean)` over the window |
| `solar_wh_before_11am` | `SUM(MAX(pv.mean, 0))` over buckets in window (Wh; mean × 1h) |
| `consumption_wh_load` | `SUM(ABS(load.mean))` over buckets in window (Wh) — QA only |
| `grid_import_wh` | `consumed.sum @ 11:00 − consumed.sum @ 18:00 prior` |
| `grid_export_wh` | `produced.sum @ 11:00 − produced.sum @ 18:00 prior` |
| `battery_charged_wh` | `charged.sum @ 11:00 − charged.sum @ 18:00 prior` |
| `battery_discharged_wh` | `discharged.sum @ 11:00 − discharged.sum @ 18:00 prior` |
| `consumption_wh` | `solar_wh_before_11am + grid_import_wh + battery_discharged_wh − grid_export_wh − battery_charged_wh` |
| `curtailment_likely` | `1 if max_soc_prev_daylight ≥ 99 else 0` |
| `guests` | `1 if MAX(hastguests.mean over window) > 0.5 else 0`. **NULL** if window ends before 2026-03-08. |

All energy values are stored as **integer Wh**. Round to nearest whole Wh.
SoC and temperature values are stored as **REAL** with 1 decimal place of precision.

## Provider period logic

| Provider | Date range (inclusive, by row date) | Notes |
|---|---|---|
| `ea` | start of data → 2025-08-15 | Energy Australia |
| `amber` | 2025-08-16 → globird_start − 1 | Amber Energy (variable wholesale pricing) |
| `globird` | globird_start → present | Free 11am–2pm window (TBD; stored in `extraction_meta`) |

`globird_start_date` is NULL in `extraction_meta` until the user cuts over. Until then, all rows from 2025-08-16 onward are `amber`.

## Special period flags

### Hospital period
Rows where `2025-09-28 ≤ date ≤ 2025-11-03` get `hospital_period = 1`. All other rows: `0`. Consumption during this period is abnormal (occupant absent) and should be excluded from model training, but rows are kept in the dataset for completeness.

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

| Column | Expected |
|---|---|
| `provider` | `amber` |
| `hospital_period` | 0 |
| `guests` | 0 (sensor exists by this date; verify against actual data) |
| `soc_at_6pm` | 100.0 |
| `min_soc_overnight` | 73.9 |
| `max_soc_prev_daylight` | 100.0 |
| `soc_at_11am` | 98.9 |
| `min_outdoor_temp` | 17.6 |
| `avg_indoor_temp` | 21.7 |
| `solar_wh_before_11am` | 9612 |
| `consumption_wh_load` | 4949 |
| `grid_import_wh` | 24 |
| `grid_export_wh` | 4677 |
| `battery_charged_wh` | 3600 |
| `battery_discharged_wh` | 3398 |
| `consumption_wh` (balance) | 4757 |
| `curtailment_likely` | 1 |

### Mar 20, 2026 (AEDT) — cloudy, deep discharge

| Column | Expected |
|---|---|
| `provider` | `amber` |
| `hospital_period` | 0 |
| `guests` | (verify against actual data) |
| `soc_at_6pm` | 63.2 |
| `min_soc_overnight` | 20.0 |
| `max_soc_prev_daylight` | 64.4 |
| `soc_at_11am` | 25.1 |
| `min_outdoor_temp` | 16.8 |
| `avg_indoor_temp` | 23.2 |
| `solar_wh_before_11am` | 2779 |
| `consumption_wh_load` | 6624 |
| `grid_import_wh` | 772 |
| `grid_export_wh` | 6 |
| `battery_charged_wh` | 3304 |
| `battery_discharged_wh` | 4954 |
| `consumption_wh` (balance) | 5195 |
| `curtailment_likely` | 0 |

### Jul 17, 2025 (AEST) — winter, depleted, ea period

| Column | Expected |
|---|---|
| `provider` | `ea` |
| `hospital_period` | 0 |
| `guests` | NULL (sensor doesn't exist yet) |
| `soc_at_6pm` | 58.7 |
| `min_soc_overnight` | 6.5 |
| `max_soc_prev_daylight` | 65.7 |
| `soc_at_11am` | 6.5 |
| `min_outdoor_temp` | 10.0 |
| `avg_indoor_temp` | 19.4 |
| `solar_wh_before_11am` | 1007 |
| `consumption_wh_load` | 13040 |
| `grid_import_wh` | 6969 |
| `grid_export_wh` | 1 |
| `battery_charged_wh` | 327 |
| `battery_discharged_wh` | 5051 |
| `consumption_wh` (balance) | 12699 |
| `curtailment_likely` | 0 |

These three samples cover both DST regimes (AEST and AEDT), both providers active during validation, full and depleted battery states, sunny and cloudy days, and curtailment / no-curtailment.

## Coverage and gaps

| Period | Behaviour |
|---|---|
| Before 2023-11-27 | No data; skip |
| 2023-11-28 → first available date | First complete window |
| Hospital period (2025-09-28 to 2025-11-03) | Flagged but not excluded |
| Before 2026-03-08 | `guests` is NULL |
| Today | Skipped (window incomplete) |

## Energy balance as a QA signal

Each row's energy balance can be sanity-checked. Define:
```
imbalance_wh = consumption_wh_load − consumption_wh
```

This indicates how noisy the integrated power-mean was on a given day. Typical magnitudes: ±500 Wh on quiet days, up to ±2000 Wh on rapidly-changing (cloudy/windy) days. The extraction script logs a warning for any row where `|imbalance_wh| > 3000` so it can be investigated. The warning is non-fatal — the row is still written.
