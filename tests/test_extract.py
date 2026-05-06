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
}
WH_COLS = {
    "solar_wh_before_11am",
    "consumption_wh",
    "consumption_wh_load",
    "grid_import_wh",
    "grid_export_wh",
    "battery_charged_wh",
    "battery_discharged_wh",
}
EXACT_COLS = {"provider", "hospital_period", "guests", "curtailment_likely"}


@pytest.fixture(scope="session")
def dataset_db(tmp_path_factory):
    if not HA_DB.exists():
        pytest.skip(f"HA database not found at {HA_DB}")
    db_path = tmp_path_factory.mktemp("data") / "dataset.db"
    extract_all(ha_db=HA_DB, dataset_db=db_path)
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
        assert abs(row[col] - expected[col]) <= REAL_TOL, (
            f"{date_str} {col}: got {row[col]}, expected {expected[col]} (tol ±{REAL_TOL})"
        )

    for col in WH_COLS:
        assert abs(row[col] - expected[col]) <= WH_TOL, (
            f"{date_str} {col}: got {row[col]}, expected {expected[col]} (tol ±{WH_TOL} Wh)"
        )
