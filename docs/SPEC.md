# SPEC.md

## Project goal

Build a reliable, data-driven model that predicts how much energy can be safely exported from a home battery during the evening peak period (6–9pm) while ensuring sufficient charge remains to carry the home through to 11am the following day without significant grid import.

The model becomes the brain of a Home Assistant automation that, at 6pm each day, recommends a "safe export limit" and either drives a battery export controller automatically or surfaces the value as a sensor for the user.

## System context

| Component       | Detail                                                                          |
| --------------- | ------------------------------------------------------------------------------- |
| Battery         | Configured in `config.yaml` (`battery.capacity_wh`, `battery.reserve_fraction`) |
| Reserve         | Configurable floor (default 10%)                                                |
| Solar           | Any inverter with HA statistics integration                                     |
| Location        | Configured timezone in `config.yaml`                                            |
| Heating/cooling | Indoor climate strongly affects overnight load                                  |
| Energy provider | Time-varying; periods configured in `config.yaml`; see DATASET.md               |

The provider is recorded in the dataset because tariff structure influences consumption behaviour — free overnight charging windows, wholesale price exposure, and flat-rate plans all change how the home is operated, which shifts the underlying load profile. Provider is not currently used in the `predict()` function; the user selects an appropriate confidence level (P50–P95) for their own risk tolerance. Provider is retained as a stratification variable so per-provider model performance can be evaluated as more data accumulates under each tariff.

## Prediction objective

At **6:00pm on day N**, given inputs available at that moment, predict the maximum energy `E_export` (in Wh) that can be exported from the battery between 6pm and 9pm on day N such that:

> **The battery State of Charge at 11am on day N+1 remains above the configured discharge floor (`battery.reserve_fraction`, default 10%) with high confidence (default ≥90%).**

The 11am endpoint is chosen deliberately: it is the start of the GloBird free-power window when active, and the natural recovery point of the morning solar ramp regardless of provider. After 11am, the system has clear paths to recharge.

### Conceptual decomposition

The prediction can be decomposed (we are not committing to this structure for Phase 2 — it's just the cleanest mental model):

```text
SoC_at_11am_tomorrow ≈ SoC_now
                     + (predicted_solar_in_window / battery_capacity)
                     − (predicted_consumption_in_window / battery_capacity)
                     − (E_export / battery_capacity)
                     + (any planned grid import / battery_capacity)
```

Solving for the maximum `E_export` that satisfies the safety constraint:

```text
E_export_max ≈ (SoC_now − SoC_safety_threshold) × battery_capacity
              + predicted_solar
              − predicted_consumption
              + planned_grid_import
```

The model's primary job is to estimate `predicted_solar` and `predicted_consumption` for the 6pm-to-11am window — both with usable uncertainty bounds, since the safety constraint is probabilistic.

## Decision logic at 6pm

The recommended export limit is derived from the prediction:

1. Compute `E_export_max` from the model with the chosen confidence level
2. Cap by physical constraints (max battery discharge rate × 3 hours, inverter export limit)
3. Cap by economic constraints (provider-specific, e.g. don't bother exporting under flat-rate periods)
4. Clamp to non-negative

The output is exposed in HA as one or more sensors:

- `sensor.battery_safe_export_wh` — the recommended cap
- `sensor.battery_export_confidence` — model's reported confidence
- `sensor.battery_export_reasoning` — short text explanation

## Inputs available at inference (6pm on day N)

The training dataset uses _actuals_ for these. At inference time, the system needs _forecasts_. The interface should accept both transparently.

| Input                                 | Training source                                      | Inference source                                                  |
| ------------------------------------- | ---------------------------------------------------- | ----------------------------------------------------------------- |
| Current SoC                           | `sensor.byd_battery_box_premium_hv_state_of_charge`  | live HA state                                                     |
| Predicted solar 6pm–11am              | computed from `solarnet_power_photovoltaics` history | `sensor.solcast_pv_forecast_forecast_today` + `forecast_tomorrow` |
| Predicted outdoor temperature profile | `sensor.netatmo_outdoor_temperature` history         | weather forecast integration (e.g. Met.no, BOM)                   |
| Indoor climate state                  | `sensor.netatmo_indoor_temperature`                  | live HA state                                                     |
| Guests overnight flag                 | configured in `config.yaml` (`sensors.guests`)       | live HA sensor (configured in `config.yaml`)                      |
| Provider                              | derived from row date via `config.yaml`              | derived from current date / `config.yaml`                         |
| Calendar features                     | row date                                             | inference time                                                    |

The Solcast and weather forecasts feed both the model and (for evaluation) the comparison between predicted-with-forecast and predicted-with-actual.

## Success criteria

The model is judged on three metrics, computed against held-out historical data and against ongoing live operation:

| Metric                                                                                                                           | Target                                |
| -------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| **Safety violation rate** — fraction of days where SoC at 11am dropped below the configured threshold despite the recommendation | ≤ 5% (configurable; safety > revenue) |
| **Export utilisation** — average ratio of actual safe export taken to theoretical maximum (with hindsight)                       | ≥ 70%                                 |
| **Calibration** — predicted confidence intervals contain the actual outcome at the stated rate                                   | within ±5pp of stated confidence      |

A model that exports nothing has a 0% safety violation rate but is useless. A model that exports the full battery every night maximises short-term revenue but blows past the safety threshold often. The metrics are designed to balance these.

Configured absence periods are excluded from training and from these metric calculations.

## Phase plan

| Phase                                           | Deliverable                                                                                                                                                                                                              | Status                                                                                                       |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| **Phase 1** — Standalone Python data extraction | `src/extract.py` builds the dataset SQLite DB; tests reproduce three known-good fixtures exactly                                                                                                                         | **Complete**                                                                                                 |
| **Phase 2** — Modelling                         | Train a model (or stack of models) that produces the prediction objective above. Evaluate against success criteria on a held-out time slice. Output: a trained model file + a `predict()` function                       | **Complete**                                                                                                 |
| **Phase 3** — Home Assistant integration        | Package as a HACS-compatible custom integration. Auto-discovers required sensors (or asks during config). Provides the export-limit sensor and a service to query predictions. Re-extracts daily, retrains periodically. | **Current phase** — Node-RED stand-in live at P50 since 2026-05-22; HACS integration targeted September 2026 |

Each phase produces artifacts the next phase consumes — the dataset is the contract between Phase 1 and Phase 2; the trained model + `predict()` is the contract between Phase 2 and Phase 3.

## Out of scope

To keep the project tractable, these are explicitly not addressed:

- **Battery degradation modelling** — the system optimises for export today, not battery lifespan. A naive heavy-cycling strategy may shorten battery life; that's a separate trade-off.
- **Whole-home load shifting** — we don't tell the user when to run the dishwasher or charge the EV. We assume their consumption pattern is what it is.
- **Tariff arbitrage on import** — we recommend export limits, not import scheduling. (GloBird's free window is implicit context; we don't try to schedule imports during it.)
- **Multi-day optimisation** — the decision at 6pm uses tomorrow morning as its horizon. No attempt to optimise across, say, a forecast cloudy week.
- **Inverter command/control** — Phase 3 _exposes_ a recommended limit. Translating that to actual inverter behaviour (Amber API, Fronius local API, etc.) is left to user-built automations until proven necessary.
- **Faulty-data handling beyond logging** — the extraction script logs warnings on energy-balance imbalances but does not auto-correct or impute.

## Open questions for Phase 2 (status)

These were deferred until we had the dataset in hand.
Most have since been resolved; the rest remain open and are tracked in `docs/DECISIONS.md`'s "Open / deferred decisions".

- **Single model or decomposed?** Resolved: decomposed into four temperature-keyed zones (heating, warm-boundary, mild, cooling), not a single model or a separate solar/consumption split. See "Four-zone model for overnight consumption" in `docs/DECISIONS.md`.
- **Classical or temporal?** Resolved: classical, not sequence-based — OLS regression for the heating and cooling zones, empirical percentile tables for the warm-boundary and mild zones. See the same "Four-zone model for overnight consumption" entry.
- **How should provider transitions be handled?** Still open. Provider is recorded in the dataset but not passed to `predict()`; deferred until enough data exists under each tariff to evaluate separate-model vs feature vs fine-tune approaches. See "Provider handling" under "Open / deferred decisions" in `docs/DECISIONS.md`.
- **How much history is enough? Does the EA period help or hurt vs Amber-period data?** Resolved in practice, not by separate study: the 2026-05-22 retrain used all 858 available trainable nights (`absence_period=0 AND data_gap=0`) without provider-period stratification. See "Four-zone model for overnight consumption" for the retrain details; provider-specific performance remains future work (see "Provider handling" above).
- **How to incorporate the guests flag with so few positive examples?** Still open/deferred. The column is populated (`NULL` before the sensor existed, per "Guests column is NULL before 2026-03-08" in `docs/DECISIONS.md`) but not yet used by the model — too few positive examples remain to evaluate it.
