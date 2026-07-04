"""Unit tests for extraction primitives against a synthetic HA database fixture.

Complements the golden-fixture integration tests in test_extract.py (which require
the owner's personal HA database and therefore skip in CI/forks) with a portable,
hand-computable fixture: a tmp_path SQLite database shaped like the two HA recorder
tables extraction reads (`statistics_meta`, `statistics`), seeded with a synthetic
sensor set and values chosen so every derived column can be checked independently
of the SQL under test.

Sensor names here are deliberately distinct from the personal `test_cfg` fixture in
conftest.py, so nobody mistakes this for real installation data.
"""

import logging
import sqlite3
from dataclasses import fields
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.config import Config, ModelConfig, ProviderPeriod, SensorConfig
from src.extract import (
    _cum_delta,
    _forecast_at_6pm,
    _get_metadata_ids,
    extract_all,
    extract_row,
)
from src.windows import windows_for_date

TZ = ZoneInfo("Australia/Melbourne")
HOUR = 3600

BATTERY_SOC = "sensor.synth_battery_soc"
PV = "sensor.synth_pv"
LOAD = "sensor.synth_load"
GRID_IMPORT = "sensor.synth_grid_import"
GRID_EXPORT = "sensor.synth_grid_export"
BATTERY_CHARGED = "sensor.synth_battery_charged"
BATTERY_DISCHARGED = "sensor.synth_battery_discharged"
OUTDOOR_TEMP = "sensor.synth_outdoor_temp"
INDOOR_TEMP = "sensor.synth_indoor_temp"
WEATHER_TEMP = "sensor.synth_weather_temp"
WEATHER_FEELS_LIKE = "sensor.synth_weather_feels_like"
WEATHER_RAIN = "sensor.synth_weather_rain"
WEATHER_WIND = "sensor.synth_weather_wind"
WEATHER_GUST = "sensor.synth_weather_gust"
WEATHER_HUMIDITY = "sensor.synth_weather_humidity"
FORECAST_TEMP = "sensor.synth_forecast_temp"

REQUIRED_SENSOR_IDS = {
    "battery_soc": BATTERY_SOC,
    "pv": PV,
    "load": LOAD,
    "grid_import": GRID_IMPORT,
    "grid_export": GRID_EXPORT,
    "battery_charged": BATTERY_CHARGED,
    "battery_discharged": BATTERY_DISCHARGED,
    "outdoor_temp": OUTDOOR_TEMP,
    "indoor_temp": INDOOR_TEMP,
    "weather_temp": WEATHER_TEMP,
    "weather_feels_like": WEATHER_FEELS_LIKE,
    "weather_rain": WEATHER_RAIN,
    "weather_wind": WEATHER_WIND,
    "weather_gust": WEATHER_GUST,
    "weather_humidity": WEATHER_HUMIDITY,
}


def _zero_model() -> ModelConfig:
    """A ModelConfig with all-zero coefficients — extract.py never reads cfg.model."""
    return ModelConfig(**{f.name: 0.0 for f in fields(ModelConfig)})


def make_cfg(**sensor_overrides: str | None) -> Config:
    """Build a minimal synthetic Config. Optional sensors default to unconfigured (None)."""
    sensors = SensorConfig(
        battery_soc=BATTERY_SOC,
        pv=PV,
        load=LOAD,
        grid_import=GRID_IMPORT,
        grid_export=GRID_EXPORT,
        battery_charged=BATTERY_CHARGED,
        battery_discharged=BATTERY_DISCHARGED,
        outdoor_temp=OUTDOOR_TEMP,
        indoor_temp=INDOOR_TEMP,
        weather_temp=WEATHER_TEMP,
        weather_feels_like=WEATHER_FEELS_LIKE,
        weather_rain=WEATHER_RAIN,
        weather_wind=WEATHER_WIND,
        weather_gust=WEATHER_GUST,
        weather_humidity=WEATHER_HUMIDITY,
        **sensor_overrides,
    )
    return Config(
        battery_capacity_wh=5000.0,
        battery_reserve_fraction=0.1,
        timezone=TZ,
        sensors=sensors,
        providers=[ProviderPeriod(name="synthtest", start_date=date(2020, 1, 1))],
        model=_zero_model(),
    )


def make_ha_db(path: Path) -> sqlite3.Connection:
    """Create a tmp SQLite DB shaped like the HA recorder tables extraction reads."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE statistics_meta (
            id INTEGER PRIMARY KEY,
            statistic_id TEXT NOT NULL
        );
        CREATE TABLE statistics (
            id INTEGER PRIMARY KEY,
            metadata_id INTEGER NOT NULL,
            start_ts REAL NOT NULL,
            state REAL,
            mean REAL,
            min REAL,
            max REAL,
            sum REAL
        );
        """
    )
    return conn


def register(conn: sqlite3.Connection, statistic_id: str) -> int:
    cur = conn.execute(
        "INSERT INTO statistics_meta (statistic_id) VALUES (?)", (statistic_id,)
    )
    return cur.lastrowid


def override_stat(conn: sqlite3.Connection, mid: int, ts: int, **cols: float | None) -> None:
    """Replace any existing row at (mid, ts) — for overriding a baseline bucket."""
    conn.execute("DELETE FROM statistics WHERE metadata_id = ? AND start_ts = ?", (mid, ts))
    insert_stat(conn, mid, ts, **cols)


def insert_stat(
    conn: sqlite3.Connection,
    mid: int,
    ts: int,
    *,
    state: float | None = None,
    mean: float | None = None,
    min: float | None = None,  # noqa: A002 - matches HA column name
    max: float | None = None,  # noqa: A002 - matches HA column name
    sum: float | None = None,  # noqa: A002 - matches HA column name
) -> None:
    conn.execute(
        "INSERT INTO statistics (metadata_id, start_ts, state, mean, min, max, sum) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (mid, ts, state, mean, min, max, sum),
    )


# ---------------------------------------------------------------------------
# _cum_delta
# ---------------------------------------------------------------------------


def test_cum_delta_normal(tmp_path):
    conn = make_ha_db(tmp_path / "ha.db")
    mid = register(conn, "sensor.synth_meter")
    insert_stat(conn, mid, 1000, sum=100.0)
    insert_stat(conn, mid, 1100, sum=250.0)
    conn.commit()

    assert _cum_delta(conn, mid, 1000, 1100) == 150


def test_cum_delta_missing_start(tmp_path):
    conn = make_ha_db(tmp_path / "ha.db")
    mid = register(conn, "sensor.synth_meter")
    insert_stat(conn, mid, 1100, sum=250.0)
    conn.commit()

    assert _cum_delta(conn, mid, 1000, 1100) is None


def test_cum_delta_missing_end(tmp_path):
    conn = make_ha_db(tmp_path / "ha.db")
    mid = register(conn, "sensor.synth_meter")
    insert_stat(conn, mid, 1000, sum=100.0)
    conn.commit()

    assert _cum_delta(conn, mid, 1000, 1100) is None


# ---------------------------------------------------------------------------
# _forecast_at_6pm
# ---------------------------------------------------------------------------


def test_forecast_at_6pm_exact_hit(tmp_path):
    conn = make_ha_db(tmp_path / "ha.db")
    mid = register(conn, FORECAST_TEMP)
    w = windows_for_date(date(2024, 6, 15), TZ)
    insert_stat(conn, mid, w.ts_18_prior, mean=12.34)
    conn.commit()

    assert _forecast_at_6pm(conn, mid, w.ts_18_prior) == 12.3


def test_forecast_at_6pm_fallback_within_window(tmp_path):
    conn = make_ha_db(tmp_path / "ha.db")
    mid = register(conn, FORECAST_TEMP)
    w = windows_for_date(date(2024, 6, 15), TZ)
    # No exact 18:00 bucket; nearest prior bucket is 2h earlier (within the 3h window).
    insert_stat(conn, mid, w.ts_18_prior - 2 * HOUR, mean=11.1)
    conn.commit()

    assert _forecast_at_6pm(conn, mid, w.ts_18_prior) == 11.1


def test_forecast_at_6pm_stale_beyond_window(tmp_path):
    conn = make_ha_db(tmp_path / "ha.db")
    mid = register(conn, FORECAST_TEMP)
    w = windows_for_date(date(2024, 6, 15), TZ)
    # Nearest bucket is 4h earlier — older than the 3h fallback window.
    insert_stat(conn, mid, w.ts_18_prior - 4 * HOUR, mean=9.9)
    conn.commit()

    assert _forecast_at_6pm(conn, mid, w.ts_18_prior) is None


def test_forecast_at_6pm_sensor_absent(tmp_path):
    conn = make_ha_db(tmp_path / "ha.db")
    w = windows_for_date(date(2024, 6, 15), TZ)
    assert _forecast_at_6pm(conn, None, w.ts_18_prior) is None


# ---------------------------------------------------------------------------
# _get_metadata_ids
# ---------------------------------------------------------------------------


def test_get_metadata_ids_required_sensor_missing(tmp_path):
    conn = make_ha_db(tmp_path / "ha.db")
    for key, statistic_id in REQUIRED_SENSOR_IDS.items():
        if key == "weather_gust":
            continue  # deliberately not registered
        register(conn, statistic_id)
    conn.commit()

    cfg = make_cfg()
    with pytest.raises(ValueError, match=WEATHER_GUST):
        _get_metadata_ids(conn, cfg)


def test_get_metadata_ids_optional_sensor_unconfigured(tmp_path):
    conn = make_ha_db(tmp_path / "ha.db")
    for statistic_id in REQUIRED_SENSOR_IDS.values():
        register(conn, statistic_id)
    conn.commit()

    cfg = make_cfg()  # solcast, guests, median_temp, etc. all left unconfigured (None)
    ids = _get_metadata_ids(conn, cfg)
    for key in ("solcast", "guests", "median_temp", "median_humidity", "forecast_temp",
                "forecast_humidity"):
        assert ids[key] is None


def test_get_metadata_ids_optional_sensor_configured_but_absent(tmp_path):
    conn = make_ha_db(tmp_path / "ha.db")
    for statistic_id in REQUIRED_SENSOR_IDS.values():
        register(conn, statistic_id)
    conn.commit()

    # Configured in cfg, but no matching statistics_meta row in the HA db.
    cfg = make_cfg(solcast="sensor.synth_solcast_not_in_db")
    ids = _get_metadata_ids(conn, cfg)
    assert ids["solcast"] is None


# ---------------------------------------------------------------------------
# extract_row — a complete, hand-computable synthetic day
# ---------------------------------------------------------------------------


@pytest.fixture
def full_day(tmp_path):
    """A fully-seeded synthetic day plus the independently-computed expected values."""
    conn = make_ha_db(tmp_path / "ha.db")
    morning_date = date(2024, 6, 15)  # AEST (no DST) both sides of the window
    w = windows_for_date(morning_date, TZ)

    ids = {}
    for key, statistic_id in REQUIRED_SENSOR_IDS.items():
        ids[key] = register(conn, statistic_id)
    forecast_mid = register(conn, FORECAST_TEMP)

    extreme_ts = w.ts_20_prior + 6 * HOUR  # 02:00 local, inside the overnight window

    # battery_soc: baseline 60% everywhere, with named extremes/boundaries overridden.
    for ts in range(w.ts_06_prior, w.ts_10_today + 1, HOUR):
        insert_stat(conn, ids["battery_soc"], ts, mean=60.0, min=60.0, max=60.0)
    override_stat(conn, ids["battery_soc"], w.ts_12_prior, mean=60.0, min=60.0, max=95.0)
    override_stat(conn, ids["battery_soc"], w.ts_17_prior, mean=80.0, min=60.0, max=60.0)
    override_stat(conn, ids["battery_soc"], extreme_ts, mean=60.0, min=20.0, max=60.0)
    override_stat(conn, ids["battery_soc"], w.ts_10_today, mean=40.0, min=60.0, max=60.0)

    # pv: zero overnight, 1000W for the 5 sunrise-to-11am buckets (06:00-10:00).
    for ts in range(w.ts_18_prior, w.ts_10_today + 1, HOUR):
        insert_stat(conn, ids["pv"], ts, mean=0.0)
    sun_start = w.ts_10_today - 4 * HOUR
    for ts in range(sun_start, w.ts_10_today + 1, HOUR):
        override_stat(conn, ids["pv"], ts, mean=1000.0)

    # load (consumption, sign-flipped): constant -300W across the window.
    for ts in range(w.ts_18_prior, w.ts_10_today + 1, HOUR):
        insert_stat(conn, ids["load"], ts, mean=-300.0)

    # Cumulative meters: only the exact endpoints _cum_delta reads are needed.
    insert_stat(conn, ids["grid_import"], w.ts_17_prior, sum=1000.0)
    insert_stat(conn, ids["grid_import"], w.ts_10_today, sum=1170.0)  # +170 over 17h
    insert_stat(conn, ids["grid_export"], w.ts_17_prior, sum=500.0)
    insert_stat(conn, ids["grid_export"], w.ts_20_prior, sum=515.0)   # +15 over 3h
    insert_stat(conn, ids["grid_export"], w.ts_10_today, sum=585.0)   # +85 over 17h
    insert_stat(conn, ids["battery_charged"], w.ts_17_prior, sum=2000.0)
    insert_stat(conn, ids["battery_charged"], w.ts_10_today, sum=2136.0)  # +136
    insert_stat(conn, ids["battery_discharged"], w.ts_17_prior, sum=3000.0)
    insert_stat(conn, ids["battery_discharged"], w.ts_10_today, sum=3204.0)  # +204

    # outdoor_temp: baseline min 10.0, one cold snap at extreme_ts.
    for ts in range(w.ts_18_prior, w.ts_10_today + 1, HOUR):
        insert_stat(conn, ids["outdoor_temp"], ts, min=10.0)
    insert_stat(conn, ids["outdoor_temp"], extreme_ts, min=5.0)

    # indoor_temp: constant mean, so AVG is trivially the same constant.
    for ts in range(w.ts_18_prior, w.ts_10_today + 1, HOUR):
        insert_stat(conn, ids["indoor_temp"], ts, mean=21.0)

    # weather_temp (BOM): covers the afternoon window too (12:00-17:00 prior day).
    for ts in range(w.ts_12_prior, w.ts_10_today + 1, HOUR):
        insert_stat(conn, ids["weather_temp"], ts, min=8.0, mean=10.0, max=12.0)
    insert_stat(conn, ids["weather_temp"], extreme_ts, min=6.0, mean=10.0, max=14.0)

    for ts in range(w.ts_18_prior, w.ts_10_today + 1, HOUR):
        insert_stat(conn, ids["weather_feels_like"], ts, min=5.0)
    insert_stat(conn, ids["weather_feels_like"], extreme_ts, min=2.0)

    for ts in range(w.ts_18_prior, w.ts_10_today + 1, HOUR):
        insert_stat(conn, ids["weather_wind"], ts, mean=15.0)

    for ts in range(w.ts_18_prior, w.ts_10_today + 1, HOUR):
        insert_stat(conn, ids["weather_gust"], ts, max=25.0)
    insert_stat(conn, ids["weather_gust"], extreme_ts, max=40.0)

    for ts in range(w.ts_18_prior, w.ts_10_today + 1, HOUR):
        insert_stat(conn, ids["weather_humidity"], ts, mean=70.0, max=75.0)
    insert_stat(conn, ids["weather_humidity"], extreme_ts, mean=70.0, max=90.0)

    for ts in range(w.ts_18_prior, w.ts_10_today + 1, HOUR):
        insert_stat(conn, ids["weather_rain"], ts, state=0.0)
    insert_stat(conn, ids["weather_rain"], extreme_ts, state=4.5)

    insert_stat(conn, forecast_mid, w.ts_18_prior, mean=9.5)

    conn.commit()

    ids["forecast_temp"] = forecast_mid
    ids["forecast_humidity"] = None
    ids["solcast"] = None
    ids["guests"] = None
    ids["median_temp"] = None
    ids["median_humidity"] = None

    cfg = make_cfg(forecast_temp=FORECAST_TEMP)

    expected = {
        "provider": "synthtest",
        "guests": None,
        "absence_period": 0,
        "data_gap": 0,
        "soc_at_6pm": 80.0,
        "min_soc_overnight": 20.0,
        "max_soc_prev_daylight": 95.0,
        "soc_at_11am": 40.0,
        "min_outdoor_temp": 5.0,
        "avg_indoor_temp": 21.0,
        "bom_temp_min": 6.0,
        "bom_temp_mean": 10.0,
        "bom_temp_max": 14.0,
        "bom_temp_afternoon_max": 12.0,
        "bom_feels_like_min": 2.0,
        "bom_rain_max": 4.5,
        "bom_wind_mean": 15.0,
        "bom_gust_max": 40.0,
        "bom_humidity_mean": 70.0,
        "bom_humidity_max": 90.0,
        "solcast_forecast_tomorrow_wh": None,
        "median_indoor_temp": None,
        "median_indoor_humidity": None,
        "forecast_temp_mean": 9.5,
        "forecast_humidity_mean": None,
        "solar_wh_before_11am": 5000,
        "consumption_wh_load": 5100,
        "grid_import_wh": 170,
        "grid_export_wh": 85,
        "battery_charged_wh": 136,
        "battery_discharged_wh": 204,
        "evening_grid_export_wh": 15,
        "consumption_wh": 5000 + 170 + 204 - 85 - 136,
        "curtailment_likely": 0,
    }

    return conn, ids, cfg, morning_date, expected


def test_extract_row_complete_day(full_day):
    conn, ids, cfg, morning_date, expected = full_day
    row = extract_row(conn, ids, morning_date, cfg)

    assert row is not None
    for key, value in expected.items():
        assert row[key] == value, f"{key}: got {row[key]!r}, expected {value!r}"
    assert row["date"] == morning_date.isoformat()
    assert row["extraction_version"]
    assert row["_large_imbalance"] is None  # imbalance (50 Wh) is well under the threshold


def test_extract_row_missing_required_endpoint_skips(full_day):
    conn, ids, cfg, morning_date, _expected = full_day
    # Remove the cumulative endpoint _cum_delta needs for grid_import_wh.
    conn.execute(
        "DELETE FROM statistics WHERE metadata_id = ? AND start_ts = ?",
        (ids["grid_import"], windows_for_date(morning_date, TZ).ts_10_today),
    )
    conn.commit()

    assert extract_row(conn, ids, morning_date, cfg) is None


# ---------------------------------------------------------------------------
# Gap-warning heuristic (extract_all): near-zero battery throughput + SOC swing
# ---------------------------------------------------------------------------


def _seed_minimal_day(conn: sqlite3.Connection, ids: dict, morning_date: date) -> None:
    """Seed just enough statistics for extract_row to produce a non-skipped row.

    Values are arbitrary (not hand-computed for a specific expected result) — these
    tests care about *which dates* end up in daily_observations, not the column
    values, so nothing here needs to match a fixture.
    """
    w = windows_for_date(morning_date, TZ)
    insert_stat(conn, ids["battery_soc"], w.ts_17_prior, mean=80.0)
    insert_stat(conn, ids["battery_soc"], w.ts_10_today, mean=60.0)
    insert_stat(conn, ids["pv"], w.ts_18_prior, mean=0.0)
    for ts in range(w.ts_18_prior, w.ts_10_today + 1, HOUR):
        insert_stat(conn, ids["load"], ts, mean=-300.0)
    insert_stat(conn, ids["grid_import"], w.ts_17_prior, sum=0.0)
    insert_stat(conn, ids["grid_import"], w.ts_10_today, sum=100.0)
    insert_stat(conn, ids["grid_export"], w.ts_17_prior, sum=0.0)
    insert_stat(conn, ids["grid_export"], w.ts_20_prior, sum=0.0)
    insert_stat(conn, ids["grid_export"], w.ts_10_today, sum=0.0)
    insert_stat(conn, ids["battery_charged"], w.ts_17_prior, sum=0.0)
    insert_stat(conn, ids["battery_charged"], w.ts_10_today, sum=0.0)
    insert_stat(conn, ids["battery_discharged"], w.ts_17_prior, sum=0.0)
    insert_stat(conn, ids["battery_discharged"], w.ts_10_today, sum=0.0)


# ---------------------------------------------------------------------------
# extract_all — incremental resume (MAX(date)+1) and --rebuild (T0.2 safety net
# for the rebuild-atomicity change; pins current behaviour before it changes)
# ---------------------------------------------------------------------------


def test_extract_all_resumes_from_max_date_plus_one(tmp_path):
    # extract_all's incremental upper bound is always the real "yesterday", so
    # day2 is anchored there: the first call (from_date=day1) extracts exactly
    # {day1, day2}, then the second (from_date-less) call has nothing new to
    # find — proving it resumed from MAX(date)+1=day3 (beyond the upper bound)
    # rather than re-extracting day1/day2 or jumping back to FIRST_DATE.
    day2 = datetime.now(TZ).date() - timedelta(days=1)
    day1 = day2 - timedelta(days=1)

    ha_path = tmp_path / "ha.db"
    conn = make_ha_db(ha_path)
    ids = {key: register(conn, statistic_id) for key, statistic_id in REQUIRED_SENSOR_IDS.items()}
    _seed_minimal_day(conn, ids, day1)
    _seed_minimal_day(conn, ids, day2)
    conn.commit()
    conn.close()

    cfg = make_cfg()
    dataset_db = tmp_path / "dataset.db"

    # First run: from_date=day1, upper bound=yesterday=day2 -> extracts both.
    extract_all(ha_db=ha_path, dataset_db=dataset_db, cfg=cfg, from_date=day1)

    with sqlite3.connect(dataset_db) as ds:
        dates = {r[0] for r in ds.execute("SELECT date FROM daily_observations")}
    assert dates == {day1.isoformat(), day2.isoformat()}

    # Corrupt day1's row so a spurious re-extraction (e.g. a resume bug that
    # jumps back to FIRST_DATE) would be caught by the assertion below.
    with sqlite3.connect(dataset_db) as ds:
        ds.execute(
            "UPDATE daily_observations SET consumption_wh = -999999 WHERE date = ?",
            (day1.isoformat(),),
        )
        ds.commit()

    # Second run: no from_date/rebuild -> must resume from MAX(date)+1 = day3,
    # which is beyond the incremental upper bound (day2) -> no-op.
    extract_all(ha_db=ha_path, dataset_db=dataset_db, cfg=cfg)

    with sqlite3.connect(dataset_db) as ds:
        ds.row_factory = sqlite3.Row
        rows = {
            r["date"]: r["consumption_wh"]
            for r in ds.execute("SELECT date, consumption_wh FROM daily_observations")
        }
    assert set(rows) == {day1.isoformat(), day2.isoformat()}
    assert rows[day1.isoformat()] == -999999  # untouched, not re-extracted


def test_extract_all_rebuild_drops_and_reextracts(tmp_path):
    day1 = date(2024, 6, 10)
    day2 = date(2024, 6, 11)

    ha_path = tmp_path / "ha.db"
    conn = make_ha_db(ha_path)
    ids = {key: register(conn, statistic_id) for key, statistic_id in REQUIRED_SENSOR_IDS.items()}
    for d in (day1, day2):
        _seed_minimal_day(conn, ids, d)
    conn.commit()
    conn.close()

    cfg = make_cfg()
    dataset_db = tmp_path / "dataset.db"

    extract_all(ha_db=ha_path, dataset_db=dataset_db, cfg=cfg, from_date=day1)
    with sqlite3.connect(dataset_db) as ds:
        before = {r[0] for r in ds.execute("SELECT date FROM daily_observations")}
    assert before == {day1.isoformat(), day2.isoformat()}

    # Manually corrupt a row so we can prove --rebuild actually re-extracts it
    # rather than leaving the old (stale) value in place.
    with sqlite3.connect(dataset_db) as ds:
        ds.execute(
            "UPDATE daily_observations SET consumption_wh = -999999 WHERE date = ?",
            (day1.isoformat(),),
        )
        ds.commit()

    # --rebuild with an explicit from_date so the test doesn't walk all the way
    # back to FIRST_DATE (2023-11-28) — from_date takes priority over rebuild's
    # FIRST_DATE default (see extract_all's start-date branching).
    extract_all(ha_db=ha_path, dataset_db=dataset_db, cfg=cfg, rebuild=True, from_date=day1)

    with sqlite3.connect(dataset_db) as ds:
        ds.row_factory = sqlite3.Row
        rows = {
            r["date"]: r["consumption_wh"]
            for r in ds.execute("SELECT date, consumption_wh FROM daily_observations")
        }
    assert set(rows) == {day1.isoformat(), day2.isoformat()}
    assert rows[day1.isoformat()] != -999999  # re-extracted, not left stale


def test_extract_all_up_to_date_returns_without_error(tmp_path):
    """The 'nothing to extract' early-return path (start > yesterday) must not raise
    and must leave any existing rows untouched — this is the path T2.3's connection
    cleanup refactor changed from an explicit ha.close()/ds.close() to try/finally.
    """
    yesterday = datetime.now(TZ).date() - timedelta(days=1)

    ha_path = tmp_path / "ha.db"
    conn = make_ha_db(ha_path)
    ids = {key: register(conn, statistic_id) for key, statistic_id in REQUIRED_SENSOR_IDS.items()}
    _seed_minimal_day(conn, ids, yesterday)
    conn.commit()
    conn.close()

    cfg = make_cfg()
    dataset_db = tmp_path / "dataset.db"

    extract_all(ha_db=ha_path, dataset_db=dataset_db, cfg=cfg, from_date=yesterday)
    with sqlite3.connect(dataset_db) as ds:
        before = {r[0] for r in ds.execute("SELECT date FROM daily_observations")}
    assert before == {yesterday.isoformat()}

    # Running again with no from_date: MAX(date)+1 = today, which is > yesterday
    # (the incremental upper bound), so this should be a no-op, not an error.
    extract_all(ha_db=ha_path, dataset_db=dataset_db, cfg=cfg)

    with sqlite3.connect(dataset_db) as ds:
        after = {r[0] for r in ds.execute("SELECT date FROM daily_observations")}
    assert after == before


def test_gap_warning_triggers_on_imbalanced_low_battery_day(tmp_path, caplog):
    # Use "yesterday" so extract_all's single-day incremental run only processes
    # this one synthetic day, regardless of when the test suite happens to run.
    morning_date = datetime.now(TZ).date() - timedelta(days=1)
    w = windows_for_date(morning_date, TZ)

    ha_path = tmp_path / "ha.db"
    conn = make_ha_db(ha_path)
    ids = {key: register(conn, statistic_id) for key, statistic_id in REQUIRED_SENSOR_IDS.items()}

    insert_stat(conn, ids["battery_soc"], w.ts_17_prior, mean=80.0)
    insert_stat(conn, ids["battery_soc"], w.ts_10_today, mean=60.0)  # 20% swing

    insert_stat(conn, ids["pv"], w.ts_18_prior, mean=0.0)  # solar_wh = 0 (non-NULL)

    for ts in range(w.ts_18_prior, w.ts_10_today + 1, HOUR):
        insert_stat(conn, ids["load"], ts, mean=-1000.0)  # consumption_wh_load = 17000

    insert_stat(conn, ids["grid_import"], w.ts_17_prior, sum=0.0)
    insert_stat(conn, ids["grid_import"], w.ts_10_today, sum=100.0)
    insert_stat(conn, ids["grid_export"], w.ts_17_prior, sum=0.0)
    insert_stat(conn, ids["grid_export"], w.ts_20_prior, sum=0.0)
    insert_stat(conn, ids["grid_export"], w.ts_10_today, sum=0.0)
    insert_stat(conn, ids["battery_charged"], w.ts_17_prior, sum=0.0)
    insert_stat(conn, ids["battery_charged"], w.ts_10_today, sum=50.0)  # under 500W threshold
    insert_stat(conn, ids["battery_discharged"], w.ts_17_prior, sum=0.0)
    insert_stat(conn, ids["battery_discharged"], w.ts_10_today, sum=60.0)  # under threshold
    conn.commit()
    conn.close()

    cfg = make_cfg()
    dataset_db = tmp_path / "dataset.db"

    with caplog.at_level(logging.WARNING, logger="src.extract"):
        extract_all(ha_db=ha_path, dataset_db=dataset_db, cfg=cfg, from_date=morning_date)

    assert any("Possible data gap" in r.message for r in caplog.records)
    assert any("near-zero battery throughput" in r.message for r in caplog.records)

    with sqlite3.connect(dataset_db) as ds:
        ds.row_factory = sqlite3.Row
        row = ds.execute(
            "SELECT * FROM daily_observations WHERE date = ?", (morning_date.isoformat(),)
        ).fetchone()
    assert row is not None
    assert row["consumption_wh"] == 110
    assert row["consumption_wh_load"] == 17000
