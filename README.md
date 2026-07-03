# ha-safe-export

[![CI](https://github.com/hastarin/ha-safe-export/actions/workflows/ci.yml/badge.svg)](https://github.com/hastarin/ha-safe-export/actions/workflows/ci.yml)

Predict the maximum amount of energy that can be safely exported from a home battery during the evening peak, without leaving the home short before solar recovers the next morning.

> **Status:** Phase 2 (modelling) — data extraction complete; prediction model built and tested.

---

## What this does

Each evening at 6pm, residential battery owners with solar face a decision:

- **Export aggressively** to capture the day's highest grid-feed-in tariffs, but risk running the battery flat overnight and importing expensive grid power at the worst possible time.
- **Hold back** to guarantee comfort overnight, leaving export revenue on the table.

`ha-safe-export` is a Home Assistant integration (eventually — see [phases](#phases)) that takes the guesswork out of this decision. At 6pm each day it answers a single question:

> _Given the current battery charge, the weather forecast, and what we've learned from past nights, how much can be safely exported between now and 9pm such that the battery still has enough at 11am tomorrow?_

The answer is exposed as an HA sensor that can drive automations or simply inform manual decisions.

## Why this is hard

The naive approach — "export anything above a fixed reserve threshold" — works on average but fails on the days that matter most. A cold cloudy night with high heating load can drain a battery that _seemed_ safe at 6pm. A clear sunny morning following a moderate evening can leave the battery wastefully full. The decision needs to anticipate:

- **Overnight consumption**, which scales with outdoor temperature and household occupancy
- **Morning solar recovery**, which depends on the day-ahead weather forecast
- **Provider context**, which changes the value of every kWh in or out of the battery
- **Curtailment effects**, where a battery already at 100% during the day couldn't absorb all available solar

The model uses ~2.5 years of historical operational data to learn these relationships and produce a calibrated, uncertainty-aware export limit.

## System context

This project is currently scoped to one specific home installation:

| Component                    | Detail                                    |
| ---------------------------- | ----------------------------------------- |
| Battery                      | BYD Battery-Box Premium HV, 13.8 kWh      |
| Solar inverter               | Fronius (via SolarNet integration in HA)  |
| Smart meter                  | Fronius 63A single-phase                  |
| Climate sensors              | Netatmo (indoor + outdoor)                |
| Location                     | Melbourne, Australia                      |
| Energy providers (over time) | Energy Australia → Amber Energy → GloBird |

The data extraction is hardcoded to these specific sensors and providers. Generalising this is a Phase 3 concern; the integration version will discover available sensors at config time.

## How it works (high level)

```text
┌────────────────────┐    ┌──────────────────┐    ┌─────────────────────┐
│ HA recorder DB     │───▶│ extract.py       │───▶│ ha-safe-export.db   │
│ (read-only)        │    │ daily extraction │    │ (one row per night) │
└────────────────────┘    └──────────────────┘    └──────────┬──────────┘
                                                              │
                                                              ▼
                          ┌────────────────────┐    ┌──────────────────┐
                          │ Solcast forecast   │───▶│ model.py         │
                          │ Weather forecast   │    │ predict() at 6pm │
                          │ Live HA state      │    └────────┬─────────┘
                          └────────────────────┘             │
                                                              ▼
                                                   ┌──────────────────┐
                                                   │ HA sensor        │
                                                   │ safe_export_wh   │
                                                   └──────────────────┘
```

Phase 1 builds the extraction half. Phase 2 builds the prediction half. Phase 3 wraps both in an HA integration.

## Project structure

```text
ha-safe-export/
├── CLAUDE.md             ← Standing instructions for AI agents working on the code
├── README.md             ← This file
├── docs/
│   ├── SPEC.md           ← Project specification: prediction objective, success criteria
│   ├── DATASET.md        ← Data contract: schema, sensors, formulas, validation samples
│   ├── DECISIONS.md      ← Rationale log for design choices (read before changing them)
│   └── analysis/         ← Background analysis docs (model selection, schema evolution)
├── src/
│   ├── extract.py        ← Builds and refreshes the dataset (Phase 1)
│   ├── schema.sql        ← Canonical DDL for the dataset DB
│   ├── windows.py        ← Timezone-aware window math
│   └── model.py          ← Three-zone predictor + predict() function (Phase 2)
├── tools/
│   └── predictor.html    ← Interactive browser-based predictor (no Python needed)
├── tests/
│   ├── fixtures.py       ← Known-good values for three validation days
│   ├── test_extract.py   ← Extraction fixture tests
│   └── test_model.py     ← Model unit and regression tests
├── data/                 ← gitignored; holds the dataset DB
└── pyproject.toml
```

## Documentation

| Document                                 | What it covers                                                                           |
| ---------------------------------------- | ---------------------------------------------------------------------------------------- |
| [`docs/SPEC.md`](docs/SPEC.md)           | What the model predicts, success criteria, inference-time inputs, what's out of scope    |
| [`docs/DATASET.md`](docs/DATASET.md)     | The data contract — every column, every sensor, every formula, three validation fixtures |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Why each significant design choice was made; rejected alternatives; evidence             |
| [`CLAUDE.md`](CLAUDE.md)                 | Standing context for AI agents (Claude Code, etc.) — gotchas and conventions             |

The `DECISIONS.md` log is the most important one to consult before changing how anything is computed. Several non-obvious choices (timezone handling, sensor selection, balance-derived consumption) have specific evidence behind them and should not be undone without strong justification.

## Phases

| Phase                  | Deliverable                                                                                                                          | Status        |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | ------------- |
| **1. Data extraction** | `src/extract.py` builds an incrementally-updateable SQLite dataset (v1.3.0, 33 columns); passes three validation fixtures            | **Complete**  |
| **2. Modelling**       | `src/model.py` — three-zone linear consumption model with calibrated P90/P95 uncertainty bounds; `predict()` callable for Phase 3    | **Complete**  |
| **3. HA integration**  | HACS-installable custom component; auto-discovers sensors; exposes `sensor.safe_export_wh`                                           | Not started   |

## Setup

```bash
# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install (no external dependencies beyond tzdata on Windows)
pip install -e .

# First-time extraction (point at your HA recorder DB)
python -m src.extract /path/to/home-assistant_v2.db

# Rebuild from scratch (e.g. after a methodology change)
python -m src.extract /path/to/home-assistant_v2.db --rebuild

# Run tests
python -m pytest
```

## Home Assistant template sensors

The model needs overnight mean temperature and humidity derived from the hourly weather forecast. Add these trigger-based template sensors to your HA `configuration.yaml` (or a package YAML file). They call `weather.get_forecasts` on a schedule and average the 6pm–11am window.

```yaml
template:
  - trigger:
      - trigger: time
        at: "17:59:00"
      - trigger: homeassistant
        event: start
      - trigger: event
        event_type: event_template_reloaded
    action:
      - action: weather.get_forecasts
        data:
          type: hourly
        target:
          entity_id: weather.truganina_hourly  # Replace with your weather entity
        response_variable: hourly
    sensor:
      - name: "Overnight Forecast Temp Mean"
        unique_id: overnight_forecast_temp_mean
        unit_of_measurement: "°C"
        state_class: measurement
        state: >
          {% set forecasts = hourly['weather.truganina_hourly']['forecast'] %}  {# Replace entity name #}
          {% set tomorrow = (now().date() + timedelta(days=1)).strftime('%Y-%m-%d') %}
          {% set tonight = now().date().strftime('%Y-%m-%d') %}
          {% set ns = namespace(total=0, count=0) %}
          {% for f in forecasts %}
            {% set dt = f.datetime %}
            {% set is_tonight = dt >= tonight ~ 'T18:00:00' and dt < tonight ~ 'T24:00:00' %}
            {% set is_tomorrow_morning = dt >= tomorrow ~ 'T00:00:00' and dt <= tomorrow ~ 'T11:00:00' %}
            {% if is_tonight or is_tomorrow_morning %}
              {% set ns.total = ns.total + f.temperature %}
              {% set ns.count = ns.count + 1 %}
            {% endif %}
          {% endfor %}
          {{ (ns.total / ns.count) | round(1) if ns.count > 0 else 'unknown' }}

      - name: "Overnight Forecast Humidity Mean"
        unique_id: overnight_forecast_humidity_mean
        unit_of_measurement: "%"
        state_class: measurement
        state: >
          {% set forecasts = hourly['weather.truganina_hourly']['forecast'] %}  {# Replace entity name #}
          {% set tomorrow = (now().date() + timedelta(days=1)).strftime('%Y-%m-%d') %}
          {% set tonight = now().date().strftime('%Y-%m-%d') %}
          {% set ns = namespace(total=0, count=0) %}
          {% for f in forecasts %}
            {% set dt = f.datetime %}
            {% set is_tonight = dt >= tonight ~ 'T18:00:00' and dt < tonight ~ 'T24:00:00' %}
            {% set is_tomorrow_morning = dt >= tomorrow ~ 'T00:00:00' and dt <= tomorrow ~ 'T11:00:00' %}
            {% if is_tonight or is_tomorrow_morning %}
              {% set ns.total = ns.total + f.humidity %}
              {% set ns.count = ns.count + 1 %}
            {% endif %}
          {% endfor %}
          {{ (ns.total / ns.count) | round(1) if ns.count > 0 else 'unknown' }}
```

The `event_template_reloaded` trigger fires immediately when you reload templates via the UI — no HA restart needed after a config change.

## Interactive predictor

[`tools/predictor.html`](tools/predictor.html) is a standalone HTML file that embeds the model coefficients and lets you explore predictions without running Python. Open it directly in any browser:

```powershell
start tools\predictor.html
```

Sliders for temp, Solcast forecast, humidity, SOC, and confidence level update the result in real time. The humidity input is only active in the cooling zone (>21°C); the Solcast input is only active in the heating zone (<19°C).

## Node-RED automation

[`tools/nodered-flow.json`](tools/nodered-flow.json) is a ready-to-import Node-RED flow that runs the predictor automatically at 6pm each day and writes the results back to Home Assistant helpers.

### How it works

1. Triggers at 6pm (plus a manual trigger button for testing)
2. Reads five HA sensors in sequence: overnight forecast temp, humidity, Solcast tomorrow, battery SOC, and min SOC cutoff
3. Runs the three-zone linear model in a function node (no Python needed — coefficients are embedded as JS constants)
4. Writes results to two HA helpers:
   - `input_number.safe_export_wh` — P90 safe export in **Wh** (integer). Use this directly as a W export limit for a 1-hour window, or divide by 3 to spread over 3 hours.
   - `input_text.safe_export_detail` — compact JSON with all four confidence levels and context. All `p50`/`p75`/`p90`/`p95` values in the JSON are **Wh**; `avail_kwh` is kWh. Internal model fields (`consumption`, `buffer`, `total_needed`, `grid_needed`) are kWh.

### Installation

**1. Create the HA helpers** (Settings → Devices & Services → Helpers):

| Type   | Entity ID            | Min | Max   | Step | Unit           |
| ------ | -------------------- | --- | ----- | ---- | -------------- |
| Number | `safe_export_wh`     | 0   | 13800 | 1    | Wh             |
| Text   | `safe_export_detail` | —   | —     | —    | max length 255 |

**2. Import the flow** in Node-RED: hamburger menu → Import → paste the contents of `tools/nodered-flow.json`.

**3. Set the HA server** on each node (they'll show as unconfigured until you select your Home Assistant connection).

**4. Update the sensor entity IDs.** The flow's state-reader nodes are pre-populated with the author's sensor names. Open each of the five "Get …" nodes and replace the entity ID with your own:

| Node | Entity ID to replace | What it reads |
| ---- | -------------------- | -------------- |
| Get overnight temp | `sensor.overnight_forecast_temp_mean` | Your overnight temp template sensor (see above) |
| Get overnight humidity | `sensor.overnight_forecast_humidity_mean` | Your overnight humidity template sensor (see above) |
| Get Solcast tomorrow | `sensor.solcast_pv_forecast_forecast_tomorrow` | Your Solcast forecast entity |
| Get battery SOC | `sensor.byd_battery_box_premium_hv_state_of_charge` | Your battery's state-of-charge sensor |
| Get min SOC cutoff | `sensor.byd_battery_box_premium_hv_soc_minimum` | Your battery's minimum SOC sensor |

**5. Update the battery capacity** in the "Three-zone linear model" function node. Near the top, change `BATTERY_KWH` to match your battery's usable capacity in kWh:

```js
const BATTERY_KWH = 13.8;  // ← replace with your battery's usable capacity
```

**6. Deploy and test** using the Manual trigger button. Check the debug sidebar for the full result object.

### Updating the model

When you retrain (every few months), only the constants at the top of the "Three-zone linear model" function node need updating — the eight coefficient values (`b0`, `b1`, `b2` for heating and cooling zones, the four `MILD` percentile values, and the two P95 buffer values).

### Exposing the result as an HA sensor

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

## Manual prediction at 6pm

Read the values from HA and call `predict()` from the command line:

```bash
.venv\Scripts\python -c "
from pathlib import Path
from src.config import load_config
from src.model import PredictInputs, predict
cfg = load_config(Path('config/config.yaml'))
result = predict(PredictInputs(
    soc_at_6pm=85.0,                      # live battery SoC at 6pm (%)
    bom_temp_mean=10.5,                   # sensor.overnight_forecast_temp_mean
    bom_humidity_mean=87.0,               # sensor.overnight_forecast_humidity_mean
    solcast_forecast_tomorrow_wh=18000,   # sensor.solcast_pv_forecast_forecast_tomorrow
    min_soc=0.10,                         # battery min SoC setting (0.20 in storm mode)
    confidence=0.90,
), cfg)
print(f'Safe export: {result.safe_export_wh:.0f} Wh  ({result.safe_export_wh/1000:.2f} kWh)')
print(result.reasoning)
"
```

Or from a Python script/REPL:

```python
from pathlib import Path
from src.config import load_config
from src.model import PredictInputs, predict

cfg = load_config(Path("config/config.yaml"))
result = predict(PredictInputs(
    soc_at_6pm=85.0,               # live battery SoC at 6pm (%)
    bom_temp_mean=10.5,
    bom_humidity_mean=87.0,
    solcast_forecast_tomorrow_wh=18000,
    min_soc=0.10,
    confidence=0.90,
), cfg)

print(f"Safe export: {result.safe_export_wh:.0f} Wh")
print(f"Zone: {result.zone}, model: {result.model_variant}")
print(f"Predicted consumption: {result.predicted_consumption_kwh:.1f} kWh "
      f"+ {result.error_buffer_kwh:.1f} kWh buffer")
print(result.reasoning)
```

## Requirements

- Python 3.11+
- A Home Assistant installation with at least 12 months of recorded statistics for the sensors listed in `docs/DATASET.md`
- SQLite (bundled with Python; no separate install needed)
- For Phase 2 onwards: Solcast PV forecasting integration in HA, plus a weather forecast integration

## Contributing

This is a personal infrastructure project built heavily with [Claude AI](https://claude.ai/code). The design docs (`DECISIONS.md`, `DATASET.md`, `SPEC.md`) and the `CLAUDE.md` standing instructions are written so that an AI agent can pick up the codebase cold — if you fork this and want to adapt it to your own hardware, that's the intended path.

Issues are unlikely to get personal attention. If something is broken or unclear, your best bet is to fork, use Claude Code (or similar) to work through the adaptation, and iterate from there. Pull requests that fix bugs or improve the documentation are welcome, but support requests for getting it running on different hardware won't be addressed.

## License

MIT — see [LICENSE](LICENSE).
