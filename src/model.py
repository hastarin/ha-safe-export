"""Four-zone model for overnight energy consumption prediction.

Produces a safe-export recommendation at 6pm on day N: the maximum Wh that can
be exported between 6–9pm such that SoC at 11am next day remains above a
configurable safety threshold with ~90% confidence.

Zones are determined by forecast overnight mean temperature (bom_temp_mean):
  - Heating       : < 17 °C  — OLS on temp + Solcast (R²=0.83), temp-only fallback (R²=0.82)
  - Warm boundary : 17–19 °C — empirical percentile table (no weather signal in this band)
  - Mild          : 19–21 °C — empirical percentile table (no predictive signal from temp)
  - Cooling       : > 21 °C  — OLS on temp + humidity (R²=0.52, improves with more data)

NOTE ON THE "MILD" LABEL: 19–21 °C is *not* the consumption sweet spot — that
minimum actually sits in the warm-boundary band (17–19 °C, ~6.0 kWh). The 1 °C
consumption profile shows 19–21 °C is already on the cooling upslope (~6.8 kWh),
so the mild table consistently sits *above* the warm-boundary table. This is
correct, not a bug: "mild" is a retained misnomer for what is really a
low-cooling shoulder. Bands were reviewed against the profile on 2026-05-22 and
kept at 17/19/21; renaming was rejected as cosmetic churn across three model
implementations (this file, tools/predictor.html, tools/nodered-flow.json).
See DECISIONS.md "Zone bands retained at 17/19/21 after retraining".

Coefficients and percentile tables are loaded from config.yaml.
Held-out test MAE = 1.75 kWh (heating zone); violation rate 2.4% on stratified test set.

The safe-export formula:
  E_export_max = (SoC_now − min_soc) × capacity − predicted_consumption

Solar credit is deliberately excluded: morning solar arrives in a ~3-hour burst
(8–11am) but the battery must survive the full 17-hour window on its own. Including
solar inflates the recommendation beyond what is safe overnight. See DECISIONS.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from src.config import Config


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

    solcast_forecast_tomorrow_wh: float | None = None
    """Solcast full-day PV forecast for tomorrow (Wh). Available from Oct 2024 onward.
    Used as a cloud-cover proxy in the consumption model and to estimate morning solar credit."""

    bom_humidity_mean: float | None = None
    """Forecast overnight mean relative humidity (%). Used in the cooling model."""

    min_soc: float | None = None
    """Battery's configured minimum discharge floor (fraction, e.g. 0.10 = 10%).
    Defaults to cfg.battery_reserve_fraction when None. Pass an explicit value to
    override at call time — e.g. 0.20 in storm mode."""

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

    zone: Literal["heating", "warm_boundary", "mild", "cooling"]
    """Thermal zone used for this prediction."""

    model_variant: str
    """Which model variant was used (e.g. 'heating_with_solcast')."""

    available_discharge_wh: float
    """(SoC_now − min_soc) × capacity — energy available before hitting the configured floor."""

    reasoning: str = field(default="", repr=False)
    """Human-readable explanation of the recommendation."""


# Ratios of P50/P75/P90 to P95 of heating-zone |residual| (recomputed 2026-05-22
# from the post-fix dataset; drift from prior was negligible). Mirrored in the
# Node-RED flow's CONF ladder — keep in sync (see CLAUDE.md). tools/retrain.py
# imports this to report drift against freshly recomputed ratios.
CONFIDENCE_SCALE: dict[float, float] = {0.50: 0.33, 0.75: 0.58, 0.90: 0.88, 0.95: 1.00}


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
    cfg: Config,
) -> tuple[float, float, str, Literal["heating", "warm_boundary", "mild", "cooling"]]:
    """Return (point_estimate_kwh, p95_buffer_kwh, model_variant, zone)."""
    m = cfg.model
    temp = inputs.bom_temp_mean

    if temp < 17.0:
        if inputs.solcast_forecast_tomorrow_wh is not None:
            solcast_kwh = inputs.solcast_forecast_tomorrow_wh / 1000.0
            est = m.heating_intercept + m.heating_b_temp * temp + m.heating_b_solcast * solcast_kwh
            return est, m.heating_p95_buffer_kwh, "heating_with_solcast", "heating"
        else:
            est = m.heating_temp_only_intercept + m.heating_temp_only_b_temp * temp
            return est, m.heating_p95_buffer_kwh, "heating_temp_only", "heating"

    elif temp < 19.0:
        # Warm boundary: no predictive weather signal, use empirical percentile table
        return m.warm_boundary_p50, 0.0, "warm_boundary_empirical", "warm_boundary"

    elif temp <= 21.0:
        # "mild" is a misnomer — this band is the low-cooling shoulder, not the
        # sweet spot (which is the warm-boundary band below). See module docstring.
        return m.mild_p50, 0.0, "mild_empirical", "mild"

    else:  # cooling
        if inputs.bom_humidity_mean is not None:
            est = (
                m.cooling_intercept
                + m.cooling_b_temp * temp
                + m.cooling_b_humidity * inputs.bom_humidity_mean
            )
            return est, m.cooling_p95_buffer_kwh, "cooling_with_humidity", "cooling"
        else:
            est = m.cooling_temp_only_intercept + m.cooling_temp_only_b_temp * temp
            return est, m.cooling_p95_buffer_kwh, "cooling_temp_only", "cooling"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def predict(inputs: PredictInputs, cfg: Config) -> PredictResult:
    """Compute the safe-export recommendation for the evening peak (6–9pm).

    Uses actuals at training time and forecasts at inference time — the interface
    is identical. Returns a PredictResult with safe_export_wh ≥ 0.
    """
    capacity_wh = cfg.battery_capacity_wh
    min_soc = inputs.min_soc if inputs.min_soc is not None else cfg.battery_reserve_fraction

    available_discharge_wh = max(
        0.0, (inputs.soc_at_6pm / 100.0 - min_soc) * capacity_wh
    )

    point_kwh, p95_buffer_kwh, variant, zone = _predict_consumption(inputs, cfg)

    m = cfg.model
    mild_percentiles = {"p50": m.mild_p50, "p75": m.mild_p75, "p90": m.mild_p90, "p95": m.mild_p95}
    warm_boundary_percentiles = {
        "p50": m.warm_boundary_p50, "p75": m.warm_boundary_p75,
        "p90": m.warm_boundary_p90, "p95": m.warm_boundary_p95,
    }

    if zone == "warm_boundary":
        consumption_estimate_kwh = _select_percentile(warm_boundary_percentiles, inputs.confidence)
        buffer_kwh = 0.0
    elif zone == "mild":
        consumption_estimate_kwh = _select_percentile(mild_percentiles, inputs.confidence)
        buffer_kwh = 0.0
    else:
        consumption_estimate_kwh = point_kwh
        scale = min(CONFIDENCE_SCALE.items(), key=lambda kv: abs(kv[0] - inputs.confidence))[1]
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
        f"(SoC {inputs.soc_at_6pm:.0f}% → {min_soc*100:.0f}% floor).",
        f"Safe export: {safe_export_wh/1000:.2f} kWh.",
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
