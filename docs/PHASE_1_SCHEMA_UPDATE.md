# Phase 1 Schema Update: Add Weather & Forecast Features (v1.1.0)

## Context

The dataset currently has 882 rows extracted with schema v1.0.0. We're adding 9 new columns to capture additional predictive features:

- BOM weather station data (temp, wind, rain)
- Solcast PV forecast (what the model would have seen at 6pm decision time)
- Multi-room median temperature

All new sensors have been verified to exist in the HA database with sufficient history (see coverage table below).

## Sensor availability & coverage

| Sensor                                         | Available from | Unit | Coverage vs dataset start (2023-11-28) |
| ---------------------------------------------- | -------------- | ---- | -------------------------------------- |
| `sensor.laverton_temp`                         | 2023-04-12     | °C   | Full ✅                                |
| `sensor.laverton_temp_feels_like`              | 2023-04-12     | °C   | Full ✅                                |
| `sensor.laverton_rain_since_9am`               | 2023-04-12     | mm   | Full ✅                                |
| `sensor.laverton_wind_speed_kilometre`         | 2023-04-12     | km/h | Full ✅                                |
| `sensor.laverton_gust_speed_kilometre`         | 2023-04-12     | km/h | Full ✅                                |
| `sensor.solcast_pv_forecast_forecast_tomorrow` | 2024-10-17     | kWh  | Partial (NULL before Oct 2024)         |
| `sensor.median_temperature`                    | 2024-01-08     | °C   | Partial (NULL before Jan 2024)         |

## New columns to add

Add these 9 columns to `daily_observations` table (insert after `avg_indoor_temp`, before `solar_wh_before_11am`):

| Column                         | Type    | Method                                            | Notes                                                                     |
| ------------------------------ | ------- | ------------------------------------------------- | ------------------------------------------------------------------------- |
| `bom_temp_min`                 | REAL    | `MIN(min)` over window                            | Coldest actual temp during 6pm–11am                                       |
| `bom_temp_mean`                | REAL    | `AVG(mean)` over window                           | Average actual temp during 6pm–11am                                       |
| `bom_feels_like_min`           | REAL    | `MIN(min)` over window                            | Coldest apparent temp during 6pm–11am                                     |
| `bom_rain_max`                 | REAL    | `MAX(max)` over window                            | Peak rain gauge reading during 6pm–11am                                   |
| `bom_wind_mean`                | REAL    | `AVG(mean)` over window                           | Average wind speed during 6pm–11am                                        |
| `bom_gust_max`                 | REAL    | `MAX(max)` over window                            | Peak gust speed during 6pm–11am                                           |
| `solcast_forecast_tomorrow_wh` | INTEGER | Value at 6pm prior day (17:00 bucket mean × 1000) | Solcast forecast in Wh; NULL before Oct 2024                              |
| `median_indoor_temp`           | REAL    | `AVG(mean)` over window                           | Multi-room median temp; NULL before Jan 2024                              |
| `bom_temp_max`                 | REAL    | `MAX(max)` over window                            | Hottest actual temp during 6pm–11am (useful for cooling load correlation) |

**Total column count after update:** 29 columns (was 20).

## Tasks

### 1. Create migration script

**File:** `src/migrations/001_add_weather_forecast.sql`

This script must:

1. Check current schema version in `extraction_meta` (must be `1.0.0`)
2. Create a new table `daily_observations_new` with the v1.1.0 schema (original 20 columns + 9 new ones)
3. Copy all existing data: `INSERT INTO daily_observations_new SELECT *, NULL, NULL, ..., NULL FROM daily_observations`
4. Drop old table: `DROP TABLE daily_observations`
5. Rename: `ALTER TABLE daily_observations_new RENAME TO daily_observations`
6. Recreate indexes (`idx_provider`, `idx_hospital`)
7. Update `extraction_meta` SET `value = '1.1.0'` WHERE `key = 'schema_version'`

### 2. Update `src/schema.sql`

Add the 9 new columns in the correct position (after `avg_indoor_temp`, before `solar_wh_before_11am`).

Update the schema version comment at the top to `-- Schema version: 1.1.0`.

### 3. Update `src/extract.py`

Add queries for the new sensors following the same pattern as existing temperature/SoC queries:

**BOM weather (same window as outdoor/indoor temps):**

```python
# Inside the per-row extraction logic
bom_temp_min = query_stat(
    'sensor.laverton_temp',
    'MIN(min)',
    window_start_utc,
    window_end_utc
)
bom_temp_mean = query_stat(
    'sensor.laverton_temp',
    'AVG(mean)',
    window_start_utc,
    window_end_utc
)
# ... similar for feels_like, rain, wind, gust
```

**Solcast forecast (point read at 6pm, like soc_at_6pm):**

```python
# Read the 17:00 bucket (5-6pm hour) on the prior day
solcast_raw = query_stat(
    'sensor.solcast_pv_forecast_forecast_tomorrow',
    'mean',
    five_pm_utc,  # The 17:00 bucket start timestamp
    five_pm_utc   # Point read, not a range
)
# Convert kWh → Wh
solcast_forecast_tomorrow_wh = int(solcast_raw * 1000) if solcast_raw else None
```

**Median indoor temp (same pattern as avg_indoor_temp):**

```python
median_indoor_temp = query_stat(
    'sensor.median_temperature',
    'AVG(mean)',
    window_start_utc,
    window_end_utc
)
```

**Update the INSERT statement** to include all 9 new columns in the correct order.

**Bump `extraction_version`** to `"1.1.0"` in `src/__init__.py`.

### 4. Update `tests/fixtures.py`

Add the expected values for the 9 new columns to all three validation fixtures.

**You'll need to query the HA database to get the actual values.** Here's example SQL for Feb 7 2026 (adapt for Mar 20 and Jul 17):

```sql
-- BOM temp min during window (6pm Feb 6 AEDT → 11am Feb 7 AEDT)
-- Window in UTC: 2026-02-06 07:00:00 → 2026-02-07 00:00:00
SELECT MIN(s.min)
FROM statistics s
JOIN statistics_meta sm ON s.metadata_id = sm.id
WHERE sm.statistic_id = 'sensor.laverton_temp'
  AND s.start_ts >= strftime('%s','2026-02-06 07:00:00')
  AND s.start_ts <= strftime('%s','2026-02-06 23:00:00');

-- Solcast forecast at 6pm Feb 6 (17:00 bucket)
SELECT s.mean
FROM statistics s
JOIN statistics_meta sm ON s.metadata_id = sm.id
WHERE sm.statistic_id = 'sensor.solcast_pv_forecast_forecast_tomorrow'
  AND s.start_ts = strftime('%s','2026-02-06 06:00:00');  -- 17:00 AEDT = 06:00 UTC
```

Run similar queries for all 9 columns × 3 fixtures = 27 values. Add them to `tests/fixtures.py`.

**Note:** Solcast will be NULL for Jul 17 2025 (pre-Oct 2024). Median temp will be NULL for Jul 17 2025 (pre-Jan 2024).

### 5. Run migration and re-extract

```bash
# Apply migration
sqlite3 data/dataset.db < src/migrations/001_add_weather_forecast.sql

# Verify schema version updated
sqlite3 data/dataset.db "SELECT value FROM extraction_meta WHERE key='schema_version'"
# Should output: 1.1.0

# Re-run extraction with --rebuild to populate new columns
python -m ha_safe_export.extract \
  --source data/home-assistant_v2.db \
  --target data/dataset.db \
  --rebuild

# Run tests
pytest tests/test_extract.py -v
```

All three fixtures should pass with the new columns populated.

### 6. Update documentation

**File:** `docs/DATASET.md`

Add a new subsection under "Source sensors" for the BOM and Solcast sensors.

Add the 9 new columns to the "Output schema" table and the "Column computation" table.

Update the three validation fixture tables to include expected values for the new columns.

**File:** `docs/DECISIONS.md`

Add a new entry under "Data architecture":

```markdown
### Add BOM weather and Solcast forecast features (v1.1.0)

**Decision:** Extend the dataset with 9 additional columns: BOM weather station data (temp, feels-like, rain, wind, gust), Solcast PV forecast, median indoor temperature, and max temperature.
**Status:** Locked.
**Date:** 2026-05-07

**Rationale:** The original dataset (v1.0.0) captured overnight consumption and solar generation but lacked direct weather features beyond outdoor/indoor temperature. Adding:

- **BOM station data** provides more granular weather context (wind affects heat loss, rain affects morning solar)
- **Solcast forecast** is what the model will consume at inference time — including it in training data allows calibration of forecast vs actual
- **Median indoor temp** is more representative of whole-home climate than a single bedroom sensor
- **Max temperature** captures peak cooling load potential

**Coverage:** BOM sensors available from April 2023 (full dataset coverage). Solcast from Oct 2024 (NULL before). Median temp from Jan 2024 (NULL before). The model can handle partial coverage via NULL-aware training.

**Evidence:** All sensors verified present in HA statistics table with sufficient history. Query returned 26k+ rows for BOM sensors, 13k+ for Solcast.
```

## Testing checklist

Before considering this update complete:

- [ ] Migration script runs without errors
- [ ] Schema version in `extraction_meta` is `1.1.0`
- [ ] `daily_observations` table has 29 columns (was 20)
- [ ] Re-extraction with `--rebuild` completes successfully
- [ ] All 882 rows have non-NULL values for BOM columns
- [ ] Rows before Oct 2024 have NULL `solcast_forecast_tomorrow_wh`
- [ ] Rows before Jan 2024 have NULL `median_indoor_temp`
- [ ] All three validation fixtures pass exactly (tolerances: ±0.1 for temps, ±1 for Wh/mm/km/h)
- [ ] `pytest` shows 3/3 passing
- [ ] DATASET.md and DECISIONS.md updated

## Expected outcome

After this update, the dataset will have:

- **882 rows** (unchanged)
- **29 columns** (was 20)
- **Schema version 1.1.0**
- **9 new weather/forecast features** ready for Phase 2 modelling

The BOM weather features will have full coverage across all rows. Solcast and median_temp will have NULLs for older rows, which is expected and the model can handle.
