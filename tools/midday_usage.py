"""Household consumption over a daytime window (default 11:00-14:00), by season.

Sizes the "free power" force-charge decision: how much the house itself uses
during the window, at P50/P75/P90, so the charge target can budget for it.

Consumption uses the same energy balance as extraction (CLAUDE.md gotcha #3):

    consumption = pv + grid_import + battery_discharged
                - grid_export - battery_charged

computed per day straight from the HA DB's hourly `statistics` (the dataset DB
only covers the 6pm-11am window). The integrated load sensor is reported
alongside as an independent cross-check — a steady ~0.3 kWh gap is normal;
a jump in the gap means the balance is picking up something other than load.

Absence-period days are excluded. Use --since to isolate a behaviour regime
(e.g. after loads were shifted into the free window in May 2026).

This is a dev/analysis tool; it writes nothing unless --csv is given.

    .venv/Scripts/python -m tools.midday_usage
    .venv/Scripts/python -m tools.midday_usage --since 2026-05-01 --monthly
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from src.config import Config, load_config
from src.extract import _cum_delta, _get_metadata_ids, _required_id

HA_DB_PATH = "data/home-assistant_v2.db"
CONFIG_PATH = "config/config.yaml"
FIRST_DATE = date(2023, 11, 28)

SEASONS = ("Summer", "Autumn", "Winter", "Spring")  # southern hemisphere
_SEASON_BY_MONTH = {
    12: "Summer", 1: "Summer", 2: "Summer",
    3: "Autumn", 4: "Autumn", 5: "Autumn",
    6: "Winter", 7: "Winter", 8: "Winter",
    9: "Spring", 10: "Spring", 11: "Spring",
}


@dataclass
class WindowUsage:
    date: str
    consumption_wh: int
    load_wh: int | None
    pv_wh: int
    grid_import_wh: int
    grid_export_wh: int
    battery_charged_wh: int
    battery_discharged_wh: int


def season_of(d: date) -> tuple[str, str]:
    """Return (season, label) — label includes the year, e.g. "Summer 2025-26"."""
    name = _SEASON_BY_MONTH[d.month]
    if name == "Summer":
        start = d.year if d.month == 12 else d.year - 1
        return name, f"Summer {start}-{(start + 1) % 100:02d}"
    return name, f"{name} {d.year}"


def quantile(xs: list[float], p: float) -> float:
    """Linear-interpolated quantile (numpy's default method), no numpy dependency."""
    s = sorted(xs)
    k = (len(s) - 1) * p
    i = int(k)
    j = min(i + 1, len(s) - 1)
    return s[i] + (s[j] - s[i]) * (k - i)


def window_usage(
    ha: sqlite3.Connection,
    ids: dict[str, int | None],
    d: date,
    cfg: Config,
    start_hour: int,
    end_hour: int,
) -> WindowUsage | None:
    """Energy flows over [start_hour, end_hour) local on day d, or None on a data gap."""

    def ts(hour: int) -> int:
        return int(datetime(d.year, d.month, d.day, hour, tzinfo=cfg.timezone).timestamp())

    # Hourly buckets are labelled by their start; cumulative `sum` at bucket H is the
    # reading at H+1:00. So the window's delta is sum@(start-1) -> sum@(end-1).
    t_before, t_first, t_last = ts(start_hour - 1), ts(start_hour), ts(end_hour - 1)
    n_buckets = end_hour - start_hour

    flows = {
        key: _cum_delta(ha, _required_id(ids, key), t_before, t_last)
        for key in ("grid_import", "grid_export", "battery_charged", "battery_discharged")
    }
    pv = ha.execute(
        "SELECT SUM(MAX(mean, 0)), COUNT(*) FROM statistics "
        "WHERE metadata_id = ? AND start_ts >= ? AND start_ts <= ?",
        (_required_id(ids, "pv"), t_first, t_last),
    ).fetchone()
    load = ha.execute(
        "SELECT SUM(ABS(mean)), COUNT(*) FROM statistics "
        "WHERE metadata_id = ? AND start_ts >= ? AND start_ts <= ?",
        (_required_id(ids, "load"), t_first, t_last),
    ).fetchone()

    if any(v is None for v in flows.values()) or pv[1] != n_buckets:
        return None
    imp, exp = flows["grid_import"], flows["grid_export"]
    chg, dis = flows["battery_charged"], flows["battery_discharged"]
    assert imp is not None and exp is not None and chg is not None and dis is not None
    pv_wh = round(pv[0])
    return WindowUsage(
        date=d.isoformat(),
        consumption_wh=pv_wh + imp + dis - exp - chg,
        load_wh=round(load[0]) if load[1] == n_buckets else None,
        pv_wh=pv_wh,
        grid_import_wh=imp,
        grid_export_wh=exp,
        battery_charged_wh=chg,
        battery_discharged_wh=dis,
    )


def _print_table(title: str, groups: dict[str, list[WindowUsage]]) -> None:
    print(f"\n{title}")
    print(f"{'group':<18}{'n':>5}{'p50':>7}{'p75':>7}{'p90':>7}{'max':>7}  kWh")
    for label, rows in groups.items():
        xs = [r.consumption_wh / 1000 for r in rows]
        print(
            f"{label:<18}{len(xs):>5}{quantile(xs, 0.5):>7.2f}{quantile(xs, 0.75):>7.2f}"
            f"{quantile(xs, 0.9):>7.2f}{max(xs):>7.2f}"
        )


def _print_monthly(rows: list[WindowUsage]) -> None:
    by_month: dict[str, list[WindowUsage]] = defaultdict(list)
    for r in rows:
        by_month[r.date[:7]].append(r)
    print("\nMonthly medians (kWh) — balance vs load-sensor gap should stay ~steady")
    print(f"{'month':<9}{'n':>4}{'cons':>7}{'load':>7}{'gap':>6}{'pv':>7}{'import':>8}"
          f"{'export':>8}{'charged':>9}")
    for month, rs in by_month.items():
        def med(vals: list[int]) -> float:
            return quantile([v / 1000 for v in vals], 0.5)
        cons = med([r.consumption_wh for r in rs])
        loads = [r.load_wh for r in rs if r.load_wh is not None]
        load = med(loads) if loads else float("nan")
        print(
            f"{month:<9}{len(rs):>4}{cons:>7.2f}{load:>7.2f}{cons - load:>6.2f}"
            f"{med([r.pv_wh for r in rs]):>7.2f}{med([r.grid_import_wh for r in rs]):>8.2f}"
            f"{med([r.grid_export_wh for r in rs]):>8.2f}"
            f"{med([r.battery_charged_wh for r in rs]):>9.2f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Household consumption over a daytime window, by season."
    )
    parser.add_argument("--ha-db", default=HA_DB_PATH, help=f"HA DB (default {HA_DB_PATH})")
    parser.add_argument("--config", default=CONFIG_PATH, help=f"Config (default {CONFIG_PATH})")
    parser.add_argument("--start-hour", type=int, default=11, help="Local start (default 11)")
    parser.add_argument("--end-hour", type=int, default=14, help="Local end (default 14)")
    parser.add_argument("--since", type=date.fromisoformat, default=FIRST_DATE,
                        help=f"First date, YYYY-MM-DD (default {FIRST_DATE})")
    parser.add_argument("--until", type=date.fromisoformat, default=None,
                        help="Last date, YYYY-MM-DD (default yesterday)")
    parser.add_argument("--monthly", action="store_true", help="Also print monthly medians")
    parser.add_argument("--csv", type=Path, default=None, help="Write per-day rows to this CSV")
    args = parser.parse_args()
    if not 1 <= args.start_hour < args.end_hour <= 24:
        parser.error("need 1 <= --start-hour < --end-hour <= 24")

    cfg = load_config(Path(args.config))
    until = args.until or datetime.now(cfg.timezone).date() - timedelta(days=1)
    ha = sqlite3.connect(f"file:{args.ha_db}?mode=ro", uri=True)
    ids = _get_metadata_ids(ha, cfg)

    rows: list[WindowUsage] = []
    gaps = absent = 0
    d = args.since
    while d <= until:
        if cfg.is_absence(d):
            absent += 1
        elif (u := window_usage(ha, ids, d, cfg, args.start_hour, args.end_hour)) is None:
            gaps += 1
        else:
            rows.append(u)
        d += timedelta(days=1)

    print(f"Window {args.start_hour:02d}:00-{args.end_hour:02d}:00 local, {args.since} to {until}: "
          f"{len(rows)} usable days, {absent} absence days excluded, {gaps} data-gap days skipped")
    if not rows:
        return

    pooled: dict[str, list[WindowUsage]] = {s: [] for s in SEASONS}
    by_label: dict[str, list[WindowUsage]] = defaultdict(list)
    for r in rows:
        name, label = season_of(date.fromisoformat(r.date))
        pooled[name].append(r)
        by_label[label].append(r)
    _print_table("By season (all years pooled)", {k: v for k, v in pooled.items() if v})
    _print_table("By season and year", by_label)
    if args.monthly:
        _print_monthly(rows)

    if args.csv:
        with args.csv.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(asdict(rows[0])))
            w.writeheader()
            w.writerows(asdict(r) for r in rows)
        print(f"\nWrote {len(rows)} rows to {args.csv}")


if __name__ == "__main__":
    main()
