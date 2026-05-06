CREATE TABLE IF NOT EXISTS daily_observations (
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

CREATE INDEX IF NOT EXISTS idx_provider ON daily_observations(provider);
CREATE INDEX IF NOT EXISTS idx_hospital ON daily_observations(hospital_period);

CREATE TABLE IF NOT EXISTS extraction_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
