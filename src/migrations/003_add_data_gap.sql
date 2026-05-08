-- Migration 003: Add data_gap flag (schema 1.2.0 → 1.3.0)
-- Marks rows where a known sensor outage makes energy columns unreliable.
-- Known gaps at time of writing:
--   2026-02-22 to 2026-02-24: battery sensor template deleted
--   2026-05-05 to 2026-05-06: battery sensor template deleted

ALTER TABLE daily_observations ADD COLUMN data_gap INTEGER NOT NULL DEFAULT 0;

UPDATE daily_observations
SET data_gap = 1
WHERE date IN ('2026-02-22', '2026-02-23', '2026-02-24', '2026-05-05', '2026-05-06');

CREATE INDEX IF NOT EXISTS idx_data_gap ON daily_observations(data_gap);

UPDATE extraction_meta SET value = '1.3.0', updated_at = datetime('now')
WHERE key = 'schema_version';
