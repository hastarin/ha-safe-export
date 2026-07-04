"""Tests for tools/backtest.py — pure economics functions (issue #8, audit T4).

Functions under test take an explicit `BacktestParams` rather than reading
module globals, so every test here builds its own params rather than relying
on the module defaults — this also makes the hand-computed expected values in
each test comment self-contained and independent of future default changes.
"""

from datetime import date

import pytest

import tools.backtest as bt

# ---------------------------------------------------------------------------
# season() / seasonal_confidence()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("month,expected", [
    (1, "summer"), (2, "summer"), (3, "summer"),
    (4, "shoulder"), (5, "shoulder"),
    (6, "winter"), (7, "winter"), (8, "winter"),
    (9, "shoulder"), (10, "shoulder"),
    (11, "summer"), (12, "summer"),
])
def test_season_all_months(month, expected):
    assert bt.season(date(2024, month, 15)) == expected


@pytest.mark.parametrize("month,expected", [
    (1, 0.75), (2, 0.75), (3, 0.75),
    (4, 0.90), (5, 0.90),
    (6, 0.95), (7, 0.95), (8, 0.95),
    (9, 0.90), (10, 0.90),
    (11, 0.75), (12, 0.75),
])
def test_seasonal_confidence_all_months(month, expected):
    assert bt.seasonal_confidence(date(2024, month, 15)) == expected


# ---------------------------------------------------------------------------
# one_year_before()
# ---------------------------------------------------------------------------


def test_one_year_before_normal_date():
    assert bt.one_year_before(date(2026, 7, 3)) == date(2025, 7, 3)


def test_one_year_before_leap_day_falls_back_to_feb_28():
    # 2024 is a leap year; 2023 is not, so replace(year=2023) on Feb 29 raises
    # ValueError and the fallback clamps to Feb 28.
    assert bt.one_year_before(date(2024, 2, 29)) == date(2023, 2, 28)


# ---------------------------------------------------------------------------
# adjusted_soc()
# ---------------------------------------------------------------------------


def test_adjusted_soc_full_charge_adjustment():
    # max_soc 90 + soc 80 -> 80 + (100-90) = 90
    row = {"soc_at_6pm": 80.0, "max_soc_prev_daylight": 90.0}
    assert bt.adjusted_soc(row) == 90.0


def test_adjusted_soc_caps_at_100():
    # max_soc 50 + soc 95 -> 95 + (100-50) = 145, capped to 100
    row = {"soc_at_6pm": 95.0, "max_soc_prev_daylight": 50.0}
    assert bt.adjusted_soc(row) == 100.0


def test_adjusted_soc_max_soc_none_passthrough():
    row = {"soc_at_6pm": 72.0, "max_soc_prev_daylight": None}
    assert bt.adjusted_soc(row) == 72.0


def test_adjusted_soc_max_soc_already_100_passthrough():
    row = {"soc_at_6pm": 65.0, "max_soc_prev_daylight": 100.0}
    assert bt.adjusted_soc(row) == 65.0


# ---------------------------------------------------------------------------
# baseline_trough_soc()
# ---------------------------------------------------------------------------


@pytest.fixture
def params_13800() -> "bt.BacktestParams":
    return bt.BacktestParams(battery_wh=13800.0)


def test_baseline_trough_soc_evening_export_add_back(params_13800):
    # soc_used == soc_at_6pm (no full-charge delta); evening export of 690 Wh
    # adds back 690/13800*100 = 5.0 points.
    row = {"soc_at_6pm": 80.0, "min_soc_overnight": 30.0, "evening_grid_export_wh": 690.0}
    assert bt.baseline_trough_soc(row, soc_used=80.0, params=params_13800) == pytest.approx(35.0)


def test_baseline_trough_soc_full_charge_delta_shift(params_13800):
    # soc_used 85 vs soc_at_6pm 70 -> delta 15 points shifts the trough up;
    # no evening export.
    row = {"soc_at_6pm": 70.0, "min_soc_overnight": 20.0, "evening_grid_export_wh": 0.0}
    assert bt.baseline_trough_soc(row, soc_used=85.0, params=params_13800) == pytest.approx(35.0)


def test_baseline_trough_soc_caps_at_100(params_13800):
    # 95 + 0 + 1380/13800*100 (=10) = 105, capped to 100.
    row = {"soc_at_6pm": 90.0, "min_soc_overnight": 95.0, "evening_grid_export_wh": 1380.0}
    assert bt.baseline_trough_soc(row, soc_used=90.0, params=params_13800) == pytest.approx(100.0)


def test_baseline_trough_soc_missing_evening_export_defaults_zero(params_13800):
    row = {"soc_at_6pm": 50.0, "min_soc_overnight": 40.0}
    assert bt.baseline_trough_soc(row, soc_used=50.0, params=params_13800) == pytest.approx(40.0)


# ---------------------------------------------------------------------------
# accum_night()
# ---------------------------------------------------------------------------
# All cases below pin BATTERY_WH=10000, HARD_FLOOR_FRAC=0.10 (hard=10%),
# SOFT_FLOOR_FRAC=0.20 (soft=20%), EXPORT_RATE=0.15, BUYBACK_RATE=0.28.


@pytest.fixture
def econ() -> "bt.BacktestParams":
    return bt.BacktestParams(
        battery_wh=10000.0,
        hard_floor_frac=0.10,
        soft_floor_margin=0.10,  # soft floor = 0.20
        export_rate=0.15,
        buyback_rate=0.28,
    )


def _run(export_wh: float, trough_soc: float, params: "bt.BacktestParams") -> dict:
    monthly: dict = {}
    bt.ensure_month(monthly, "2026-01")
    bt.accum_night(monthly, "2026-01", export_wh=export_wh, trough_soc=trough_soc, params=params)
    return monthly["2026-01"]


def test_accum_night_no_breach(econ):
    # trough 50%, export 2000 Wh -> sim_trough 50 - 2000/10000*100 = 30%, still
    # above the 10% hard floor. base_breach=0, sim_breach=0 -> shortfall 0.
    # perfect_export = (50-20)/100*10000 = 3000 Wh.
    # revenue = 2000/1000*0.15 = 0.30
    # opportunity = (3000-2000)/1000*0.15 = 0.15
    # perfect_net = 3000/1000*0.15 = 0.45
    m = _run(export_wh=2000.0, trough_soc=50.0, params=econ)
    assert m["revenue"] == pytest.approx(0.30)
    assert m["shortfall"] == pytest.approx(0.0)
    assert m["opportunity"] == pytest.approx(0.15)
    assert m["perfect_net"] == pytest.approx(0.45)
    assert m["nights"] == 1


def test_accum_night_export_caused_breach(econ):
    # trough 15% (above hard floor 10, so base_breach=0). Export 1000 Wh pushes
    # sim_trough to 15 - 1000/10000*100 = 5%, i.e. 5 points below the hard
    # floor -> sim_breach = 5/100*10000 = 500 Wh, all export-caused.
    # revenue = 1000/1000*0.15 = 0.15
    # shortfall = 500/1000*0.28 = 0.14
    # perfect_export = max(0, (15-20)/100*10000) = 0 (trough below soft floor)
    # opportunity = max(0, (0-1000)/1000)*0.15 = 0
    # perfect_net = 0
    m = _run(export_wh=1000.0, trough_soc=15.0, params=econ)
    assert m["revenue"] == pytest.approx(0.15)
    assert m["shortfall"] == pytest.approx(0.14)
    assert m["opportunity"] == pytest.approx(0.0)
    assert m["perfect_net"] == pytest.approx(0.0)


def test_accum_night_already_breached_baseline_zero_export(econ):
    # trough 8% (below hard floor 10, already breached with no export).
    # base_breach = (10-8)/100*10000 = 200. sim_breach with export_wh=0 is the
    # same 200 -> shortfall = max(0, 200-200) = 0, not blamed on export.
    m = _run(export_wh=0.0, trough_soc=8.0, params=econ)
    assert m["shortfall"] == pytest.approx(0.0)
    assert m["revenue"] == pytest.approx(0.0)
    assert m["perfect_net"] == pytest.approx(0.0)


def test_accum_night_already_breached_baseline_small_export_charges_extra_only(econ):
    # Same trough 8% (base_breach=200 Wh), now export 200 Wh ->
    # sim_trough = 8 - 200/10000*100 = 6%, sim_breach = (10-6)/100*10000 = 400.
    # shortfall = max(0, 400-200) = 200 Wh -- only the extra 2-point breach.
    # revenue = 200/1000*0.15 = 0.03
    # shortfall_cost = 200/1000*0.28 = 0.056
    m = _run(export_wh=200.0, trough_soc=8.0, params=econ)
    assert m["revenue"] == pytest.approx(0.03)
    assert m["shortfall"] == pytest.approx(0.056)
    assert m["perfect_net"] == pytest.approx(0.0)


def test_accum_night_perfect_export_drains_exactly_to_soft_floor(econ):
    # trough 50%, perfect_export = (50-20)/100*10000 = 3000 Wh. Exporting
    # exactly that much should leave sim_trough at the soft floor (20%) with
    # no shortfall.
    m = _run(export_wh=3000.0, trough_soc=50.0, params=econ)
    assert m["shortfall"] == pytest.approx(0.0)
    assert m["perfect_net"] == pytest.approx(0.45)  # 3000/1000*0.15


def test_accum_night_perfect_export_zero_when_trough_below_soft_floor(econ):
    # trough 15% is below the soft floor (20%), so there's no cushion left to
    # call "perfect" -> perfect_export = 0 regardless of export_wh.
    m = _run(export_wh=0.0, trough_soc=15.0, params=econ)
    assert m["perfect_net"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _capture()
# ---------------------------------------------------------------------------


def test_capture_undefined_when_no_opportunity():
    text, cls, sort = bt._capture(net=0.0, perfect_net=0.0)
    assert text == "—"
    assert cls == ""
    assert sort == float("-inf")


def test_capture_normal_ratio_eff_low():
    text, cls, sort = bt._capture(net=50.0, perfect_net=100.0)
    assert text == "50.0%"
    assert cls == "eff-low"
    assert sort == pytest.approx(50.0)


def test_capture_normal_ratio_eff_mid():
    text, cls, sort = bt._capture(net=60.0, perfect_net=100.0)
    assert text == "60.0%"
    assert cls == "eff-mid"
    assert sort == pytest.approx(60.0)


def test_capture_normal_ratio_eff_high():
    text, cls, sort = bt._capture(net=70.0, perfect_net=100.0)
    assert text == "70.0%"
    assert cls == "eff-high"
    assert sort == pytest.approx(70.0)


def test_capture_over_100_percent():
    text, cls, sort = bt._capture(net=150.0, perfect_net=100.0)
    assert text == "150.0%"
    assert cls == "eff-high"
    assert sort == pytest.approx(150.0)
