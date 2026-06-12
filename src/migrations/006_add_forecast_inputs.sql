-- Migration 006: Add live-flow forecast inputs (schema 1.5.0 → 1.6.0)
-- forecast_temp_mean / forecast_humidity_mean are the overnight_forecast_* template
-- sensors read at the 6pm decision point (bucket labeled 18:00 local on the prior day,
-- matching the 6pm-prior→11am window convention of bom_temp_mean). They are the
-- *forecast* counterparts to bom_temp_mean / bom_humidity_mean (which are BOM actuals
-- over the same window). The two sources can differ by several °C — never substitute one
-- for the other (see CLAUDE.md gotcha #6 and docs/analysis/LIVE_INTEGRATION.md).
--
-- Read fallback: if the exact 18:00-local bucket is missing, extraction uses the most
-- recent earlier bucket within 3 hours (no older than 15:00 local); otherwise NULL.
--
-- These columns are NULL for nights before the overnight_forecast_* sensors began
-- recording to long-term statistics (the recorder/state_class fixes landed in the
-- 2026-05-31 audit; the first cleanly-readable prior-evening value is for the morning of
-- 2026-06-01). Extraction is forecast-only — the backtest still scores on bom_temp_mean.
-- A forecast-scored backtest scenario is deferred to the next retrain.
--
-- NOTE: migrations/*.sql are a historical record. The live extract path drops and
-- recreates from schema.sql, so this file is not executed by --rebuild.

ALTER TABLE daily_observations ADD COLUMN forecast_temp_mean REAL;      -- °C, overnight_forecast_temp_mean at 6pm local; NULL before ~Jun 2026
ALTER TABLE daily_observations ADD COLUMN forecast_humidity_mean REAL;  -- %, overnight_forecast_humidity_mean at 6pm local; NULL before ~Jun 2026

UPDATE extraction_meta SET value = '1.6.0', updated_at = datetime('now')
WHERE key = 'schema_version';
