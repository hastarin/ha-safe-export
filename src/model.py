"""Three-zone linear model for overnight energy consumption prediction.

Produces a safe-export recommendation at 6pm on day N: the maximum Wh that can
be exported between 6–9pm such that SoC at 11am next day remains above a
configurable safety threshold with ~90% confidence.

Zones are determined by forecast overnight mean temperature (bom_temp_mean):
  - Heating  : < 19 °C  — OLS on temp + Solcast (R²=0.77), temp-only fallback (R²=0.71)
  - Mild     : 19–21 °C — empirical percentile table (no predictive signal from temp)
  - Cooling  : > 21 °C  — OLS on temp + humidity (R²=0.37, weak; improves with more data)

Coefficients fitted on chronological 80% training split (up to 2025-11-16, n=676).
Held-out test MAE = 1.75 kWh; P95 error buffer (3.56 kWh) covers 92% of test residuals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

BATTERY_CAPACITY_WH: float = 13_800.0

Provider = Literal["ea", "amber", "globird"]

# ---------------------------------------------------------------------------
# Hardcoded model coefficients (fitted on training set ≤ 2025-11-16)
# ---------------------------------------------------------------------------

# Heating zone (bom_temp_mean < 19°C)
# consumption_kwh = H_INTERCEPT + H_B_TEMP * temp + H_B_SOLCAST * solcast_kwh
_H_INTERCEPT: float = 19.7258
_H_B_TEMP: float = -0.7756
_H_B_SOLCAST: float = -0.070291  # per kWh of Solcast forecast

# Heating zone fallback when Solcast is unavailable
# consumption_kwh = H_TEMP_ONLY_INTERCEPT + H_TEMP_ONLY_B_TEMP * temp
_H_TEMP_ONLY_INTERCEPT: float = 18.8039
_H_TEMP_ONLY_B_TEMP: float = -0.8614

# Cooling zone (bom_temp_mean > 21°C)
# consumption_kwh = C_INTERCEPT + C_B_TEMP * temp + C_B_HUMIDITY * humidity_pct
_C_INTERCEPT: float = -13.4046
_C_B_TEMP: float = 0.7231
_C_B_HUMIDITY: float = 0.059498  # per % relative humidity

# Cooling zone fallback when humidity is unavailable
_C_TEMP_ONLY_INTERCEPT: float = -6.756
_C_TEMP_ONLY_B_TEMP: float = 0.660

# Mild zone (19°C ≤ bom_temp_mean ≤ 21°C): empirical percentile table
_MILD_PERCENTILES: dict[str, float] = {
    "p50": 4.601,
    "p75": 6.583,
    "p90": 7.829,
    "p95": 8.425,
}

# P95 absolute residual buffers (kWh) — covers ~92% of held-out test errors
_HEATING_P95_BUFFER_KWH: float = 3.562
_COOLING_P95_BUFFER_KWH: float = 3.136
_MILD_P95_BUFFER_KWH: float = 0.0  # percentile table already encodes the distribution


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass
class PredictInputs:
    """All inputs available at 6pm on day N required for the safe-export prediction."""

    soc_at_6pm: float
    """Current battery State of Charge (0–100 %)."""

    bom_temp_mean: float
    """Forecast overnight mean temperature (°C). Used to select zone and predict consumption."""

    provider: Provider
    """Current energy provider — recorded for model stratification; does not alter the prediction."""

    solcast_forecast_tomorrow_wh: float | None = None
    """Solcast full-day PV forecast for tomorrow (Wh). Available from Oct 2024 onward.
    Used as a cloud-cover proxy in the heating model and as a morning-solar estimate."""

    bom_humidity_mean: float | None = None
    """Forecast overnight mean relative humidity (%). Used in the cooling model."""

    min_soc: float = 0.10
    """Battery's configured minimum discharge floor (fraction, e.g. 0.10 = 10%).
    Pass whatever the HA min SoC is set to at decision time — e.g. 0.20 in storm mode."""

    confidence: float = 0.90
    """Desired confidence level for the safety constraint. Supported: 0.50, 0.75, 0.90, 0.95."""


@dataclass
class PredictResult:
    """Output of the safe-export prediction."""

    safe_export_wh: float
    """Recommended maximum export (Wh) between 6–9pm. Always ≥ 0."""

    predicted_consumption_kwh: float
    """Point estimate of overnight consumption (6pm–11am next day), kWh."""

    error_buffer_kwh: float
    """One-sided uncertainty buffer applied at the requested confidence level, kWh."""

    zone: Literal["heating", "mild", "cooling"]
    """Thermal zone used for this prediction."""

    model_variant: str
    """Which model variant was used (e.g. 'heating_with_solcast')."""

    available_discharge_wh: float
    """(SoC_now − min_soc) × capacity — energy available before hitting the configured floor."""

    reasoning: str = field(default="", repr=False)
    """Human-readable explanation of the recommendation."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _select_percentile(percentiles: dict[str, float], confidence: float) -> float:
    """Return the percentile value closest to the requested confidence level."""
    mapping = {0.50: "p50", 0.75: "p75", 0.90: "p90", 0.95: "p95"}
    key = min(mapping, key=lambda k: abs(k - confidence))
    return percentiles[mapping[key]]


def _predict_consumption(
    inputs: PredictInputs,
) -> tuple[float, float, str, Literal["heating", "mild", "cooling"]]:
    """Return (point_estimate_kwh, p95_buffer_kwh, model_variant, zone)."""
    temp = inputs.bom_temp_mean

    if temp < 19.0:
        if inputs.solcast_forecast_tomorrow_wh is not None:
            solcast_kwh = inputs.solcast_forecast_tomorrow_wh / 1000.0
            est = _H_INTERCEPT + _H_B_TEMP * temp + _H_B_SOLCAST * solcast_kwh
            return est, _HEATING_P95_BUFFER_KWH, "heating_with_solcast", "heating"
        else:
            est = _H_TEMP_ONLY_INTERCEPT + _H_TEMP_ONLY_B_TEMP * temp
            return est, _HEATING_P95_BUFFER_KWH, "heating_temp_only", "heating"

    elif temp <= 21.0:
        # Mild zone: return P50 as point estimate; caller applies confidence-appropriate value
        est = _MILD_PERCENTILES["p50"]
        return est, _MILD_P95_BUFFER_KWH, "mild_empirical", "mild"

    else:  # cooling
        if inputs.bom_humidity_mean is not None:
            est = _C_INTERCEPT + _C_B_TEMP * temp + _C_B_HUMIDITY * inputs.bom_humidity_mean
            return est, _COOLING_P95_BUFFER_KWH, "cooling_with_humidity", "cooling"
        else:
            est = _C_TEMP_ONLY_INTERCEPT + _C_TEMP_ONLY_B_TEMP * temp
            return est, _COOLING_P95_BUFFER_KWH, "cooling_temp_only", "cooling"


def _provider_note(provider: Provider) -> str:
    if provider == "globird":
        return "Provider: GloBird (free 11am–2pm window guarantees next-day recharge)."
    elif provider == "amber":
        return "Provider: Amber (variable wholesale pricing)."
    return "Provider: Energy Australia (flat-rate)."


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def predict(inputs: PredictInputs) -> PredictResult:
    """Compute the safe-export recommendation for the evening peak (6–9pm).

    Uses actuals at training time and forecasts at inference time — the interface
    is identical. Returns a PredictResult with safe_export_wh ≥ 0.
    """
    capacity_wh = BATTERY_CAPACITY_WH

    available_discharge_wh = max(
        0.0, (inputs.soc_at_6pm / 100.0 - inputs.min_soc) * capacity_wh
    )

    point_kwh, p95_buffer_kwh, variant, zone = _predict_consumption(inputs)

    # For mild zone, use the confidence-appropriate percentile as the consumption estimate
    # rather than adding a separate buffer on top of P50.
    if zone == "mild":
        consumption_estimate_kwh = _select_percentile(_MILD_PERCENTILES, inputs.confidence)
        buffer_kwh = 0.0
    else:
        consumption_estimate_kwh = point_kwh
        # Scale the P95 buffer to the requested confidence level.
        # P95 is the 95th percentile of abs(residual); for lower confidence we scale linearly.
        # This is conservative but avoids over-engineering with a formal quantile model.
        confidence_scale = {0.50: 0.31, 0.75: 0.58, 0.90: 0.87, 0.95: 1.00}
        scale = min(confidence_scale.items(), key=lambda kv: abs(kv[0] - inputs.confidence))[1]
        buffer_kwh = p95_buffer_kwh * scale

    predicted_consumption_wh = consumption_estimate_kwh * 1000.0
    total_needed_wh = predicted_consumption_wh + buffer_kwh * 1000.0

    safe_export_wh = max(0.0, available_discharge_wh - total_needed_wh)

    reasoning_parts = [
        f"Zone: {zone} ({inputs.bom_temp_mean:.1f}°C).",
        f"Model: {variant}.",
        f"Predicted consumption: {consumption_estimate_kwh:.2f} kWh"
        + (
            f" + {buffer_kwh:.2f} kWh buffer ({inputs.confidence*100:.0f}% confidence)."
            if buffer_kwh
            else "."
        ),
        f"Available discharge: {available_discharge_wh/1000:.2f} kWh "
        f"(SoC {inputs.soc_at_6pm:.0f}% → {inputs.min_soc*100:.0f}% floor).",
        f"Safe export: {safe_export_wh/1000:.2f} kWh.",
        _provider_note(inputs.provider),
    ]

    return PredictResult(
        safe_export_wh=safe_export_wh,
        predicted_consumption_kwh=consumption_estimate_kwh,
        error_buffer_kwh=buffer_kwh,
        zone=zone,
        model_variant=variant,
        available_discharge_wh=available_discharge_wh,
        reasoning=" ".join(reasoning_parts),
    )
