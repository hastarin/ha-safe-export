-- Schema version: 1.5.0
CREATE TABLE IF NOT EXISTS daily_observations (
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
    solcast_forecast_tomorrow_wh INTEGER,   -- Wh, state at 17:00 prior day × 1000; NULL before Oct 2024
    median_indoor_temp REAL,                -- °C, AVG(mean) over 6pm–11am window; NULL before Jan 2024
    bom_temp_max REAL,                      -- °C, MAX(max) over 6pm–11am window
    bom_temp_afternoon_max REAL,            -- °C, MAX(max) over 12:00–18:00 prior day
    bom_humidity_mean REAL,                 -- %, AVG(mean) over 6pm–11am window
    bom_humidity_max REAL,                  -- %, MAX(max) over 6pm–11am window
    median_indoor_humidity REAL,            -- %, AVG(mean) over 6pm–11am window; NULL before Jan 2024

    solar_wh_before_11am INTEGER,           -- Wh
    consumption_wh INTEGER,                 -- Wh, balance-derived (primary)
    consumption_wh_load INTEGER,            -- Wh, raw integration (QA only)
    grid_import_wh INTEGER,                 -- Wh
    grid_export_wh INTEGER,                 -- Wh
    battery_charged_wh INTEGER,             -- Wh
    battery_discharged_wh INTEGER,          -- Wh
    evening_grid_export_wh INTEGER,         -- Wh, grid export over 6–9pm peak; proxy for deliberate battery-to-grid export

    curtailment_likely INTEGER NOT NULL,    -- 0/1

    extracted_at TEXT NOT NULL,             -- ISO8601 UTC
    extraction_version TEXT NOT NULL        -- e.g. '1.1.0'
);

CREATE INDEX IF NOT EXISTS idx_provider ON daily_observations(provider);
CREATE INDEX IF NOT EXISTS idx_absence ON daily_observations(absence_period);
CREATE INDEX IF NOT EXISTS idx_data_gap ON daily_observations(data_gap);

CREATE TABLE IF NOT EXISTS extraction_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
