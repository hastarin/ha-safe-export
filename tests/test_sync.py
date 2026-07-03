"""Coefficient-parity test: config/conftest vs nodered-flow.json vs model.py ladder.

The four-zone model coefficients are hand-copied into three places (see CLAUDE.md
"Model coefficients are duplicated in three places"): config/config.yaml (canonical,
gitignored), tests/conftest.py's test_cfg fixture (documented synced copy), and the
"Four-zone model" function node in tools/nodered-flow.json (the live system). The
confidence ladder is additionally hardcoded in src/model.py. Nothing enforces these
stay in sync except convention — this test fails loudly the moment any copy drifts.
"""

from __future__ import annotations

import json
import re
from dataclasses import fields
from pathlib import Path

import pytest

from src.config import load_config
from src.model import CONFIDENCE_SCALE

FLOW_JSON = Path("tools/nodered-flow.json")
REAL_CONFIG = Path("config/config.yaml")

_NUM = r"-?\d+\.?\d*"


def _load_flow_func() -> str:
    nodes = json.loads(FLOW_JSON.read_text(encoding="utf-8"))
    for node in nodes:
        if node.get("name") == "Four-zone model":
            return node["func"]
    raise AssertionError('No node named "Four-zone model" found in nodered-flow.json')


def _extract_triple(func: str, name: str) -> tuple[float, float, float]:
    """Extract `const NAME = { b0: x, b1: y, b2: z };`-style triples."""
    m = re.search(
        rf"const\s+{name}\s*=\s*\{{\s*b0:\s*({_NUM}),\s*b1:\s*({_NUM}),\s*b2:\s*({_NUM})\s*\}}",
        func,
    )
    assert m, f"Could not find `const {name} = {{ b0, b1, b2 }}` in nodered-flow.json"
    return float(m.group(1)), float(m.group(2)), float(m.group(3))


def _extract_scalar(func: str, name: str) -> float:
    m = re.search(rf"const\s+{name}\s*=\s*({_NUM})\s*;", func)
    assert m, f"Could not find `const {name} = ...;` in nodered-flow.json"
    return float(m.group(1))


def _extract_percentiles(func: str, name: str) -> tuple[float, float, float, float]:
    m = re.search(
        rf"const\s+{name}\s*=\s*\{{\s*"
        rf"p50:\s*({_NUM}),\s*p75:\s*({_NUM}),\s*p90:\s*({_NUM}),\s*p95:\s*({_NUM})\s*\}}",
        func,
    )
    assert m, f"Could not find `const {name} = {{ p50, p75, p90, p95 }}` in nodered-flow.json"
    return tuple(float(g) for g in m.groups())  # type: ignore[return-value]


def _extract_conf_ladder(func: str) -> dict[str, float]:
    m = re.search(r"const\s+CONF\s*=\s*\[(.*?)\];", func, re.DOTALL)
    assert m, "Could not find `const CONF = [...]` in nodered-flow.json"
    entries = re.findall(
        rf'key:\s*"(p\d{{2}})",\s*label:\s*"[^"]*",\s*scale:\s*({_NUM})', m.group(1)
    )
    assert entries, "Could not parse CONF ladder entries in nodered-flow.json"
    return {key: float(scale) for key, scale in entries}


def _flow_coefficients() -> dict[str, float | dict[str, float]]:
    func = _load_flow_func()
    h_b0, h_b1, h_b2 = _extract_triple(func, "H")
    c_b0, c_b1, c_b2 = _extract_triple(func, "C")
    return {
        "heating_intercept": h_b0,
        "heating_b_temp": h_b1,
        "heating_b_solcast": h_b2,
        "heating_p95_buffer_kwh": _extract_scalar(func, "H_P95"),
        "cooling_intercept": c_b0,
        "cooling_b_temp": c_b1,
        "cooling_b_humidity": c_b2,
        "cooling_p95_buffer_kwh": _extract_scalar(func, "C_P95"),
        "warm_boundary": dict(
            zip(("p50", "p75", "p90", "p95"), _extract_percentiles(func, "WARM"))
        ),
        "mild": dict(zip(("p50", "p75", "p90", "p95"), _extract_percentiles(func, "MILD"))),
        "conf_ladder": _extract_conf_ladder(func),
    }


def test_nodered_flow_matches_test_cfg(test_cfg):
    flow = _flow_coefficients()
    m = test_cfg.model

    scalar_pairs = [
        ("heating_intercept", flow["heating_intercept"], m.heating_intercept),
        ("heating_b_temp", flow["heating_b_temp"], m.heating_b_temp),
        ("heating_b_solcast", flow["heating_b_solcast"], m.heating_b_solcast),
        ("heating_p95_buffer_kwh", flow["heating_p95_buffer_kwh"], m.heating_p95_buffer_kwh),
        ("cooling_intercept", flow["cooling_intercept"], m.cooling_intercept),
        ("cooling_b_temp", flow["cooling_b_temp"], m.cooling_b_temp),
        ("cooling_b_humidity", flow["cooling_b_humidity"], m.cooling_b_humidity),
        ("cooling_p95_buffer_kwh", flow["cooling_p95_buffer_kwh"], m.cooling_p95_buffer_kwh),
    ]
    for key, flow_val, cfg_val in scalar_pairs:
        assert flow_val == cfg_val, (
            f"{key} drifted: nodered-flow.json={flow_val!r} vs test_cfg={cfg_val!r}"
        )

    for pct in ("p50", "p75", "p90", "p95"):
        flow_val = flow["warm_boundary"][pct]
        cfg_val = getattr(m, f"warm_boundary_{pct}")
        assert flow_val == cfg_val, (
            f"warm_boundary_{pct} drifted: nodered-flow.json={flow_val!r} vs test_cfg={cfg_val!r}"
        )

        flow_val = flow["mild"][pct]
        cfg_val = getattr(m, f"mild_{pct}")
        assert flow_val == cfg_val, (
            f"mild_{pct} drifted: nodered-flow.json={flow_val!r} vs test_cfg={cfg_val!r}"
        )


def test_nodered_flow_conf_ladder_matches_model_py():
    flow_ladder = _extract_conf_ladder(_load_flow_func())
    key_to_confidence = {"p50": 0.50, "p75": 0.75, "p90": 0.90, "p95": 0.95}

    for key, confidence in key_to_confidence.items():
        flow_val = flow_ladder[key]
        model_val = CONFIDENCE_SCALE[confidence]
        assert flow_val == model_val, (
            f"CONF ladder scale for {key} drifted: "
            f"nodered-flow.json={flow_val!r} vs src/model.py CONFIDENCE_SCALE={model_val!r}"
        )


def test_test_cfg_matches_real_config(test_cfg):
    if not REAL_CONFIG.exists():
        pytest.skip("config/config.yaml not present (gitignored; local-only)")

    real_cfg = load_config(REAL_CONFIG)

    for f in fields(test_cfg.model):
        fixture_val = getattr(test_cfg.model, f.name)
        real_val = getattr(real_cfg.model, f.name)
        assert fixture_val == real_val, (
            f"{f.name} drifted: tests/conftest.py test_cfg={fixture_val!r} "
            f"vs config/config.yaml={real_val!r}"
        )
