"""Integration tests: extract against the real HA database, assert fixture rows.

Run with:  pytest
Requires:  data/home-assistant_v2.db to exist (gitignored dev database).
"""

import sqlite3
from pathlib import Path

import pytest

from src.extract import extract_all
from tests.fixtures import FIXTURES, REAL_TOL, WH_TOL

HA_DB = Path("data/home-assistant_v2.db")

REAL_COLS = {
    "soc_at_6pm",
    "min_soc_overnight",
    "max_soc_prev_daylight",
    "soc_at_11am",
    "min_outdoor_temp",
    "avg_indoor_temp",
    "bom_temp_min",
    "bom_temp_mean",
    "bom_temp_max",
    "bom_feels_like_min",
    "bom_rain_max",
    "bom_wind_mean",
    "bom_gust_max",
    "median_indoor_temp",
}
WH_COLS = {
    "solar_wh_before_11am",
    "consumption_wh",
    "consumption_wh_load",
    "grid_import_wh",
    "grid_export_wh",
    "battery_charged_wh",
    "battery_discharged_wh",
    "evening_grid_export_wh",
    "solcast_forecast_tomorrow_wh",
}
EXACT_COLS = {"provider", "absence_period", "guests", "curtailment_likely"}

# Columns that may be NULL for some fixtures (tested for NULL vs value, not numeric tolerance)
NULLABLE_REAL_COLS = {"bom_rain_max", "bom_wind_mean", "bom_gust_max", "bom_temp_min",
                      "bom_temp_mean", "bom_temp_max", "bom_feels_like_min", "median_indoor_temp"}
NULLABLE_WH_COLS = {"solcast_forecast_tomorrow_wh"}


@pytest.fixture(scope="session")
def dataset_db(tmp_path_factory, test_cfg):
    if not HA_DB.exists():
        pytest.skip(f"HA database not found at {HA_DB}")
    db_path = tmp_path_factory.mktemp("data") / "dataset.db"
    extract_all(ha_db=HA_DB, dataset_db=db_path, cfg=test_cfg)
    return db_path


@pytest.mark.parametrize("date_str,expected", list(FIXTURES.items()))
def test_fixture_row(dataset_db, date_str, expected):
    with sqlite3.connect(dataset_db) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM daily_observations WHERE date = ?", (date_str,)
        ).fetchone()

    assert row is not None, f"No row found for {date_str}"

    for col in EXACT_COLS:
        assert row[col] == expected[col], (
            f"{date_str} {col}: got {row[col]!r}, expected {expected[col]!r}"
        )

    for col in REAL_COLS:
        exp_val = expected[col]
        got_val = row[col]
        if col in NULLABLE_REAL_COLS and exp_val is None:
            assert got_val is None, f"{date_str} {col}: expected NULL, got {got_val}"
        else:
            assert abs(got_val - exp_val) <= REAL_TOL, (
                f"{date_str} {col}: got {got_val}, expected {exp_val} (tol ±{REAL_TOL})"
            )

    for col in WH_COLS:
        exp_val = expected[col]
        got_val = row[col]
        if col in NULLABLE_WH_COLS and exp_val is None:
            assert got_val is None, f"{date_str} {col}: expected NULL, got {got_val}"
        else:
            assert abs(got_val - exp_val) <= WH_TOL, (
                f"{date_str} {col}: got {got_val}, expected {exp_val} (tol ±{WH_TOL} Wh)"
            )
