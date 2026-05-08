-- Migration 001: Add weather and forecast features (schema 1.0.0 → 1.1.0)
-- Adds 9 new columns: BOM weather, Solcast forecast, median indoor temp

-- Intended to run against schema_version = 1.0.0 only.

-- Create replacement table with v1.1.0 schema (original 20 cols + 9 new)
CREATE TABLE daily_observations_new (
    date TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    guests INTEGER,
    absence_period INTEGER NOT NULL,

    soc_at_6pm REAL,
    min_soc_overnight REAL,
    max_soc_prev_daylight REAL,
    soc_at_11am REAL,

    min_outdoor_temp REAL,
    avg_indoor_temp REAL,

    -- New columns (inserted here per spec)
    bom_temp_min REAL,
    bom_temp_mean REAL,
    bom_feels_like_min REAL,
    bom_rain_max REAL,
    bom_wind_mean REAL,
    bom_gust_max REAL,
    solcast_forecast_tomorrow_wh INTEGER,
    median_indoor_temp REAL,
    bom_temp_max REAL,

    solar_wh_before_11am INTEGER,
    consumption_wh INTEGER,
    consumption_wh_load INTEGER,
    grid_import_wh INTEGER,
    grid_export_wh INTEGER,
    battery_charged_wh INTEGER,
    battery_discharged_wh INTEGER,

    curtailment_likely INTEGER NOT NULL,

    extracted_at TEXT NOT NULL,
    extraction_version TEXT NOT NULL
);

-- Copy existing data with NULLs for new columns
INSERT INTO daily_observations_new SELECT
    date, provider, guests, absence_period,
    soc_at_6pm, min_soc_overnight, max_soc_prev_daylight, soc_at_11am,
    min_outdoor_temp, avg_indoor_temp,
    NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
    solar_wh_before_11am, consumption_wh, consumption_wh_load,
    grid_import_wh, grid_export_wh, battery_charged_wh, battery_discharged_wh,
    curtailment_likely, extracted_at, extraction_version
FROM daily_observations;

DROP TABLE daily_observations;
ALTER TABLE daily_observations_new RENAME TO daily_observations;

CREATE INDEX IF NOT EXISTS idx_provider ON daily_observations(provider);
CREATE INDEX IF NOT EXISTS idx_absence ON daily_observations(absence_period);

UPDATE extraction_meta SET value = '1.1.0', updated_at = datetime('now')
WHERE key = 'schema_version';
