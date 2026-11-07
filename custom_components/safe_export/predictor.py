"""Run the bundled four-zone model for every confidence level, failing closed.

This module has no Home Assistant dependencies: it takes plain numbers (or None
for an unavailable input) and returns a result shaped like the Node-RED flow's
`msg.result`, so the shadow comparison against Node-RED is like-for-like.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .const import ZONE_ERROR
from .model import PredictInputs, predict

COEFFICIENTS_FILE = Path(__file__).with_name("coefficients.json")

# The coefficient names src/model.py reads from cfg.model (src/config.py ModelConfig).
MODEL_KEYS: tuple[str, ...] = (
    "heating_intercept",
    "heating_b_temp",
    "heating_b_solcast",
    "heating_temp_only_intercept",
    "heating_temp_only_b_temp",
    "cooling_intercept",
    "cooling_b_temp",
    "cooling_b_humidity",
    "cooling_temp_only_intercept",
    "cooling_temp_only_b_temp",
    "mild_p50",
    "mild_p75",
    "mild_p90",
    "mild_p95",
    "warm_boundary_p50",
    "warm_boundary_p75",
    "warm_boundary_p90",
    "warm_boundary_p95",
    "heating_p95_buffer_kwh",
    "cooling_p95_buffer_kwh",
)

CONFIDENCE_LEVELS: dict[str, float] = {"p50": 0.50, "p75": 0.75, "p90": 0.90, "p95": 0.95}


def load_coefficients(path: Path = COEFFICIENTS_FILE) -> dict[str, float]:
    """Read and validate the model coefficients file. Blocking I/O."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    model = raw.get("model")
    if not isinstance(model, dict):
        raise ValueError(f"{path.name}: missing 'model' object")
    missing = [k for k in MODEL_KEYS if k not in model]
    extra = [k for k in model if k not in MODEL_KEYS]
    if missing or extra:
        raise ValueError(f"{path.name}: missing keys {missing}, unexpected keys {extra}")
    coefficients: dict[str, float] = {}
    for key in MODEL_KEYS:
        value = model[key]
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"{path.name}: {key} must be a number, got {value!r}")
        if not math.isfinite(value):
            raise ValueError(f"{path.name}: {key} must be finite, got {value!r}")
        coefficients[key] = float(value)
    return coefficients


@dataclass(frozen=True)
class ModelInputs:
    """Input values read from Home Assistant. None means unavailable."""

    temp: float | None
    humidity: float | None
    solcast_kwh: float | None
    soc: float | None
    min_soc: float | None
    """Battery minimum SoC in percent (0–100), as the BYD sensor reports it."""


@dataclass(frozen=True)
class RunResult:
    """Outcome of one prediction run."""

    safe_export_wh: int
    """P50 safe export (Wh). 0 whenever the run failed."""

    zone: str
    detail: dict[str, Any] = field(repr=False)
    updated_at: datetime

    @property
    def ok(self) -> bool:
        return self.zone != ZONE_ERROR


def _round_half_up(value: float) -> int:
    """Round a non-negative Wh value half up, as the Node-RED flow's Math.round does.

    Rounding to 6 dp first absorbs float noise on exact half-Wh ties: e.g. 25 °C
    with no humidity at SoC 100 % gives exactly 252.5 Wh, which model.py's order
    of operations lands at 252.4999999999982 and the flow's at 252.5000000000013.
    Without this the two would publish 252 and 253.
    """
    return math.floor(round(value, 6) + 0.5)


def _zero_levels() -> dict[str, dict[str, float | int]]:
    return {
        key: {
            "safe_export": 0,
            "consumption": 0.0,
            "buffer": 0.0,
            "total_needed": 0.0,
            "grid_needed": 0.0,
        }
        for key in CONFIDENCE_LEVELS
    }


def fail_closed(inputs: ModelInputs | None, reason: str, now: datetime) -> RunResult:
    """Build the zeroed result published whenever a prediction cannot be made."""
    detail: dict[str, Any] = {
        "zone": ZONE_ERROR,
        "model_variant": None,
        "temp": inputs.temp if inputs else None,
        "humidity": inputs.humidity if inputs else None,
        "solcast_kwh": inputs.solcast_kwh if inputs else None,
        "soc": inputs.soc if inputs else None,
        "min_soc": inputs.min_soc if inputs else None,
        "available_kwh": 0.0,
        "reasoning": reason,
        "reason": reason,
        **_zero_levels(),
        "updated_at": now.isoformat(),
    }
    return RunResult(safe_export_wh=0, zone=ZONE_ERROR, detail=detail, updated_at=now)


def run_prediction(
    inputs: ModelInputs,
    coefficients: dict[str, float],
    battery_capacity_kwh: float,
    now: datetime,
) -> RunResult:
    """Run predict() at P50/P75/P90/P95. Never raises: failures publish 0 and a reason.

    temp, soc and min_soc are hard-required. Missing Solcast (heating zone) or
    humidity (cooling zone) falls back to the temp-only model variant, exactly as
    src/model.py does.
    """
    missing = [
        name
        for name, value in (("temp", inputs.temp), ("soc", inputs.soc), ("min_soc", inputs.min_soc))
        if value is None
    ]
    if missing:
        return fail_closed(
            inputs, f"Required input(s) unavailable: {', '.join(missing)}", now
        )
    assert inputs.temp is not None and inputs.soc is not None and inputs.min_soc is not None

    try:
        cfg = SimpleNamespace(
            battery_capacity_wh=battery_capacity_kwh * 1000.0,
            battery_reserve_fraction=inputs.min_soc / 100.0,
            model=SimpleNamespace(**coefficients),
        )
        solcast_wh = inputs.solcast_kwh * 1000.0 if inputs.solcast_kwh is not None else None

        levels: dict[str, dict[str, float | int]] = {}
        p50 = None
        for key, confidence in CONFIDENCE_LEVELS.items():
            result = predict(
                PredictInputs(
                    soc_at_6pm=inputs.soc,
                    bom_temp_mean=inputs.temp,
                    solcast_forecast_tomorrow_wh=solcast_wh,
                    bom_humidity_mean=inputs.humidity,
                    min_soc=inputs.min_soc / 100.0,
                    confidence=confidence,
                ),
                cfg,  # type: ignore[arg-type]  # duck-typed stand-in for src.config.Config
            )
            total_needed = result.predicted_consumption_kwh + result.error_buffer_kwh
            available_kwh = result.available_discharge_wh / 1000.0
            levels[key] = {
                "safe_export": _round_half_up(result.safe_export_wh),
                "consumption": round(result.predicted_consumption_kwh, 3),
                "buffer": round(result.error_buffer_kwh, 3),
                "total_needed": round(total_needed, 3),
                "grid_needed": round(max(0.0, total_needed - available_kwh), 3),
            }
            if key == "p50":
                p50 = result
    except Exception as err:  # any model failure must publish 0, never a stale value
        return fail_closed(inputs, f"Prediction error: {err!r}", now)

    assert p50 is not None
    detail: dict[str, Any] = {
        "zone": p50.zone,
        "model_variant": p50.model_variant,
        "temp": inputs.temp,
        "humidity": inputs.humidity,
        "solcast_kwh": inputs.solcast_kwh,
        "soc": inputs.soc,
        "min_soc": inputs.min_soc,
        "available_kwh": round(p50.available_discharge_wh / 1000.0, 3),
        "reasoning": p50.reasoning,
        "reason": None,
        **levels,
        "updated_at": now.isoformat(),
    }
    return RunResult(
        safe_export_wh=int(levels["p50"]["safe_export"]),
        zone=p50.zone,
        detail=detail,
        updated_at=now,
    )
