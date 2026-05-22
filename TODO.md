# TODO — live test the retrained model + follow-ups

## Done (2026-05-22)

- **Cum-delta window boundary bug — FIXED.** The four cumulative-sum energy columns
  were read over `19:00 prior → 12:00 today` instead of `18:00 prior → 11:00 today`
  (HA stores bucket `T`'s `sum` as the reading at `T+1h`; must read one hour earlier).
  Fixed in `src/extract.py` (`ts_17_prior`/`ts_10_today`), `windows.py` (dropped
  `ts_11_today`), `DATASET.md`, `DECISIONS.md` (new "Cumulative-sum boundary" entry),
  `schema_version` corrected to 1.4.0. Dataset rebuilt; all 3 fixtures re-verified;
  `pytest` green.
- **Model retrained — DONE.** `tools/retrain.py` (new; needs the `tools` numpy extra)
  refit all four zones on 858 trainable nights, held-out validation (every 5th night).
  New coefficients/percentiles/buffers in `config.yaml`, `tests/conftest.py`,
  `tools/predictor.html`, `tools/nodered-flow.json`. Heating R² 0.77→0.83, cooling
  0.37→0.52; P95 buffers shrank (heating 3.562→2.649, cooling 3.136→2.431); held-out
  violation 0.8% heating / 0.0% cooling. `confidence_scale` in `model.py` recomputed
  (negligible drift). See CHANGELOG and the DECISIONS.md retrain/band-review entries.
- **Zone bands reviewed and KEPT at 17/19/21.** The 1°C consumption profile confirmed
  the minimum sits in the warm band (17–19°C) and 19–21°C ("mild") is the low-cooling
  shoulder — so mild > warm is correct, not a bug. Misnomer documented in code +
  DECISIONS.md. Bands unchanged.
- **Three-copy sync rule documented** in CLAUDE.md (model.py/config.yaml,
  predictor.html, nodered-flow.json must stay in sync; redeploy required for the
  Node-RED flow and HTML to take effect).
- **P50 divergence fixed.** The Node-RED flow's confidence buffer-scale ladder now
  matches model.py exactly (`{0.50: 0.33, 0.75: 0.58, 0.90: 0.88, 0.95: 1.00}`;
  previously P50=0.00, P90=0.87). The flow is intended to faithfully mirror the
  canonical model as the stand-in for the eventual HA integration — documented in
  CLAUDE.md. **Re-import the flow into Node-RED to pick up the new coefficients + scales.**

- **Backtest re-run + reworked to the SoC-trough metric (v3) — DONE.** Evaluates against
  the actual overnight trough (`min_soc_overnight`), reconstructing the no-export baseline
  via the new `evening_grid_export_wh` column + full-charge adjustment. "Perfect" drains to
  a soft floor (hard + 10 pts); shortfall charged only for the incremental breach below the
  hard floor. Capacity + floor now read from `config.yaml`. Summary tables sorted by net
  capture descending. New `evening_grid_export_wh` column added (schema 1.5.0, migration
  005, `ts_20_prior`); fixtures + DATASET.md updated; `pytest` green. Resolves the old
  design questions (shortfall metric, actual floor, perfect benchmark, sorting).
- **Node-RED default output switched P90 → P50.** **Re-import the flow into Node-RED to
  deploy.** Deployment confidence stays Open (see DECISIONS.md) — P50 is a live test.

## Now: live test via Node-RED + observe

- **Deployed 2026-05-22** — Node-RED flow live with retrained coefficients, aligned
  confidence scales, and P50 default output. Running at P50 through winter (export
  opportunity is minimal in winter anyway), observing behaviour.
- **Deployment confidence is Open.** Backtest validated only a 14-day window so far (model
  exported with zero floor breaches; P50 best on net capture but full-period numbers are
  caveated by the full-charge assumption + in-sample baselines). Accumulate real nights
  before locking P50 vs P75.
- **Observed:** over the 14 nights to 2026-05-21 the model ran ~0.9 kWh/night high on
  consumption (conservative) and never breached the floor even on nights the user exported
  above its P50 recommendation. Confirms the safe-but-conservative read.

## Behaviour shift: AC now automated to save energy (future model consideration)

The model is trained on history where heating ran longer overnight. The user now controls
the AC via HA and has automated it to reduce overnight heating load, so **actual overnight
consumption is structurally drifting below the training data** — the model will increasingly
over-predict (extra conservatism). Not a problem now, but factor it in eventually:

- Watch for a growing systematic over-prediction bias in heating-zone residuals.
- A periodic retrain on recent data will partly absorb it, but a step-change in behaviour
  (rather than gradual) may warrant weighting recent nights or a thermostat/HVAC-setpoint
  feature (see the Sensibo HVAC open decision in DECISIONS.md).

## Add the SoC-minimum sensor to the dataset (future)

The discharge floor is currently the fixed `config.yaml` `reserve_fraction` (0.10) — fine
for now, since we have **no historical record** of what the floor was per night.

- `sensor.byd_battery_box_premium_hv_soc_minimum` exists in HA but is recorded in
  **short-term `states` only — it has NO long-term statistics** (not in `statistics_meta`),
  so it is not yet usable by the extraction (which reads the `statistics` table).
- **Action:** enable long-term statistics for it in HA (needs a `state_class`), then add a
  per-night floor column to the dataset and have the backtest/model use the actual floor
  instead of the fixed 0.10. Only useful going forward (no backfill possible).

## Later (still deferred)

- **Deployment** of safe-export recommendations targeted for **September 2026** (winter
  Jun–Aug is structurally loss-making). Re-confirm with the backtest.
- **Cooling model** still only ~64 nights (one summer); revisit after a second summer.
