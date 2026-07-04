"""Grid-equivalence test: src/model.py predict() vs. the extracted Node-RED JS function.

Runs the *actual* "Four-zone model" function body from tools/nodered-flow.json under
Node.js for a grid of inputs (temp, soc, confidence, with/without Solcast, with/without
humidity) and asserts the Wh output matches src/model.py's predict() within 1 Wh. This
is the parity test called for in the Node-RED fallback/fail-closed issue — it exists
because nothing else verifies the two implementations produce the same numbers.

Requires a `node` executable on PATH; skips cleanly if unavailable (CI/dev boxes without
Node.js should not fail here — see CLAUDE.md "skip is acceptable, error is not").
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.config import Config
from src.model import PredictInputs, predict

FLOW_JSON = Path("tools/nodered-flow.json")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node executable not found on PATH")


def _load_flow_func() -> str:
    nodes = json.loads(FLOW_JSON.read_text(encoding="utf-8"))
    for node in nodes:
        if node.get("name") == "Four-zone model":
            return node["func"]
    raise AssertionError('No node named "Four-zone model" found in nodered-flow.json')


_HARNESS_PREFIX = """
const msg = __INPUT_MSG__;
const node = { error: () => {}, log: () => {} };

(function () {
"""

_HARNESS_SUFFIX = """
})();

console.log(JSON.stringify(msg.result));
"""


def _run_flow(msg_in_json: str) -> dict:
    """Run the flow function once for a single raw JSON `msg` literal, return msg.result."""
    func_body = _load_flow_func()
    script = _HARNESS_PREFIX.replace("__INPUT_MSG__", msg_in_json) + func_body + _HARNESS_SUFFIX
    proc = subprocess.run(
        [NODE, "--input-type=commonjs", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, f"node failed for input {msg_in_json}:\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


BATTERY_KWH = 13.8  # matches nodered-flow.json's BATTERY_KWH; test_cfg uses the same capacity


def _grid_cases() -> list[dict]:
    """(temp, soc, min_soc, solcast_present, humidity_present) grid, all NaN-free."""
    cases = []
    for temp in [5.0, 10.0, 16.9, 18.0, 20.0, 21.5, 25.0, 30.0]:
        for soc in [20.0, 50.0, 80.0, 100.0]:
            for solcast_present in (True, False):
                for humidity_present in (True, False):
                    cases.append(
                        {
                            "temp": temp,
                            "soc": soc,
                            "min_soc": 10.0,
                            "solcast_present": solcast_present,
                            "humidity_present": humidity_present,
                        }
                    )
    return cases


@pytest.mark.parametrize("confidence", [0.50, 0.75, 0.90, 0.95])
def test_flow_matches_predict_across_grid(test_cfg: Config, confidence: float):
    cases = _grid_cases()
    solcast_wh = 5000.0
    humidity_pct = 60.0

    flow_inputs = [
        {
            "temp": c["temp"],
            "humidity": humidity_pct if c["humidity_present"] else "NaN",
            "solcast_kwh": (solcast_wh / 1000.0) if c["solcast_present"] else "NaN",
            "soc": c["soc"],
            "minSoc": c["min_soc"],
        }
        for c in cases
    ]
    # NaN isn't valid JSON — encode as the bare token the JS runtime understands,
    # then patch the serialized payload since json.dumps can't emit it directly.
    payloads = []
    for fi in flow_inputs:
        obj = dict(fi)
        raw = json.dumps(obj)
        for key in ("humidity", "solcast_kwh"):
            raw = raw.replace(f'"{key}": "NaN"', f'"{key}": NaN')
            raw = raw.replace(f'"{key}":"NaN"', f'"{key}":NaN')
        payloads.append(raw)

    conf_key = {0.50: "p50", 0.75: "p75", 0.90: "p90", 0.95: "p95"}[confidence]

    flow_results = [_run_flow(raw) for raw in payloads]

    for case, flow_result in zip(cases, flow_results):
        py_inputs = PredictInputs(
            soc_at_6pm=case["soc"],
            bom_temp_mean=case["temp"],
            solcast_forecast_tomorrow_wh=solcast_wh if case["solcast_present"] else None,
            bom_humidity_mean=humidity_pct if case["humidity_present"] else None,
            min_soc=case["min_soc"] / 100.0,
            confidence=confidence,
        )
        # predict() uses cfg.battery_capacity_wh; align it to the flow's BATTERY_KWH
        # for this comparison (the flow hardcodes its own battery size).
        aligned_cfg = Config(
            battery_capacity_wh=BATTERY_KWH * 1000.0,
            battery_reserve_fraction=test_cfg.battery_reserve_fraction,
            timezone=test_cfg.timezone,
            sensors=test_cfg.sensors,
            providers=test_cfg.providers,
            model=test_cfg.model,
        )
        py_result = predict(py_inputs, aligned_cfg)

        flow_wh = flow_result[conf_key]["safe_export"]
        py_wh = py_result.safe_export_wh

        assert abs(flow_wh - py_wh) <= 1.0, (
            f"Mismatch at {conf_key} for temp={case['temp']}, soc={case['soc']}, "
            f"solcast_present={case['solcast_present']}, "
            f"humidity_present={case['humidity_present']}: "
            f"flow={flow_wh} Wh vs predict()={py_wh} Wh"
        )
        assert flow_result["zone"] == py_result.zone, (
            f"Zone mismatch for temp={case['temp']}: flow={flow_result['zone']!r} "
            f"vs predict()={py_result.zone!r}"
        )


def test_flow_fails_closed_on_missing_required_inputs(test_cfg: Config):
    """temp/soc/minSoc NaN must yield zone='error' and safe_export=0 at every confidence."""
    bad_inputs = [
        {"temp": "NaN", "humidity": 60.0, "solcast_kwh": 5.0, "soc": 80.0, "minSoc": 10.0},
        {"temp": 15.0, "humidity": 60.0, "solcast_kwh": 5.0, "soc": "NaN", "minSoc": 10.0},
        {"temp": 15.0, "humidity": 60.0, "solcast_kwh": 5.0, "soc": 80.0, "minSoc": "NaN"},
    ]
    for msg_in in bad_inputs:
        raw = json.dumps(msg_in).replace('"NaN"', "NaN")
        result = _run_flow(raw)

        assert result["zone"] == "error", (
            f"Expected zone='error' for {msg_in}, got {result['zone']!r}"
        )
        assert result.get("reason"), f"Expected a non-empty reason string for {msg_in}"
        for conf_key in ("p50", "p75", "p90", "p95"):
            assert result[conf_key]["safe_export"] == 0, (
                f"Expected safe_export=0 for {conf_key} on {msg_in}, got {result[conf_key]}"
            )


def test_flow_falls_back_when_solcast_or_humidity_missing():
    """Missing Solcast (heating) or humidity (cooling) must NOT trigger the error zone."""
    cases = [
        # Heating zone, no Solcast
        {"temp": 10.0, "humidity": 60.0, "solcast_kwh": "NaN", "soc": 80.0, "minSoc": 10.0},
        # Cooling zone, no humidity
        {"temp": 25.0, "humidity": "NaN", "solcast_kwh": 5.0, "soc": 80.0, "minSoc": 10.0},
    ]
    for msg_in in cases:
        raw = json.dumps(msg_in).replace('"NaN"', "NaN")
        result = _run_flow(raw)

        assert result["zone"] != "error", f"Expected a real zone (not error) for {msg_in}"
        assert "temp-only" in result["model_info"].lower(), (
            f"Expected temp-only fallback variant noted in model_info for {msg_in}, "
            f"got: {result['model_info']!r}"
        )
