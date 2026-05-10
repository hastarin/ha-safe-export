"""Extract daily observations from the Home Assistant database."""

import argparse
import logging
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from src import __version__
from src.config import Config, load_config
from src.windows import windows_for_date

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

FIRST_DATE = date(2023, 11, 28)

_OPTIONAL_SENSORS = {"guests", "solcast", "median_temp", "median_humidity"}

# Thresholds for data-gap warning heuristics
_IMBALANCE_WARN_THRESHOLD = 3000
_BATTERY_ZERO_THRESHOLD = 500   # Wh — below this is suspicious if SOC swung significantly
_SOC_SWING_THRESHOLD = 10.0     # % — SOC change that should have registered battery throughput


def _get_metadata_ids(ha: sqlite3.Connection, cfg: Config) -> dict[str, int | None]:
    """Look up metadata IDs for all sensors. Optional sensors return None if absent."""
    ids: dict[str, int | None] = {}
    for key, sensor_id in cfg.sensor_ids.items():
        if sensor_id is None:
            ids[key] = None
            continue
        row = ha.execute(
            "SELECT id FROM statistics_meta WHERE statistic_id = ?", (sensor_id,)
        ).fetchone()
        if row is None:
            if key in _OPTIONAL_SENSORS:
                ids[key] = None
            else:
                raise ValueError(f"Required sensor not found in HA database: {sensor_id}")
        else:
            ids[key] = row[0]
    return ids


def _cum_delta(
    ha: sqlite3.Connection, mid: int, ts_start: int, ts_end: int
) -> int | None:
    """Return cumulative-sum delta between two exact bucket timestamps.

    Returns None if either endpoint is missing (data quality issue).
    """
    rows = ha.execute(
        "SELECT start_ts, sum FROM statistics WHERE metadata_id = ? AND start_ts IN (?, ?)",
        (mid, ts_start, ts_end),
    ).fetchall()
    by_ts = {r[0]: r[1] for r in rows}
    start_val = by_ts.get(ts_start)
    end_val = by_ts.get(ts_end)
    if start_val is None or end_val is None:
        return None
    return round(end_val - start_val)


def extract_row(
    ha: sqlite3.Connection,
    ids: dict[str, int | None],
    morning_date: date,
    cfg: Config,
) -> dict | None:
    """Return a dict of column values for one morning_date, or None to skip."""
    w = windows_for_date(morning_date, cfg.timezone)
    date_str = morning_date.isoformat()

    def soc_mean(ts: int) -> float | None:
        row = ha.execute(
            "SELECT mean FROM statistics WHERE metadata_id = ? AND start_ts = ?",
            (ids["battery_soc"], ts),
        ).fetchone()
        return round(row[0], 1) if row and row[0] is not None else None

    soc_at_6pm = soc_mean(w.ts_17_prior)
    soc_at_11am = soc_mean(w.ts_10_today)

    row = ha.execute(
        "SELECT MIN(min) FROM statistics WHERE metadata_id = ? AND start_ts >= ? AND start_ts <= ?",
        (ids["battery_soc"], w.ts_18_prior, w.ts_10_today),
    ).fetchone()
    min_soc_overnight = round(row[0], 1) if row and row[0] is not None else None

    row = ha.execute(
        "SELECT MAX(max) FROM statistics WHERE metadata_id = ? AND start_ts >= ? AND start_ts < ?",
        (ids["battery_soc"], w.ts_06_prior, w.ts_18_prior),
    ).fetchone()
    max_soc_prev_daylight = round(row[0], 1) if row and row[0] is not None else None

    _win = (w.ts_18_prior, w.ts_10_today)
    _q = "WHERE metadata_id = ? AND start_ts >= ? AND start_ts <= ?"

    row = ha.execute(
        f"SELECT SUM(MAX(mean, 0)) FROM statistics {_q}", (ids["pv"], *_win)
    ).fetchone()
    solar_wh = round(row[0]) if row and row[0] is not None else None

    row = ha.execute(
        f"SELECT SUM(ABS(mean)) FROM statistics {_q}", (ids["load"], *_win)
    ).fetchone()
    consumption_wh_load = round(row[0]) if row and row[0] is not None else None

    grid_import_wh = _cum_delta(ha, ids["grid_import"], w.ts_18_prior, w.ts_11_today)
    grid_export_wh = _cum_delta(ha, ids["grid_export"], w.ts_18_prior, w.ts_11_today)
    battery_charged_wh = _cum_delta(ha, ids["battery_charged"], w.ts_18_prior, w.ts_11_today)
    battery_discharged_wh = _cum_delta(ha, ids["battery_discharged"], w.ts_18_prior, w.ts_11_today)

    required = {
        "grid_import_wh": grid_import_wh,
        "grid_export_wh": grid_export_wh,
        "battery_charged_wh": battery_charged_wh,
        "battery_discharged_wh": battery_discharged_wh,
        "solar_wh_before_11am": solar_wh,
    }
    missing = [k for k, v in required.items() if v is None]
    if missing:
        log.warning("Skipping %s — missing data for: %s", date_str, ", ".join(missing))
        return None

    consumption_wh = (
        solar_wh + grid_import_wh + battery_discharged_wh
        - grid_export_wh - battery_charged_wh
    )

    def _r1(agg: str, key: str) -> float | None:
        r = ha.execute(f"SELECT {agg} FROM statistics {_q}", (ids[key], *_win)).fetchone()
        return round(r[0], 1) if r and r[0] is not None else None

    min_outdoor_temp = _r1("MIN(min)", "outdoor_temp")
    avg_indoor_temp = _r1("AVG(mean)", "indoor_temp")
    bom_temp_min = _r1("MIN(min)", "weather_temp")
    bom_temp_mean = _r1("AVG(mean)", "weather_temp")
    bom_temp_max = _r1("MAX(max)", "weather_temp")

    row = ha.execute(
        "SELECT MAX(max) FROM statistics WHERE metadata_id = ? AND start_ts >= ? AND start_ts <= ?",
        (ids["weather_temp"], w.ts_12_prior, w.ts_17_prior),
    ).fetchone()
    bom_temp_afternoon_max = round(row[0], 1) if row and row[0] is not None else None

    bom_feels_like_min = _r1("MIN(min)", "weather_feels_like")
    bom_wind_mean = _r1("AVG(mean)", "weather_wind")
    bom_gust_max = _r1("MAX(max)", "weather_gust")
    bom_humidity_mean = _r1("AVG(mean)", "weather_humidity")
    bom_humidity_max = _r1("MAX(max)", "weather_humidity")

    row = ha.execute(
        f"SELECT MAX(CAST(state AS REAL)) FROM statistics {_q}",
        (ids["weather_rain"], *_win),
    ).fetchone()
    bom_rain_max = round(row[0], 1) if row and row[0] is not None else None

    solcast_forecast_tomorrow_wh: int | None = None
    if ids["solcast"] is not None:
        row = ha.execute(
            "SELECT state FROM statistics WHERE metadata_id = ? AND start_ts = ?",
            (ids["solcast"], w.ts_17_prior),
        ).fetchone()
        if row and row[0] is not None:
            solcast_forecast_tomorrow_wh = int(float(row[0]) * 1000)

    median_indoor_temp: float | None = None
    if ids["median_temp"] is not None:
        median_indoor_temp = _r1("AVG(mean)", "median_temp")

    median_indoor_humidity: float | None = None
    if ids["median_humidity"] is not None:
        median_indoor_humidity = _r1("AVG(mean)", "median_humidity")

    guests: int | None = None
    if ids["guests"] is not None and cfg.sensors.guests is not None:
        row = ha.execute(
            f"SELECT MAX(mean) FROM statistics {_q}", (ids["guests"], *_win)
        ).fetchone()
        max_guests = row[0] if row and row[0] is not None else None
        if max_guests is not None:
            guests = 1 if max_guests > 0.5 else 0

    curtailment_likely = (
        1 if (max_soc_prev_daylight is not None and max_soc_prev_daylight >= 99) else 0
    )

    large_imbalance: int | None = None
    if consumption_wh_load is not None:
        imbalance = consumption_wh_load - consumption_wh
        if abs(imbalance) > _IMBALANCE_WARN_THRESHOLD:
            large_imbalance = imbalance

    now_utc = datetime.now(UTC).isoformat()
    return {
        "date": date_str,
        "provider": cfg.provider_for(morning_date),
        "guests": guests,
        "absence_period": int(cfg.is_absence(morning_date)),
        "data_gap": int(cfg.is_data_gap(morning_date)),
        "soc_at_6pm": soc_at_6pm,
        "min_soc_overnight": min_soc_overnight,
        "max_soc_prev_daylight": max_soc_prev_daylight,
        "soc_at_11am": soc_at_11am,
        "min_outdoor_temp": min_outdoor_temp,
        "avg_indoor_temp": avg_indoor_temp,
        "bom_temp_min": bom_temp_min,
        "bom_temp_mean": bom_temp_mean,
        "bom_feels_like_min": bom_feels_like_min,
        "bom_rain_max": bom_rain_max,
        "bom_wind_mean": bom_wind_mean,
        "bom_gust_max": bom_gust_max,
        "solcast_forecast_tomorrow_wh": solcast_forecast_tomorrow_wh,
        "median_indoor_temp": median_indoor_temp,
        "bom_temp_max": bom_temp_max,
        "bom_temp_afternoon_max": bom_temp_afternoon_max,
        "bom_humidity_mean": bom_humidity_mean,
        "bom_humidity_max": bom_humidity_max,
        "median_indoor_humidity": median_indoor_humidity,
        "solar_wh_before_11am": solar_wh,
        "consumption_wh": consumption_wh,
        "consumption_wh_load": consumption_wh_load,
        "grid_import_wh": grid_import_wh,
        "grid_export_wh": grid_export_wh,
        "battery_charged_wh": battery_charged_wh,
        "battery_discharged_wh": battery_discharged_wh,
        "curtailment_likely": curtailment_likely,
        "extracted_at": now_utc,
        "extraction_version": __version__,
        "_large_imbalance": large_imbalance,  # None or int; used by extract_all, not written to DB
    }


def extract_all(
    ha_db: Path,
    dataset_db: Path,
    cfg: Config,
    rebuild: bool = False,
    from_date: date | None = None,
) -> None:
    """Extract all daily observations.

    Args:
        ha_db: Path to Home Assistant SQLite database (will be opened read-only)
        dataset_db: Path to output dataset SQLite database
        cfg: Loaded configuration
        rebuild: If True, drop all tables and re-extract from FIRST_DATE
        from_date: If set, re-extract from this date forward (incremental)
    """
    ha = sqlite3.connect(f"file:{ha_db}?mode=ro", uri=True)
    ds = sqlite3.connect(dataset_db)

    schema_sql = (Path(__file__).parent / "schema.sql").read_text()

    if rebuild:
        ds.executescript(
            "DROP TABLE IF EXISTS daily_observations;"
            "DROP TABLE IF EXISTS extraction_meta;"
        )

    ds.executescript(schema_sql)

    ids = _get_metadata_ids(ha, cfg)

    if from_date is not None:
        start = from_date
    elif rebuild:
        start = FIRST_DATE
    else:
        row = ds.execute("SELECT MAX(date) FROM daily_observations").fetchone()
        max_date_str = row[0] if row and row[0] else None
        if max_date_str:
            start = date.fromisoformat(max_date_str) + timedelta(days=1)
        else:
            start = FIRST_DATE

    yesterday = date.today() - timedelta(days=1)

    if start > yesterday:
        log.info("Dataset is up to date through %s — nothing to extract.", yesterday)
        ha.close()
        ds.close()
        return

    log.info("Extracting %s → %s", start, yesterday)

    inserted = replaced = skipped = 0
    gap_warnings: list[str] = []
    current = start
    while current <= yesterday:
        date_str = current.isoformat()
        row_data = extract_row(ha, ids, current, cfg)

        if row_data is None:
            skipped += 1
            current += timedelta(days=1)
            continue

        imbalance = row_data.pop("_large_imbalance")
        if imbalance is not None:
            soc_swing = abs((row_data.get("soc_at_6pm") or 0) - (row_data.get("soc_at_11am") or 0))
            battery_zero = (
                (row_data.get("battery_charged_wh") or 0) < _BATTERY_ZERO_THRESHOLD
                and (row_data.get("battery_discharged_wh") or 0) < _BATTERY_ZERO_THRESHOLD
                and soc_swing > _SOC_SWING_THRESHOLD
            )
            solar_zero = (row_data.get("solar_wh_before_11am") or 0) == 0
            if battery_zero or solar_zero:
                reason = (
                    "near-zero battery throughput despite SOC swing"
                    if battery_zero else "zero solar"
                )
                log.warning(
                    "Possible data gap on %s: large imbalance (%+d Wh) with %s"
                    " — check this date and ±1 day in HA",
                    date_str,
                    imbalance,
                    reason,
                )
                gap_warnings.append(date_str)

        existing = ds.execute(
            "SELECT 1 FROM daily_observations WHERE date = ?", (date_str,)
        ).fetchone()

        ds.execute(
            """
            INSERT OR REPLACE INTO daily_observations (
                date, provider, guests, absence_period, data_gap,
                soc_at_6pm, min_soc_overnight, max_soc_prev_daylight, soc_at_11am,
                min_outdoor_temp, avg_indoor_temp,
                bom_temp_min, bom_temp_mean, bom_feels_like_min, bom_rain_max,
                bom_wind_mean, bom_gust_max, solcast_forecast_tomorrow_wh,
                median_indoor_temp, bom_temp_max, bom_temp_afternoon_max,
                bom_humidity_mean, bom_humidity_max, median_indoor_humidity,
                solar_wh_before_11am, consumption_wh, consumption_wh_load,
                grid_import_wh, grid_export_wh, battery_charged_wh, battery_discharged_wh,
                curtailment_likely, extracted_at, extraction_version
            ) VALUES (
                :date, :provider, :guests, :absence_period, :data_gap,
                :soc_at_6pm, :min_soc_overnight, :max_soc_prev_daylight, :soc_at_11am,
                :min_outdoor_temp, :avg_indoor_temp,
                :bom_temp_min, :bom_temp_mean, :bom_feels_like_min, :bom_rain_max,
                :bom_wind_mean, :bom_gust_max, :solcast_forecast_tomorrow_wh,
                :median_indoor_temp, :bom_temp_max, :bom_temp_afternoon_max,
                :bom_humidity_mean, :bom_humidity_max, :median_indoor_humidity,
                :solar_wh_before_11am, :consumption_wh, :consumption_wh_load,
                :grid_import_wh, :grid_export_wh, :battery_charged_wh, :battery_discharged_wh,
                :curtailment_likely, :extracted_at, :extraction_version
            )
            """,
            row_data,
        )

        if existing:
            replaced += 1
            log.info("Replaced existing row for %s", date_str)
        else:
            inserted += 1

        current += timedelta(days=1)

    ds.commit()

    now_utc = datetime.now(UTC).isoformat()
    globird_start = next(
        (p.start_date.isoformat() for p in cfg.providers if p.name == "globird"), ""
    )
    for key, value in [
        ("schema_version", "1.3.0"),
        ("last_full_extraction", now_utc),
        ("source_db_path", str(ha_db.resolve())),
        ("globird_start_date", globird_start),
    ]:
        ds.execute(
            "INSERT OR REPLACE INTO extraction_meta (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, now_utc),
        )
    ds.commit()

    log.info("Done — inserted %d, replaced %d, skipped %d", inserted, replaced, skipped)
    if gap_warnings:
        log.warning(
            "%d possible data gap(s) detected: %s — also check the day before and after each.",
            len(gap_warnings),
            ", ".join(gap_warnings),
        )

    ha.close()
    ds.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract daily observations from the Home Assistant database."
    )
    parser.add_argument("ha_db", type=Path, help="Path to the Home Assistant SQLite database")
    parser.add_argument(
        "--dataset-db",
        type=Path,
        default=Path("data/dataset.db"),
        help="Path to the output dataset SQLite database (default: data/dataset.db)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="Path to config.yaml (default: config/config.yaml)",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Drop and re-extract all rows",
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Re-extract from this date forward",
    )
    args = parser.parse_args()
    cfg = load_config(args.config)
    extract_all(
        ha_db=args.ha_db,
        dataset_db=args.dataset_db,
        cfg=cfg,
        rebuild=args.rebuild,
        from_date=args.from_date,
    )


if __name__ == "__main__":
    main()
