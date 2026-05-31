# Live Integration (Node-RED + Home Assistant)

How the Phase 2 model is wired into a running battery-export deployment. This is the
**Phase 3 surface** — the chain between `predict()` (via `tools/nodered-flow.json`) and the
battery actually exporting to grid. Documented 2026-05-31 from a live HA-database audit;
the automation/script bodies live in the user's HA config (not in this repo).

> The Node-RED flow is the low-cost stand-in for the eventual native HA integration. It is
> meant to mirror `src/model.py` exactly (see CLAUDE.md "Model coefficients are duplicated
> in three places"). This doc covers the **plumbing around** the model, not the model itself.

## The five model inputs (Node-RED reads these at ~18:00)

The "Four-zone model" function node in `tools/nodered-flow.json` reads exactly five HA sensors:

| `msg.*`       | Entity                                              | Meaning                              | Source                        |
| ------------- | --------------------------------------------------- | ------------------------------------ | ----------------------------- |
| `temp`        | `sensor.overnight_forecast_temp_mean`               | Mean overnight temp, 6pm–11am window | Truganina hourly **forecast** |
| `humidity`    | `sensor.overnight_forecast_humidity_mean`           | Mean overnight humidity, same window | Truganina hourly **forecast** |
| `solcast_kwh` | `sensor.solcast_pv_forecast_forecast_tomorrow`      | Tomorrow's PV forecast (cloud proxy) | Solcast                       |
| `soc`         | `sensor.byd_battery_box_premium_hv_state_of_charge` | Live battery SoC %                   | Fronius/BYD                   |
| `minSoc`      | `sensor.byd_battery_box_premium_hv_soc_minimum`     | Battery min-SoC cutoff %             | Fronius/BYD                   |

`temp`/`humidity` are **trigger-based template sensors** evaluated at 08:59 / 11:59 / 17:59
local (plus HA start / template reload). At 17:59 each averages the hourly forecast over
**tonight 18:00–24:00 + tomorrow 00:00–11:00** — i.e. the dataset's 6pm–11am window, but from
the Truganina forecast, **not** BOM. See the temperature-source warning in `DATASET.md` and the
"model-quality benchmark, not a live-performance predictor" decision in `DECISIONS.md`.

The flow computes `available = (soc − minSoc)/100 × BATTERY_KWH`, subtracts the model's
`consumption + buffer`, and emits the **P50** safe-export figure (Wh) as `msg.payload`.

## The export execution chain (HA side)

The flow does **not** drive the battery directly. It writes a result; a chain of HA helpers,
automations, and scripts decides whether and how to export.

1. **Node-RED** writes a JSON payload to `input_text.safe_export_detail` (~18:00). A
   trigger-based template sensor (in the user's `forecasts.yaml`) parses it and populates
   `input_number.safe_export_wh`.
2. **`automation.grid_export_copy_forecast_to_target`** watches `input_number.safe_export_wh`,
   but **only acts in the 17:55–18:05 window**. It copies the value (clamped to 0–8000 Wh)
   into `input_number.grid_export_target_wh`.
3. **`automation.grid_export_start_at_scheduled_time`** fires at the time stored in
   `input_datetime.grid_export_start_time` (currently 18:01) and calls `script.grid_export_start`
   **only if** target > 0, SoC is above the min, and the end time is still in the future.
4. **`script.grid_export_start`** snapshots the meter reading, sets the battery to
   "Discharge to Grid", and turns **`input_boolean.grid_export_active`** on.
5. While active: **`automation.grid_export_recompute_every_minute`** (gated on `active`)
   updates the target; **`automation.grid_export_stop_early_when_target_hit`** watches for
   delivered Wh ≥ target; **`automation.grid_export_end_at_scheduled_time`** calls
   `script.grid_export_stop` at the end time.
6. **`script.grid_export_stop`** zeroes grid-discharge power, returns the battery to "Auto",
   and turns `input_boolean.grid_export_active` off.

### Gotchas in the chain

- **`input_boolean.grid_export_active` is a session-state flag, NOT an enable switch.** It is
  on only _while a session is running_. Seeing it "off" in the database says nothing about
  whether sessions ran — only that none was active at that sample.
- **Start/end times (`input_datetime.grid_export_*`) are set manually.** Nothing in the config
  populates them; the scheduled-start automation just fires at whatever they currently hold.
- **There are multiple, independent SoC floors — they do not agree.** Keep them distinct:
  - `src/model.py`: a fixed 10% reserve baked into the model.
  - `nodered-flow.json`: the BYD `minSoc` sensor (10% normally; 20% only while
    `input_boolean.storm_mode` on, via `automation.storm_mode_adjust_battery_min_soc`).
  - the export subsystem's own `input_number.grid_export_min_soc` (50%) — the floor the
    discharge scripts stop at.
- **Why winter export is ~0 and that is correct:** on cold evenings the heating-zone
  consumption forecast exceeds `available`, so the flow legitimately outputs 0; the
  `target > 0` guard then prevents a session. Verified end-to-end for 2026-05-30
  (forecast temp 9.3 °C, SoC 96.2%, minSoc 10% → need ~14.3 kWh > 11.9 kWh available → 0).

## Recording requirement (auditability)

To reconstruct or backtest what the live system decided on a past night, **all five input
sensors above must be in long-term `statistics`** — the `states` table retains only ~8 days.

An audit on **2026-05-31** found three were silently not recorded; all three were fixed in HA
config that day:

| Sensor                                    | Cause                                                   | Fix                                                 |
| ----------------------------------------- | ------------------------------------------------------- | --------------------------------------------------- |
| `sensor.overnight_forecast_temp_mean`     | `recorder:` `exclude: entity_globs` `sensor.overnight*` | removed the glob (configuration.yaml)               |
| `sensor.overnight_forecast_humidity_mean` | same exclude glob                                       | same                                                |
| `sensor.byd…soc_minimum`                  | Fronius provided it with **no `state_class`**           | added `state_class: measurement` via customize.yaml |

`state_of_charge` and `solcast…forecast_tomorrow` were always recorded (full history).

**Consequence of the gap:** live export decisions before ~2026-05-31 cannot be reconstructed —
the actual model inputs were not logged. New data accumulates from the fix forward. There is
still **no overlapping history** between the live forecast temp sensor and the dataset's BOM
`bom_temp_mean`, so the forecast-vs-BOM bias remains unmeasurable until enough post-fix nights
accumulate (see `DECISIONS.md`).

**Before trusting any live-vs-backtest comparison:** confirm each of the five sensors actually
has _recent_ rows in `statistics` (don't assume — a sensor that exists in `states`, or that
goes `unknown`/`unavailable` at the top of the hour, can still be missing from `statistics`).
