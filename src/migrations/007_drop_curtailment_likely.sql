-- Migration 007: Drop curtailment_likely (schema 1.6.0 → 1.7.0)
-- The flag was `max_soc_prev_daylight >= 99`, which measured "battery filled during
-- daylight", not curtailment. On this installation PV was only curtailed on Amber when the
-- feed-in price went negative (battery full AND zero daylight export); under EA and GloBird
-- it was almost entirely false positives (100% of GloBird nights flagged). Nothing consumed
-- it. See docs/DECISIONS.md "`curtailment_likely` dropped (schema 1.7.0)".
-- max_soc_prev_daylight is retained (used by the backtest's full-charge SoC adjustment).
--
-- Requires SQLite >= 3.35 for DROP COLUMN.
--
-- NOTE: migrations/*.sql are a historical record. The live extract path drops and
-- recreates from schema.sql, so this file is not executed by --rebuild.

ALTER TABLE daily_observations DROP COLUMN curtailment_likely;

UPDATE extraction_meta SET value = '1.7.0', updated_at = datetime('now')
WHERE key = 'schema_version';
