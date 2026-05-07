-- Migration 002: Add humidity features (schema 1.1.0 → 1.2.0)
-- Adds 3 new columns: bom_humidity_mean, bom_humidity_max, median_indoor_humidity

-- Intended to run against schema_version = 1.1.0 only.

ALTER TABLE daily_observations ADD COLUMN bom_humidity_mean REAL;   -- %, AVG(mean) over 6pm–11am window
ALTER TABLE daily_observations ADD COLUMN bom_humidity_max REAL;    -- %, MAX(max) over 6pm–11am window
ALTER TABLE daily_observations ADD COLUMN median_indoor_humidity REAL; -- %, AVG(mean) over 6pm–11am window; NULL before Jan 2024

UPDATE extraction_meta SET value = '1.2.0', updated_at = datetime('now')
WHERE key = 'schema_version';
