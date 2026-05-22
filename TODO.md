# TODO — backtest re-run on corrected data + retrained model

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

## Now: re-run the backtest on the corrected data + retrained model

All prior backtest numbers (the tables in DECISIONS.md "Backtest results" / "Backtest v2",
and `tools/backtest_report.{html,json}`) were computed on the **buggy data and old
coefficients** — do not trust any of them until the backtest is re-run.

`.venv/Scripts/python -m tools.backtest`

### Backtest work already committed (52a813c)

Committed in `tools/backtest.py` (commit `52a813c`) — these run but their numbers are
based on the old buggy data + old coefficients, so re-run and re-read them:

1. `BACKTEST_END` moved to **2026-05-20**.
2. Six **blended scenarios** I1–I6: consumption = α·model + (1−α)·3-day-rolling-avg,
   α ∈ {0.75, 0.50, 0.25}, buffer-fixed and buffer-scaled variants
   (`run_blended_scenario()` + SCENARIOS entries + main() wiring).
3. **Recent-window sections**: 14-day and 30-day summary tables at the top of the HTML
   (`start` param on all 4 run_* functions, `_recent_summary_rows()`, two new HTML
   sections in `build_html`). **The last-14-days view is the priority lens** for judging
   the retrained model's near-term behaviour.

### Open design questions to resolve (now that the data is correct)

- **Shortfall metric: total consumption vs net battery draw.** Should `actual_wh` be
  total home `consumption_wh`, or **net battery draw** (`battery_discharged_wh −
  battery_charged_wh`)? Stated goal: **the battery alone carries the house to 11am with
  NO grid draw in the window.** The bad data derailed this discussion before — revisit.
- **Use actual `min_soc_overnight`** rather than a hardcoded 10% floor.
- **Reconsider the "perfect" benchmark** — maybe `min_soc + 10%` headroom rather than
  draining to exactly `min_soc`.
- **Sort summary tables by net capture descending.**
- **Deployment confidence level.** The flow now exports P50 (aligned to scale 0.33).
  Use the backtest — especially the last-14-days view — to judge whether P50 is the
  right deployment confidence or whether to lean more conservative (P75). Note: corrected
  consumption is *higher* than before, so the retrained model recommends **less** export
  at every confidence level — the old (buggy) model was over-recommending. Quantify this.

## Later (still deferred)

- **Deployment** of safe-export recommendations targeted for **September 2026** (winter
  Jun–Aug is structurally loss-making). Re-confirm with the re-run backtest.
- **Cooling model** still only ~64 nights (one summer); revisit after a second summer.
