"""The integration's predictor against the live Node-RED flow and the coefficients file.

The Stage 1 shadow exit criterion is "results match Node-RED on every compared
night", so the flow comparison here is exact on the published Wh, not ±1 Wh.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from custom_components.safe_export.predictor import (
    MODEL_KEYS,
    ModelInputs,
    _round_half_up,
    load_coefficients,
    run_prediction,
)
from tests.test_flow_parity import _run_flow

NOW = datetime(2026, 11, 7, 7, 0, tzinfo=UTC)
BATTERY_KWH = 13.8  # the flow's hardcoded BATTERY_KWH
LEVELS = ("p50", "p75", "p90", "p95")
# The diagnostic kWh fields are rounded to 3 dp in both implementations, from
# float values computed in a different order (the flow does (soc − min)/100 × kWh,
# model.py does (soc/100 − min) × Wh), so they can straddle a rounding boundary.
KWH_TOL = 0.0011


def _cases() -> list[ModelInputs]:
    cases = []
    for temp in [5.0, 10.0, 13.1, 16.9, 17.0, 18.0, 19.0, 20.0, 21.0, 21.5, 25.0, 30.0]:
        for soc in [20.0, 50.0, 80.0, 100.0]:
            for solcast in (32.0, None):
                for humidity in (60.0, None):
                    cases.append(ModelInputs(temp, humidity, solcast, soc, 10.0))
    return cases


def _flow_msg(i: ModelInputs) -> str:
    def num(v: float | None) -> str:
        return "NaN" if v is None else json.dumps(v)

    return (
        f'{{"temp": {num(i.temp)}, "humidity": {num(i.humidity)}, '
        f'"solcast_kwh": {num(i.solcast_kwh)}, "soc": {num(i.soc)}, "minSoc": {num(i.min_soc)}}}'
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node executable not found on PATH")
def test_matches_nodered_flow_exactly() -> None:
    coefficients = load_coefficients()
    for inputs in _cases():
        ours = run_prediction(inputs, coefficients, BATTERY_KWH, NOW).detail
        flow = _run_flow(_flow_msg(inputs))
        where = f"for {inputs}"
        assert ours["zone"] == flow["zone"], where
        assert ours["available_kwh"] == pytest.approx(flow["available_kwh"], abs=KWH_TOL), where
        for level in LEVELS:
            # The published Wh must match exactly.
            assert ours[level]["safe_export"] == flow[level]["safe_export"], f"{level} {where}"
            for key in ("consumption", "buffer", "total_needed", "grid_needed"):
                assert ours[level][key] == pytest.approx(flow[level][key], abs=KWH_TOL), (
                    f"{level}.{key} {where}"
                )


_BATCH_HARNESS = """
const inputs = JSON.parse(require("fs").readFileSync(0, "utf8"));
const node = { error: () => {}, log: () => {} };
const run = new Function("msg", "node", __FUNC__ + "\\nreturn msg;");
const out = inputs.map((i) => {
    const msg = {};
    for (const [k, v] of Object.entries(i)) msg[k] = v === null ? NaN : v;
    const r = run(msg, node).result;
    return [r.zone, r.p50.safe_export, r.p75.safe_export, r.p90.safe_export, r.p95.safe_export];
});
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node executable not found on PATH")
def test_matches_nodered_flow_on_dense_grid() -> None:
    """Published Wh at every level, over 0.1 °C steps and realistic SoC values.

    Live inputs are 0.1 °C temperatures and whole-percent SoC, which is where
    exact half-Wh ties (and so rounding disagreements) would show up.
    """
    from tests.test_flow_parity import _load_flow_func

    cases = [
        ModelInputs(t / 10, humidity, solcast, float(soc), min_soc)
        for t in range(0, 351)
        for soc in range(15, 101, 5)
        for min_soc in (10.0, 20.0)
        for solcast in (None, 32.07)
        for humidity in (None, 63.0)
    ]
    script = _BATCH_HARNESS.replace("__FUNC__", json.dumps(_load_flow_func()))
    payload = json.dumps(
        [
            {
                "temp": c.temp,
                "humidity": c.humidity,
                "solcast_kwh": c.solcast_kwh,
                "soc": c.soc,
                "minSoc": c.min_soc,
            }
            for c in cases
        ]
    )
    proc = subprocess.run(
        [shutil.which("node"), "--input-type=commonjs", "-e", script],
        input=payload,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    flow_rows = json.loads(proc.stdout)

    coefficients = load_coefficients()
    mismatches = []
    for inputs, row in zip(cases, flow_rows, strict=True):
        d = run_prediction(inputs, coefficients, BATTERY_KWH, NOW).detail
        ours = [d["zone"], *(d[level]["safe_export"] for level in LEVELS)]
        if ours != row:
            mismatches.append((inputs, ours, row))
    assert not mismatches, f"{len(mismatches)} of {len(cases)} differ, e.g. {mismatches[:3]}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node executable not found on PATH")
def test_fail_closed_matches_nodered_flow() -> None:
    coefficients = load_coefficients()
    inputs = ModelInputs(temp=15.0, humidity=60.0, solcast_kwh=30.0, soc=None, min_soc=10.0)
    ours = run_prediction(inputs, coefficients, BATTERY_KWH, NOW)
    flow = _run_flow(_flow_msg(inputs))
    assert ours.zone == flow["zone"] == "error"
    assert ours.safe_export_wh == 0
    for level in LEVELS:
        assert ours.detail[level] == flow[level]


def test_coefficients_cover_every_model_key() -> None:
    assert tuple(load_coefficients()) == MODEL_KEYS


@pytest.mark.parametrize(
    ("model", "match"),
    [
        ({k: 1.0 for k in MODEL_KEYS if k != "mild_p50"}, "missing"),
        ({**{k: 1.0 for k in MODEL_KEYS}, "surprise": 1.0}, "unexpected"),
        ({**{k: 1.0 for k in MODEL_KEYS}, "mild_p50": "6.8"}, "must be a number"),
        ({**{k: 1.0 for k in MODEL_KEYS}, "mild_p50": True}, "must be a number"),
    ],
)
def test_load_coefficients_rejects_bad_files(tmp_path: Path, model: dict, match: str) -> None:
    path = tmp_path / "coefficients.json"
    path.write_text(json.dumps({"model": model}), encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        load_coefficients(path)


def test_round_half_up_like_javascript() -> None:
    # Python's round() rounds half to even; the flow uses Math.round (half up).
    # Finding model inputs whose export lands exactly on .5 Wh is impractical,
    # so check the helper directly.
    assert _round_half_up(2.5) == 3
    assert _round_half_up(3.5) == 4
    assert _round_half_up(0.0) == 0
    # A true half-Wh tie that float noise puts just below .5 still rounds up.
    assert _round_half_up(252.49999999999818) == 253
    assert _round_half_up(252.4999) == 252


def test_half_wh_tie_matches_flow() -> None:
    # 25 °C, no humidity, SoC 100 %, floor 10 %: P95 is exactly 252.5 Wh.
    inputs = ModelInputs(temp=25.0, humidity=None, solcast_kwh=None, soc=100.0, min_soc=10.0)
    result = run_prediction(inputs, load_coefficients(), BATTERY_KWH, NOW)
    assert result.detail["p95"]["safe_export"] == 253
