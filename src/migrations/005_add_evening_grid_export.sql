-- Migration 005: Add evening grid export (schema 1.4.0 → 1.5.0)
-- evening_grid_export_wh is the grid-export `sum` delta over the 6–9pm peak
-- window (18:00–21:00 local prior day), i.e. sum_at(20:00) − sum_at(17:00)
-- following the cum-delta convention (bucket T's sum is the reading at T+1h).
--
-- It is a proxy for deliberate battery-to-grid export during the peak. It is
-- used by the backtest to reconstruct the no-export baseline overnight SoC
-- trough: a night's recorded min_soc_overnight is depressed by any real export,
-- so adding this energy back recovers what the trough would have been with no
-- export, against which the model's recommended export can be simulated.
--
-- Caveat: in high-summer the 6–9pm window can still have some PV generation, so
-- this slightly over-counts (includes solar→grid as well as battery→grid).
-- Outside summer evenings PV is negligible and it is essentially battery export.

ALTER TABLE daily_observations ADD COLUMN evening_grid_export_wh INTEGER;  -- Wh, grid export over 6–9pm peak

UPDATE extraction_meta SET value = '1.5.0', updated_at = datetime('now')
WHERE key = 'schema_version';
