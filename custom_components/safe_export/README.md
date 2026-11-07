# Safe Export integration

A minimal Home Assistant custom integration that runs the four-zone model (`src/model.py`) inside Home Assistant.
It is the planned replacement for the Node-RED flow in `tools/nodered-flow.json` (issue #53).
It only publishes a recommendation: the existing automations and scripts still control the inverter.

It is built for one household.
If you fork it, retrain on your own history and replace `coefficients.json` (see [Forking and retraining](#forking-and-retraining)).

## What it does

- Runs the model at 18:00 in Home Assistant's time zone, on the **Run now** button, or on the `safe_export.run` action.
- Reads five input sensors chosen in the config flow (see [Inputs](#inputs)).
- Publishes the P50 safe export, the full prediction detail, and a timestamp that changes on every run.
- Fails closed: a missing required input, or any error, publishes 0 Wh with zone `error` and a `reason`.
  It never leaves the previous night's value in place.
- Falls back to the temperature-only model variants when Solcast (heating zone) or humidity (cooling zone) is unavailable, exactly as `src/model.py` does.
- After a Home Assistant restart the sensors are `unknown` until the next run, so nothing stale is restored.

## Entities

| Entity                        | State                                                          | Notes                                                                                                                                     |
| ----------------------------- | -------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `sensor.safe_export_p50`      | P50 safe export, Wh (integer)                                  | `state_class: measurement`, so it reaches long-term statistics. Same units and rounding as `input_number.safe_export_wh`.                 |
| `sensor.safe_export_detail`   | Zone: `heating`, `warm_boundary`, `mild`, `cooling` or `error` | Attributes: every input, `model_variant`, `available_kwh`, `reasoning`, `reason` (set only on failure), `p50`–`p95` blocks, `updated_at`. |
| `sensor.safe_export_last_run` | Timestamp of the last run                                      | Changes on every run, even when the result does not. Use it as the trigger at cutover.                                                    |
| `button.safe_export_run_now`  | —                                                              | Runs a prediction immediately.                                                                                                            |

Each `p50`–`p95` attribute holds `safe_export` (Wh), `consumption`, `buffer`, `total_needed` and `grid_needed` (kWh), the same shape as the Node-RED flow's `msg.result`.

The names deliberately avoid the Node-RED helpers (`input_number.safe_export_wh`, `input_text.safe_export_detail`), so both can run side by side during the shadow stage.

## Inputs

| Config flow field                   | Live sensor today                              | Required at run time                     |
| ----------------------------------- | ---------------------------------------------- | ---------------------------------------- |
| Overnight forecast temperature mean | `sensor.overnight_forecast_temp_mean`          | Yes                                      |
| Overnight forecast humidity mean    | `sensor.overnight_forecast_humidity_mean`      | No: cooling zone falls back to temp-only |
| Solcast forecast for tomorrow (kWh) | `sensor.solcast_pv_forecast_forecast_tomorrow` | No: heating zone falls back to temp-only |
| Battery state of charge (%)         | the unsuffixed `byd…state_of_charge` sensor    | Yes                                      |
| Battery minimum state of charge (%) | `byd…soc_minimum`                              | Yes                                      |
| Battery capacity (kWh)              | —                                              | Typed in, not a sensor                   |

These are the same five sensors the Node-RED flow reads (gotcha #6 in `CLAUDE.md`).
Solcast may be reported in kWh, Wh or MWh, and a missing unit is taken as kWh, as the flow does.
The sensors and capacity can be changed later under the integration's **Configure** option.

## Install (shadow stage)

Installing this does not touch the existing export chain: nothing reads the new entities until cutover (issue #55).

1. In Home Assistant, open **Settings → Marketplace** (HACS before Home Assistant 2026.11), then the three-dot menu, then **Custom repositories**.
2. Add `https://github.com/hastarin/ha-safe-export` (or your fork) with type **Integration**.
3. Find **Safe Export** in the Marketplace, install it, and restart Home Assistant.
   Every update to the integration also needs a full restart.
4. Go to **Settings → Devices & services → Add integration → Safe Export**, then pick the five sensors and enter the battery capacity.
5. Press **Run now** once and check `sensor.safe_export_detail` against `input_text.safe_export_detail`.

## Shadow comparison

Both systems run at 18:00 against the same sensors.
Each night, compare:

- `sensor.safe_export_p50` with `input_number.safe_export_wh`.
- The `zone` and `p50`–`p95` `safe_export` values on `sensor.safe_export_detail` with the JSON in `input_text.safe_export_detail`.

The tests check the integration against the flow's own JavaScript on more than 50,000 input combinations, matching exactly at every confidence level.
A live difference would therefore most likely come from the two reading a sensor at slightly different instants (for example SoC ticking over at 18:00), so record the inputs as well as the outputs on any mismatch.
The `states` table keeps about ten days, which covers a week of shadowing; `sensor.safe_export_p50` is also in long-term statistics.

## Forking and retraining

All model coefficients live in [`coefficients.json`](coefficients.json).
After retraining with `tools/retrain.py`, replace the values in that one file.
In this repo the same numbers are also kept in `config/config.yaml`, `tests/conftest.py` and `tools/nodered-flow.json` until Node-RED is removed (issue #56); `tests/test_sync.py` fails if they drift.

`model.py` here is a byte-for-byte copy of `src/model.py`, because Home Assistant loads this folder without the rest of the repo.
After changing `src/model.py`, copy it over this file; `tests/test_sync.py` fails until you do.

## Tests

The integration tests in `tests_ha/` use `pytest-homeassistant-custom-component`, which pins a Home Assistant release.
That release needs Python 3.14 and does not run on Windows, so use Linux or WSL and a separate venv:

```bash
uv venv ~/.venvs/ha-safe-export --python 3.14
VIRTUAL_ENV=~/.venvs/ha-safe-export uv pip install -e ".[ha]"
~/.venvs/ha-safe-export/bin/python -m pytest tests_ha
```

The Node-RED parity tests need `node` on `PATH` and skip without it.
In the main project venv `pytest tests_ha` skips every module.
CI runs these tests in their own job.
