# Node-RED automation

> **Being retired.**
> The `safe_export` custom integration in [`custom_components/safe_export/`](../custom_components/safe_export/README.md) replaces this flow (issues #53–#56).
> This page is kept for the shadow and cutover stages and will be deleted along with the flow.

The overnight temperature and humidity sensors the flow reads are described in [Home Assistant template sensors](TEMPLATE_SENSORS.md).

[`tools/nodered-flow.json`](../tools/nodered-flow.json) is a ready-to-import Node-RED flow that runs the predictor automatically at 6pm each day and writes the results back to Home Assistant helpers.

## How it works

1. Triggers at 6pm (plus a manual trigger button for testing)
2. Reads five HA sensors in sequence: overnight forecast temp, humidity, Solcast tomorrow, battery SOC, and min SOC cutoff
3. Runs the four-zone linear model in a function node (no Python needed — coefficients are embedded as JS constants)
4. Writes results to two HA helpers:
   - `input_number.safe_export_wh` — P50 safe export in **Wh** (integer) — the flow's current default output, found to be the viable operating level (see [`TODO.md`](../TODO.md)). Use this directly as a W export limit for a 1-hour window, or divide by 3 to spread over 3 hours. All four confidence levels are available in the detail JSON below if you want a more conservative figure.
   - `input_text.safe_export_detail` — compact JSON with all four confidence levels and context. All `p50`/`p75`/`p90`/`p95` values in the JSON are **Wh**; `avail_kwh` is kWh. Internal model fields (`consumption`, `buffer`, `total_needed`, `grid_needed`) are kWh.

## Installation

**1. Create the HA helpers** (Settings → Devices & Services → Helpers):

| Type   | Entity ID            | Min | Max   | Step | Unit           |
| ------ | -------------------- | --- | ----- | ---- | -------------- |
| Number | `safe_export_wh`     | 0   | 13800 | 1    | Wh             |
| Text   | `safe_export_detail` | —   | —     | —    | max length 255 |

**2. Import the flow** in Node-RED: hamburger menu → Import → paste the contents of `tools/nodered-flow.json`.

**3. Set the HA server** on each node (they'll show as unconfigured until you select your Home Assistant connection).

**4. Update the sensor entity IDs.** The flow's state-reader nodes are pre-populated with the author's sensor names. Open each of the five "Get …" nodes and replace the entity ID with your own:

| Node                   | Entity ID to replace                                | What it reads                           |
| ---------------------- | --------------------------------------------------- | --------------------------------------- |
| Get overnight temp     | `sensor.overnight_forecast_temp_mean`               | Your overnight temp template sensor     |
| Get overnight humidity | `sensor.overnight_forecast_humidity_mean`           | Your overnight humidity template sensor |
| Get Solcast tomorrow   | `sensor.solcast_pv_forecast_forecast_tomorrow`      | Your Solcast forecast entity            |
| Get battery SOC        | `sensor.byd_battery_box_premium_hv_state_of_charge` | Your battery's state-of-charge sensor   |
| Get min SOC cutoff     | `sensor.byd_battery_box_premium_hv_soc_minimum`     | Your battery's minimum SOC sensor       |

The flow fails closed (writes `safe_export = 0`, `zone: "error"`) both when a required sensor's state is `unknown`/`unavailable` and when a "Get …" node's entity ID doesn't exist at all (mistyped, renamed, or deleted). The latter case is handled by the "Catch sensor read errors" node, scoped to the five "Get …" nodes plus the model function node — any thrown error is caught and routed through the same fail-closed writer path, so the HA helpers always get a fresh `zone: "error"` result instead of holding a stale value from the previous run. Still double-check the five entity IDs above against Developer Tools → States after import.

**5. Update the battery capacity** in the "Four-zone model" function node. Near the top, change `BATTERY_KWH` to match your battery's usable capacity in kWh:

```js
const BATTERY_KWH = 13.8;  // ← replace with your battery's usable capacity
```

**6. Deploy and test** using the Manual trigger button. Check the debug sidebar for the full result object.

## Updating the model

When you retrain (every few months), only the constants at the top of the "Four-zone model" function node need updating.
This is more than a handful of values now: the heating and cooling zone coefficients (`b0`/`b1`/`b2` each, plus their temp-only fallback `b0`/`b1` pairs), the two P95 buffer values, the `WARM` and `MILD` empirical percentile tables (four values each), and the four-value confidence buffer-scale ladder (`CONF`).
`tests/test_sync.py` enforces that these stay identical to `src/model.py`, `config/config.yaml`, and `tests/conftest.py` — run `pytest` after editing either side to confirm they still agree.

## Exposing the result as an HA sensor

The Node-RED flow writes the result to `input_text.safe_export_detail`. You can expose this as a proper HA sensor with full attribute support using a template sensor in your `configuration.yaml`:

```yaml
template:
  - trigger:
      - trigger: state
        entity_id: input_text.safe_export_detail
    sensor:
      - name: "Overnight Forecast Safe Power Export"
        unique_id: overnight_forecast_safe_power_export
        unit_of_measurement: "Wh"
        device_class: energy
        state_class: measurement

        variables:
          j: >
            {% set raw = states('input_text.safe_export_detail') %}
            {% set parsed = raw | from_json(default=None) %}
            {{ parsed }}

        state: "{{ j.p75 if j else 'unknown' }}"

        attributes:
          zone: "{{ j.zone if j else 'unknown' }}"
          temp: "{{ j.temp if j else 'unknown' }}"
          soc: "{{ j.soc if j else 'unknown' }}"
          avail_kwh: "{{ j.avail_kwh if j else 'unknown' }}"
          p50: "{{ j.p50 if j else 'unknown' }}"
          p75: "{{ j.p75 if j else 'unknown' }}"
          p90: "{{ j.p90 if j else 'unknown' }}"
          p95: "{{ j.p95 if j else 'unknown' }}"
          at: "{{ j.at if j else 'unknown' }}"
```

The sensor's state is P75 (a reasonable default for most nights). All four confidence levels and the full prediction context are available as attributes. Change `j.p75` in the `state:` line to `j.p90` if you prefer a more conservative default.

A tile card that surfaces all four confidence levels at once:

```yaml
type: tile
grid_options:
  columns: full
entity: sensor.overnight_forecast_safe_power_export
name: Safe Export Wh
icon: mdi:chart-bell-curve
show_entity_picture: false
hide_state: false
state_content:
  - p50
  - p75
  - p90
  - p95
vertical: false
features_position: bottom
```
