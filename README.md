# ha-safe-export

Predict the maximum amount of energy that can be safely exported from a home battery during the evening peak, without leaving the home short before solar recovers the next morning.

> **Status:** Phase 1 (data extraction). Pre-implementation — specifications and design docs are complete; code in progress.

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

```
┌────────────────────┐    ┌──────────────────┐    ┌─────────────────────┐
│ HA recorder DB     │───▶│ extract.py       │───▶│ ha-safe-export.db   │
│ (read-only)        │    │ daily extraction │    │ (one row per night) │
└────────────────────┘    └──────────────────┘    └──────────┬──────────┘
                                                              │
                                                              ▼
                          ┌────────────────────┐    ┌──────────────────┐
                          │ Solcast forecast   │───▶│ predict.py       │
                          │ Weather forecast   │    │ at 6pm daily     │
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

```
ha-safe-export/
├── CLAUDE.md          ← Standing instructions for AI agents working on the code
├── README.md          ← This file
├── docs/
│   ├── SPEC.md        ← Project specification: prediction objective, success criteria
│   ├── DATASET.md     ← Data contract: schema, sensors, formulas, validation samples
│   └── DECISIONS.md   ← Rationale log for design choices (read before changing them)
├── src/
│   ├── extract.py     ← Builds and refreshes the dataset (Phase 1)
│   ├── schema.sql     ← Canonical DDL for the dataset DB
│   ├── windows.py     ← Timezone-aware window math
│   ├── model.py       ← Trains the predictor (Phase 2)
│   └── predict.py     ← Inference (Phase 2 / 3)
├── tests/
│   ├── fixtures.py    ← Known-good values for three validation days
│   └── test_extract.py
├── data/              ← gitignored; holds the dataset DB
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

| Phase                  | Deliverable                                                                                                            | Status      |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------- | ----------- |
| **1. Data extraction** | `src/extract.py` builds an incrementally-updateable SQLite dataset; passes three validation fixtures                   | In progress |
| **2. Modelling**       | A trained predictor with calibrated uncertainty estimates; meets safety / utilisation / calibration targets in SPEC.md | Not started |
| **3. HA integration**  | HACS-installable custom component; auto-discovers sensors; exposes `sensor.safe_export_wh`                             | Not started |

## Setup

> Phase 1 implementation in progress. Setup instructions will be added once `src/extract.py` exists.

When ready, the workflow will be:

```bash
# Install
pip install -e .

# First-time extraction (point at HA recorder DB)
python -m ha_safe_export.extract \
  --source /path/to/home-assistant_v2.db \
  --target data/ha-safe-export.db

# Daily incremental extraction (e.g. via cron at 11:30am local)
python -m ha_safe_export.extract \
  --source /path/to/home-assistant_v2.db \
  --target data/ha-safe-export.db
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
