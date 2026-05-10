-- Migration 004: Add afternoon temperature peak (schema 1.3.0 → 1.4.0)
-- bom_temp_afternoon_max is the MAX(max) of the BOM temperature sensor over
-- 12:00–18:00 local time on the prior day — the afternoon peak before the 6pm
-- export decision. Used to compute the day's temperature swing (afternoon peak
-- minus overnight mean), which helps identify summer nights where early-evening
-- AC load inflates consumption above what overnight temperature alone suggests.

ALTER TABLE daily_observations ADD COLUMN bom_temp_afternoon_max REAL;  -- °C, MAX(max) over 12:00–18:00 prior day

UPDATE extraction_meta SET value = '1.4.0', updated_at = datetime('now')
WHERE key = 'schema_version';
