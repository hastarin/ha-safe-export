# ha-safe-export

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
│   └── DECISIONS.md      ← Rationale log for design choices (read before changing them)
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
          entity_id: weather.truganina_hourly
        response_variable: hourly
    sensor:
      - name: "Overnight Forecast Temp Mean"
        unique_id: overnight_forecast_temp_mean
        unit_of_measurement: "°C"
        state_class: measurement
        state: >
          {% set forecasts = hourly['weather.truganina_hourly']['forecast'] %}
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
          {% set forecasts = hourly['weather.truganina_hourly']['forecast'] %}
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
   - `input_number.safe_export_kwh` — P90 export value, suitable for dashboard tiles and automations
   - `input_text.safe_export_detail` — compact JSON with all four confidence levels (P50/P75/P90/P95), zone, temp, SOC, available kWh, and timestamp

### Installation

**1. Create the HA helpers** (Settings → Devices & Services → Helpers):

| Type   | Entity ID            | Min            | Max | Step |
| ------ | -------------------- | -------------- | --- | ---- |
| Number | `safe_export_kwh`    | 0              | 14  | 0.01 |
| Text   | `safe_export_detail` | max length 255 | —   | —    |

**2. Import the flow** in Node-RED: hamburger menu → Import → paste the contents of `tools/nodered-flow.json`.

**3. Set the HA server** on each node (they'll show as unconfigured until you select your Home Assistant connection).

**4. Deploy and test** using the Manual trigger button. Check the debug sidebar for the full result object.

### Updating the model

When you retrain (every few months), only the constants at the top of the "Three-zone linear model" function node need updating — the eight coefficient values (`b0`, `b1`, `b2` for heating and cooling zones, the four `MILD` percentile values, and the two P95 buffer values).

## Manual prediction at 6pm

Read the values from HA and call `predict()` from the command line:

```bash
.venv\Scripts\python -c "
from src.model import PredictInputs, predict
result = predict(PredictInputs(
    soc_at_6pm=85.0,                      # sensor.byd_battery_box_premium_hv_state_of_charge
    bom_temp_mean=10.5,                   # sensor.overnight_forecast_temp_mean
    bom_humidity_mean=87.0,               # sensor.overnight_forecast_humidity_mean
    solcast_forecast_tomorrow_wh=18000,   # sensor.solcast_pv_forecast_forecast_tomorrow
    provider='amber',                     # current provider: 'ea', 'amber', or 'globird'
    min_soc=0.10,                         # battery min SoC setting (0.20 in storm mode)
    confidence=0.90,
))
print(f'Safe export: {result.safe_export_wh:.0f} Wh  ({result.safe_export_wh/1000:.2f} kWh)')
print(result.reasoning)
"
```

Or from a Python script/REPL:

```python
from src.model import PredictInputs, predict

result = predict(PredictInputs(
    soc_at_6pm=85.0,
    bom_temp_mean=10.5,
    bom_humidity_mean=87.0,
    solcast_forecast_tomorrow_wh=18000,
    provider="amber",
    min_soc=0.10,
    confidence=0.90,
))

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

This is currently a personal infrastructure project. If you've stumbled across it and have a similar setup (BYD + Fronius + Australian residential), feel free to open an issue — generalisation is a Phase 3 concern but the design docs may already be useful to you.

## License

TBD.
