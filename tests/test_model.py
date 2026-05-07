"""Tests for src/model.py — three-zone safe-export prediction model."""

import pytest

from src.model import (
    PredictInputs,
    PredictResult,
    predict,
)
from tests.fixtures import FIXTURES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _inputs_from_fixture(date: str, **overrides) -> PredictInputs:
    f = FIXTURES[date]
    base = PredictInputs(
        soc_at_6pm=f["soc_at_6pm"],
        bom_temp_mean=f["bom_temp_mean"],
        provider=f["provider"],
        solcast_forecast_tomorrow_wh=f.get("solcast_forecast_tomorrow_wh"),
        bom_humidity_mean=f.get("bom_humidity_mean"),
    )
    for k, v in overrides.items():
        object.__setattr__(base, k, v)
    return base


# ---------------------------------------------------------------------------
# Zone routing
# ---------------------------------------------------------------------------


def test_heating_zone_selected():
    inp = PredictInputs(soc_at_6pm=80, bom_temp_mean=10.0, provider="amber")
    result = predict(inp)
    assert result.zone == "heating"


def test_mild_zone_selected():
    inp = PredictInputs(soc_at_6pm=80, bom_temp_mean=20.0, provider="amber")
    result = predict(inp)
    assert result.zone == "mild"


def test_cooling_zone_selected():
    inp = PredictInputs(soc_at_6pm=80, bom_temp_mean=25.0, provider="amber")
    result = predict(inp)
    assert result.zone == "cooling"


def test_heating_with_solcast_variant():
    inp = PredictInputs(
        soc_at_6pm=80, bom_temp_mean=10.0, provider="amber",
        solcast_forecast_tomorrow_wh=15000,
    )
    result = predict(inp)
    assert result.model_variant == "heating_with_solcast"


def test_heating_temp_only_variant_when_no_solcast():
    inp = PredictInputs(soc_at_6pm=80, bom_temp_mean=10.0, provider="amber")
    result = predict(inp)
    assert result.model_variant == "heating_temp_only"


def test_cooling_with_humidity_variant():
    inp = PredictInputs(
        soc_at_6pm=80, bom_temp_mean=25.0, provider="amber",
        bom_humidity_mean=60.0,
    )
    result = predict(inp)
    assert result.model_variant == "cooling_with_humidity"


# ---------------------------------------------------------------------------
# Output invariants
# ---------------------------------------------------------------------------


def test_safe_export_never_negative():
    # Very cold night, low SoC — should clamp to 0 not go negative
    inp = PredictInputs(soc_at_6pm=25.0, bom_temp_mean=5.0, provider="amber")
    result = predict(inp)
    assert result.safe_export_wh >= 0.0


def test_safe_export_bounded_by_available_discharge():
    inp = PredictInputs(soc_at_6pm=100.0, bom_temp_mean=20.0, provider="globird")
    result = predict(inp)
    assert result.safe_export_wh <= result.available_discharge_wh


def test_available_discharge_calculation():
    # soc=80%, min_soc=10%, capacity=13800 → available = 0.70 * 13800 = 9660 Wh
    inp = PredictInputs(soc_at_6pm=80.0, bom_temp_mean=20.0, provider="amber")
    result = predict(inp)
    assert abs(result.available_discharge_wh - 9660.0) < 1.0


def test_storm_mode_min_soc_reduces_export():
    # Raising min_soc from 10% to 20% should reduce available discharge by 1380 Wh
    base = dict(soc_at_6pm=80.0, bom_temp_mean=15.0, provider="amber",
                solcast_forecast_tomorrow_wh=25000, confidence=0.50)
    normal = predict(PredictInputs(min_soc=0.10, **base))
    storm = predict(PredictInputs(min_soc=0.20, **base))
    assert abs((normal.available_discharge_wh - storm.available_discharge_wh) - 1380.0) < 1.0


def test_lower_soc_means_less_export():
    base = dict(bom_temp_mean=15.0, provider="amber", solcast_forecast_tomorrow_wh=20000)
    high = predict(PredictInputs(soc_at_6pm=100.0, **base))
    low = predict(PredictInputs(soc_at_6pm=50.0, **base))
    assert high.safe_export_wh > low.safe_export_wh


def test_colder_night_means_less_export():
    # Use full battery + P50 confidence so there's headroom to distinguish
    base = dict(soc_at_6pm=100.0, provider="amber",
                solcast_forecast_tomorrow_wh=25000, confidence=0.50)
    warm = predict(PredictInputs(bom_temp_mean=16.0, **base))
    cold = predict(PredictInputs(bom_temp_mean=6.0, **base))
    assert warm.safe_export_wh > cold.safe_export_wh


def test_higher_confidence_means_less_or_equal_export():
    base = dict(soc_at_6pm=90.0, bom_temp_mean=12.0, provider="amber",
                solcast_forecast_tomorrow_wh=20000)
    p50 = predict(PredictInputs(confidence=0.50, **base))
    p90 = predict(PredictInputs(confidence=0.90, **base))
    p95 = predict(PredictInputs(confidence=0.95, **base))
    assert p50.safe_export_wh >= p90.safe_export_wh >= p95.safe_export_wh


def test_result_is_predictresult():
    inp = PredictInputs(soc_at_6pm=80, bom_temp_mean=15.0, provider="amber")
    assert isinstance(predict(inp), PredictResult)


def test_reasoning_non_empty():
    inp = PredictInputs(soc_at_6pm=80, bom_temp_mean=15.0, provider="amber")
    result = predict(inp)
    assert len(result.reasoning) > 20


# ---------------------------------------------------------------------------
# Fixture-based regression checks
# ---------------------------------------------------------------------------
# These assert that the model output for known historical nights stays within
# reasonable bounds and doesn't regress if coefficients are accidentally changed.


@pytest.mark.parametrize("date,expected_zone", [
    ("2026-02-07", "heating"),   # 17.2°C, mild-ish Feb night
    ("2026-03-20", "heating"),   # 17.0°C
    ("2025-07-17", "heating"),   # 10.7°C, cold winter night
])
def test_fixture_zone(date, expected_zone):
    result = predict(_inputs_from_fixture(date))
    assert result.zone == expected_zone


def test_fixture_jul17_cold_winter_no_export():
    # Jul 17: 58.7% SoC, 10.7°C — predicted consumption ~11.5 kWh,
    # available discharge only ~5.3 kWh → safe export should be 0
    result = predict(_inputs_from_fixture("2025-07-17"))
    assert result.safe_export_wh == 0.0


def test_fixture_feb07_full_battery_warm_has_export():
    # Feb 7: 100% SoC, 17.2°C — should have meaningful export headroom
    result = predict(_inputs_from_fixture("2026-02-07"))
    assert result.safe_export_wh > 2000.0


def test_fixture_consumption_estimate_in_range():
    # Jul 17 actual consumption: 12699 Wh (12.7 kWh).
    # Model should estimate something in the same ballpark (within 4 kWh P95 buffer).
    result = predict(_inputs_from_fixture("2025-07-17"))
    actual_kwh = FIXTURES["2025-07-17"]["consumption_wh"] / 1000.0
    assert abs(result.predicted_consumption_kwh - actual_kwh) < 4.0


def test_higher_min_soc_reduces_export():
    # Raising min_soc (e.g. storm mode 10% → 30%) should reduce safe export
    inp_default = _inputs_from_fixture("2026-02-07")
    inp_storm = _inputs_from_fixture("2026-02-07", min_soc=0.30)
    r_default = predict(inp_default)
    r_storm = predict(inp_storm)
    assert r_storm.safe_export_wh <= r_default.safe_export_wh


def test_storm_mode_min_soc_reduces_available_discharge():
    # min_soc 10% → 20% should reduce available discharge by exactly 1380 Wh (1% of 13800)
    base = dict(soc_at_6pm=80.0, bom_temp_mean=15.0, provider="amber",
                solcast_forecast_tomorrow_wh=25000, confidence=0.50)
    normal = predict(PredictInputs(min_soc=0.10, **base))
    storm = predict(PredictInputs(min_soc=0.20, **base))
    assert abs((normal.available_discharge_wh - storm.available_discharge_wh) - 1380.0) < 1.0
