# ha-safe-export

[![CI](https://github.com/hastarin/ha-safe-export/actions/workflows/ci.yml/badge.svg)](https://github.com/hastarin/ha-safe-export/actions/workflows/ci.yml)

> [!WARNING]
> **Built for one household.**
> The model's coefficients were fitted to a single home's battery, solar, climate and consumption history, so its export numbers are wrong for any other home.
> To use this yourself, fork the repo, extract your own history, retrain with `tools/retrain.py`, and replace `custom_components/safe_export/coefficients.json` before installing your fork.
> Installing this repository unchanged gives you someone else's model.
> See [Forking and retraining](custom_components/safe_export/README.md#forking-and-retraining).

Predict the maximum amount of energy that can be safely exported from a home battery during the evening peak, without leaving the home short before solar recovers the next morning.

> **Status:** Phase 3 (Home Assistant integration) underway.
> Phases 1 (data extraction) and 2 (modelling) are complete.
> A Node-RED flow has run the model live at P50 confidence since 2026-05-22.
> The custom integration in `custom_components/safe_export/` is now shadowing it before cutover.

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
├── CHANGELOG.md          ← Version history (Keep a Changelog format)
├── TODO.md               ← Working notes and live-testing follow-ups
├── hacs.json             ← Marketplace (formerly HACS) custom-repository metadata for the integration
├── custom_components/
│   └── safe_export/      ← Home Assistant integration: runs predict() at 6pm (replacing Node-RED, issue #53)
├── config/
│   ├── config.example.yaml ← Template config
│   └── config.yaml         ← gitignored; your sensor names, coefficients, and history
├── docs/
│   ├── SPEC.md           ← Project specification: prediction objective, success criteria
│   ├── DATASET.md        ← Data contract: schema, sensors, formulas, validation samples
│   ├── DECISIONS.md      ← Rationale log for design choices (read before changing them)
│   ├── TEMPLATE_SENSORS.md ← Overnight forecast template sensors the model reads
│   ├── NODERED.md        ← Setting up the Node-RED flow (being retired)
│   ├── MANUAL_PREDICTION.md ← Calling predict() by hand
│   └── analysis/         ← Background analysis docs (model selection, schema evolution)
├── src/
│   ├── config.py         ← Config dataclass + load_config()
│   ├── extract.py        ← Builds and refreshes the dataset (Phase 1)
│   ├── schema.sql        ← Canonical DDL for the dataset DB
│   ├── windows.py        ← Timezone-aware window math
│   ├── model.py          ← Four-zone predictor + predict() function (Phase 2)
│   └── migrations/       ← Historical schema migrations (not auto-applied; schema.sql is canonical)
├── tests/
│   ├── fixtures.py       ← Known-good values for three validation days
│   ├── test_extract.py   ← Extraction fixture tests
│   ├── test_model.py     ← Model unit and regression tests
│   └── test_sync.py      ← Enforces coefficient parity across config.yaml/conftest.py/nodered-flow.json/model.py/coefficients.json
├── tests_ha/             ← Integration tests under Home Assistant's test harness (separate Python 3.14 venv)
├── tools/
│   ├── backtest.py       ← Economic backtest; outputs backtest_report.html/.json
│   ├── retrain.py        ← Refits the four-zone model from the dataset
│   ├── midday_usage.py   ← Seasonal household usage over the 11:00–14:00 free-power window
│   └── nodered-flow.json ← Live Node-RED flow: runs predict() at 6pm, writes to HA helpers
├── data/                 ← gitignored; holds the dataset DB
└── pyproject.toml
```

## Documentation

| Document                                                 | What it covers                                                                           |
| -------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| [`docs/SPEC.md`](docs/SPEC.md)                           | What the model predicts, success criteria, inference-time inputs, what's out of scope    |
| [`docs/DATASET.md`](docs/DATASET.md)                     | The data contract — every column, every sensor, every formula, three validation fixtures |
| [`docs/DECISIONS.md`](docs/DECISIONS.md)                 | Why each significant design choice was made; rejected alternatives; evidence             |
| [`docs/TEMPLATE_SENSORS.md`](docs/TEMPLATE_SENSORS.md)   | The overnight forecast temperature and humidity template sensors                         |
| [`docs/NODERED.md`](docs/NODERED.md)                     | Installing and updating the Node-RED flow (being retired)                                |
| [`docs/MANUAL_PREDICTION.md`](docs/MANUAL_PREDICTION.md) | Calling `predict()` by hand from the command line or Python                              |
| [`CLAUDE.md`](CLAUDE.md)                                 | Standing context for AI agents (Claude Code, etc.) — gotchas and conventions             |

The `DECISIONS.md` log is the most important one to consult before changing how anything is computed. Several non-obvious choices (timezone handling, sensor selection, balance-derived consumption) have specific evidence behind them and should not be undone without strong justification.

## Phases

| Phase                  | Deliverable                                                                                                                                                                                         | Status       |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------ |
| **1. Data extraction** | `src/extract.py` builds an incrementally-updateable SQLite dataset (see `src/schema.sql` for the current version); passes three validation fixtures                                                 | **Complete** |
| **2. Modelling**       | `src/model.py` — four-zone linear consumption model with calibrated P90/P95 uncertainty bounds; `predict()` callable for Phase 3                                                                    | **Complete** |
| **3. HA integration**  | Node-RED flow live at P50 confidence since 2026-05-22. The `safe_export` custom integration is installed from the Home Assistant Marketplace and shadowing the flow before cutover (issues #53–#56) | **Underway** |

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

### AI agent tooling (optional)

The `work-issue` skill (and CLAUDE.md's GitHub conventions generally) work with plain `gh`, but are smoother with the [`gh-axi`](https://github.com/kunchenguid/gh-axi) skill installed, which wraps `gh` for issues/PRs/CI/releases. It requires `gh` itself to be installed and authenticated (`gh auth status`) first:

```bash
npx skills add kunchenguid/gh-axi --skill gh-axi -g
```

Not required — agents fall back to plain `gh` (or native GitHub MCP tools, in sessions that expose them) if `gh-axi` isn't present.

## Running it in Home Assistant

The [`safe_export` integration](custom_components/safe_export/README.md) runs the model at 6pm and publishes the result as sensors.
It needs two overnight forecast template sensors: see [Home Assistant template sensors](docs/TEMPLATE_SENSORS.md).
The Node-RED flow it replaces is documented in [Node-RED automation](docs/NODERED.md), and [Manual prediction](docs/MANUAL_PREDICTION.md) shows how to call `predict()` by hand.

## Requirements

- Python 3.13+
- A Home Assistant installation with at least 12 months of recorded statistics for the sensors listed in `docs/DATASET.md`
- SQLite (bundled with Python; no separate install needed)
- For Phase 2 onwards: Solcast PV forecasting integration in HA, plus a weather forecast integration

## Contributing

This is a personal infrastructure project built heavily with [Claude AI](https://claude.ai/code). The design docs (`DECISIONS.md`, `DATASET.md`, `SPEC.md`) and the `CLAUDE.md` standing instructions are written so that an AI agent can pick up the codebase cold — if you fork this and want to adapt it to your own hardware, that's the intended path.

Issues are unlikely to get personal attention. If something is broken or unclear, your best bet is to fork, use Claude Code (or similar) to work through the adaptation, and iterate from there. Pull requests that fix bugs or improve the documentation are welcome, but support requests for getting it running on different hardware won't be addressed.

## License

MIT — see [LICENSE](LICENSE).

## Command cheat sheet

Kept last so `cat README.md` leaves it on screen.

```bash
# Incremental extraction — the normal path after refreshing the HA DB
.venv/Scripts/python -m src.extract data/home-assistant_v2.db

# Full rebuild — only when extraction methodology changed
.venv/Scripts/python -m src.extract data/home-assistant_v2.db --rebuild

# Economic backtest (writes tools/backtest_report.{html,json})
.venv/Scripts/python -m tools.backtest

# Seasonal household usage over the 11:00–14:00 free-power window
.venv/Scripts/python -m tools.midday_usage --since 2026-05-01

# Commit gate: lint + tests, both must pass
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m pytest
```
