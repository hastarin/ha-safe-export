"""Tests for tools/midday_usage.py against a synthetic HA DB."""

from datetime import date, datetime

import pytest

from src.extract import _get_metadata_ids
from tests.test_extract_synthetic import (
    BATTERY_CHARGED,
    BATTERY_DISCHARGED,
    GRID_EXPORT,
    GRID_IMPORT,
    LOAD,
    PV,
    TZ,
    insert_stat,
    make_cfg,
    make_ha_db,
    register,
)
from tools.midday_usage import quantile, season_of, window_usage

DAY = date(2026, 7, 15)

# Per-hour deltas for the cumulative sensors across the 11:00-14:00 window.
HOURLY = {GRID_IMPORT: 3000, GRID_EXPORT: 100, BATTERY_CHARGED: 3500, BATTERY_DISCHARGED: 50}
PV_W, LOAD_W = 2000.0, -1600.0  # load is stored negative (gotcha #2)


def _ts(hour: int) -> int:
    return int(datetime(DAY.year, DAY.month, DAY.day, hour, tzinfo=TZ).timestamp())


@pytest.fixture
def midday_db(tmp_path):
    cfg = make_cfg()
    conn = make_ha_db(tmp_path / "ha.db")
    mids = {sid: register(conn, sid) for sid in cfg.sensor_ids.values() if sid}
    for sid, per_hour in HOURLY.items():
        for i, hour in enumerate(range(10, 14)):  # sum@10 .. sum@13
            insert_stat(conn, mids[sid], _ts(hour), sum=1_000_000 + i * per_hour)
    for hour in (11, 12, 13):
        insert_stat(conn, mids[PV], _ts(hour), mean=PV_W)
        insert_stat(conn, mids[LOAD], _ts(hour), mean=LOAD_W)
    conn.commit()
    return conn, cfg, mids


def test_window_usage_energy_balance(midday_db):
    conn, cfg, _ = midday_db
    u = window_usage(conn, _get_metadata_ids(conn, cfg), DAY, cfg, 11, 14)
    assert u is not None
    # 3 h x (2000 pv + 3000 imp + 50 dis - 100 exp - 3500 chg)
    assert u.consumption_wh == 3 * (2000 + 3000 + 50 - 100 - 3500)
    assert u.load_wh == 4800
    assert u.pv_wh == 6000
    assert u.grid_import_wh == 9000


def test_window_usage_missing_boundary_bucket_is_gap(midday_db):
    conn, cfg, mids = midday_db
    conn.execute(
        "DELETE FROM statistics WHERE metadata_id = ? AND start_ts = ?",
        (mids[GRID_IMPORT], _ts(10)),
    )
    assert window_usage(conn, _get_metadata_ids(conn, cfg), DAY, cfg, 11, 14) is None


def test_window_usage_missing_pv_hour_is_gap(midday_db):
    conn, cfg, mids = midday_db
    conn.execute(
        "DELETE FROM statistics WHERE metadata_id = ? AND start_ts = ?", (mids[PV], _ts(12))
    )
    assert window_usage(conn, _get_metadata_ids(conn, cfg), DAY, cfg, 11, 14) is None


@pytest.mark.parametrize(
    ("d", "expected"),
    [
        (date(2025, 12, 1), ("Summer", "Summer 2025-26")),
        (date(2026, 2, 28), ("Summer", "Summer 2025-26")),
        (date(2026, 3, 1), ("Autumn", "Autumn 2026")),
        (date(2026, 7, 15), ("Winter", "Winter 2026")),
        (date(2026, 11, 30), ("Spring", "Spring 2026")),
        (date(1999, 12, 31), ("Summer", "Summer 1999-00")),
    ],
)
def test_season_of(d, expected):
    assert season_of(d) == expected


def test_quantile_matches_linear_interpolation():
    xs = [1.0, 2.0, 3.0, 4.0]
    assert quantile(xs, 0.5) == 2.5
    assert quantile(xs, 0.75) == 3.25
    assert quantile([5.0], 0.9) == 5.0
