"""
Backtest the four-zone model against the last year of observations.

All scenarios use full-charge SoC (GloBird overnight charging assumed).

Model scenarios:
  A) Full-charge SoC, fixed P90
  B) Full-charge SoC, seasonal Px (P95 winter / P90 shoulder / P75 summer)

Baseline scenarios (bypass model — use consumption estimate directly):
  C) 3-day rolling average consumption
  D) 7-day rolling average consumption
  E) Seasonal fixed median (dataset medians, computed at runtime)

Absence periods (config.yaml's absence_periods) proxy each night to the same
calendar date one year prior. Rates (export / grid buyback) come from
config.yaml's backtest section.
Output: HTML report.
"""

import json
import sqlite3
from collections import deque
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from statistics import median

from src.config import AbsencePeriod, Config, load_config
from src.model import PredictInputs, predict

DB_PATH = "data/dataset.db"

# Documented defaults for the BacktestParams constructor — main() builds the real
# params from config.yaml + the dataset's last date. Not used directly by any
# scenario/economics function; importing this module is safe without main().
DEFAULT_EXPORT_RATE     = 0.15    # $/kWh
DEFAULT_BUYBACK_RATE    = 0.28    # $/kWh
DEFAULT_BATTERY_WH      = 13800.0 # BYD 13.8 kWh
DEFAULT_HARD_FLOOR_FRAC = 0.10    # grid-discharge floor; breaching it = grid buyback (shortfall)
DEFAULT_SOFT_FLOOR_MARGIN = 0.10  # 'perfect' export leaves this cushion above the hard floor
DEFAULT_BACKTEST_START  = date(2025, 5, 11)
DEFAULT_BACKTEST_END    = date(2026, 5, 20)


@dataclass(frozen=True)
class BacktestParams:
    """Evaluation parameters for a backtest run — built in main() from Config
    + the dataset's last date. Threading this through avoids module-level
    mutable state: importing any scenario/economics function is safe without
    running main() first.
    """
    battery_wh: float = DEFAULT_BATTERY_WH
    hard_floor_frac: float = DEFAULT_HARD_FLOOR_FRAC
    soft_floor_margin: float = DEFAULT_SOFT_FLOOR_MARGIN
    export_rate: float = DEFAULT_EXPORT_RATE
    buyback_rate: float = DEFAULT_BUYBACK_RATE
    start: date = DEFAULT_BACKTEST_START
    end: date = DEFAULT_BACKTEST_END
    absence_periods: list[AbsencePeriod] = field(default_factory=list)

    @classmethod
    def from_config(cls, cfg: Config, *, start: date, end: date) -> "BacktestParams":
        """Derive evaluation params from the same Config that predict() reads,
        so the scoring capacity/floor cannot drift from what the model assumes
        for its decisions.
        """
        return cls(
            battery_wh=cfg.battery_capacity_wh,
            hard_floor_frac=cfg.battery_reserve_fraction,
            export_rate=cfg.backtest.export_rate_per_kwh,
            buyback_rate=cfg.backtest.buyback_rate_per_kwh,
            start=start,
            end=end,
            absence_periods=cfg.absence_periods,
        )

    @property
    def soft_floor_frac(self) -> float:
        return self.hard_floor_frac + self.soft_floor_margin

    def is_absence(self, d: date) -> bool:
        return any(p.contains(d) for p in self.absence_periods)


def season(d: date) -> str:
    m = d.month
    if m in (6, 7, 8):
        return "winter"
    if m in (11, 12, 1, 2, 3):
        return "summer"
    return "shoulder"


def seasonal_confidence(d: date) -> float:
    s = season(d)
    if s == "winter":
        return 0.95
    if s == "summer":
        return 0.75
    return 0.90


def seasonal_confidence_aggressive(d: date) -> float:
    s = season(d)
    if s == "winter":
        return 0.95   # unchanged — winter is loss-making regardless
    if s == "summer":
        return 0.50
    return 0.75


def last_dataset_date(db_path: str) -> date:
    """Latest date present in daily_observations (the dataset's last available day)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    row = conn.execute("SELECT MAX(date) FROM daily_observations").fetchone()
    conn.close()
    if not row or not row[0]:
        raise SystemExit("dataset is empty — run extraction first")
    return date.fromisoformat(row[0])


def one_year_before(d: date) -> date:
    """Same calendar date one year earlier (29 Feb → 28 Feb)."""
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return d.replace(year=d.year - 1, day=28)


def compute_seasonal_medians(rows: dict, params: BacktestParams) -> dict[str, float]:
    """Median consumption_wh per season, over non-absence rows.

    `rows` is already restricted to non-gap rows by `load_rows`'s query; absence
    periods are excluded here via `params.is_absence` so the medians reflect normal
    occupancy.
    """
    buckets: dict[str, list[float]] = {"winter": [], "shoulder": [], "summer": []}
    for ds, row in rows.items():
        d = date.fromisoformat(ds)
        if params.is_absence(d):
            continue
        buckets[season(d)].append(row["consumption_wh"])
    return {s: median(vals) if vals else 0.0 for s, vals in buckets.items()}


def load_rows(db_path: str) -> dict[str, dict]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT date, soc_at_6pm, min_soc_overnight, max_soc_prev_daylight,
               bom_temp_mean, bom_humidity_mean, solcast_forecast_tomorrow_wh,
               consumption_wh, evening_grid_export_wh, data_gap
        FROM daily_observations
        WHERE (data_gap = 0 OR data_gap IS NULL)
          AND soc_at_6pm IS NOT NULL
          AND min_soc_overnight IS NOT NULL
          AND bom_temp_mean IS NOT NULL
          AND consumption_wh IS NOT NULL
        ORDER BY date
    """).fetchall()
    conn.close()
    return {r["date"]: dict(r) for r in rows}


def adjusted_soc(row: dict) -> float:
    soc     = row["soc_at_6pm"]
    max_soc = row["max_soc_prev_daylight"]
    if max_soc is not None and max_soc < 100.0:
        soc = min(100.0, soc + (100.0 - max_soc))
    return soc


def baseline_trough_soc(row: dict, soc_used: float, params: BacktestParams) -> float:
    """Reconstruct the no-export overnight SoC trough (%) for this night.

    `min_soc_overnight` is what actually happened — but it is depressed by any
    real battery-to-grid export during the peak. Add that energy back (via
    `evening_grid_export_wh`) to recover the trough that *would* have occurred
    with no deliberate export. Also shift by the full-charge adjustment applied
    to the 6pm SoC (`soc_used − soc_at_6pm`), since a higher start lifts the whole
    overnight trajectory — including the trough — by the same amount.
    """
    delta_full_charge = soc_used - row["soc_at_6pm"]
    evening_export_wh = row.get("evening_grid_export_wh") or 0
    trough = row["min_soc_overnight"] + delta_full_charge + (evening_export_wh / params.battery_wh) * 100.0
    return min(100.0, trough)


def accum_night(
    monthly: dict, ym: str, *, export_wh: float, trough_soc: float, params: BacktestParams
) -> None:
    """Record one night's economics using the SoC-trough metric.

    `trough_soc` is the reconstructed no-export overnight trough (%). Exporting
    `export_wh` at 6–9pm lowers the whole trajectory, so the simulated trough is
    `trough_soc − export_wh/capacity`. A shortfall (grid buyback) is charged only
    for the portion that pushes the trough below the HARD floor — and only the
    *extra* breach the export causes (a night that was already short with no
    export is not blamed on the export decision). The 'perfect' benchmark exports
    down to the SOFT floor (hard floor + margin), leaving a cushion.
    """
    hard_pct = params.hard_floor_frac * 100.0
    soft_pct = params.soft_floor_frac * 100.0

    sim_trough  = trough_soc - (export_wh / params.battery_wh) * 100.0
    base_breach = max(0.0, (hard_pct - trough_soc) / 100.0 * params.battery_wh)  # unavoidable, no-export
    sim_breach  = max(0.0, (hard_pct - sim_trough) / 100.0 * params.battery_wh)
    shortfall_wh = max(0.0, sim_breach - base_breach)                            # export-caused only

    perfect_export = max(0.0, (trough_soc - soft_pct) / 100.0 * params.battery_wh)

    revenue        = (export_wh / 1000) * params.export_rate
    shortfall_cost = (shortfall_wh / 1000) * params.buyback_rate
    opportunity    = max(0.0, (perfect_export - export_wh) / 1000) * params.export_rate
    perfect_net    = (perfect_export / 1000) * params.export_rate

    monthly[ym]["revenue"]     += revenue
    monthly[ym]["shortfall"]   += shortfall_cost
    monthly[ym]["opportunity"] += opportunity
    monthly[ym]["perfect_net"] += perfect_net
    monthly[ym]["nights"]      += 1


def ensure_month(monthly: dict, ym: str) -> None:
    if ym not in monthly:
        monthly[ym] = dict(revenue=0.0, shortfall=0.0, opportunity=0.0, perfect_net=0.0, nights=0, skipped=0)


def run_model_scenario(
    rows: dict, cfg: Config, params: BacktestParams, seasonal: bool, confidence_fn=None,
    start: date | None = None,
) -> dict[str, dict]:
    """Model-based scenario (full-charge SoC, fixed P90 or seasonal Px).

    If confidence_fn is provided it takes a date and returns a confidence float,
    overriding the seasonal/fixed logic.
    """
    monthly: dict[str, dict] = {}
    d = start or params.start
    while d <= params.end:
        ds = d.isoformat()
        ym = ds[:7]
        ensure_month(monthly, ym)

        in_absence  = params.is_absence(d)
        lookup_date = (d - timedelta(days=365)).isoformat() if in_absence else ds
        row = rows.get(lookup_date)
        if row is None:
            monthly[ym]["skipped"] += 1
            d += timedelta(days=1)
            continue

        soc = adjusted_soc(row)
        if confidence_fn is not None:
            confidence = confidence_fn(d)
        elif seasonal:
            confidence = seasonal_confidence(d)
        else:
            confidence = 0.90

        inp    = PredictInputs(
            soc_at_6pm=soc,
            bom_temp_mean=row["bom_temp_mean"],
            bom_humidity_mean=row["bom_humidity_mean"],
            solcast_forecast_tomorrow_wh=row["solcast_forecast_tomorrow_wh"],
            confidence=confidence,
        )
        result = predict(inp, cfg)
        accum_night(
            monthly, ym,
            export_wh=result.safe_export_wh,
            trough_soc=baseline_trough_soc(row, soc, params),
            params=params,
        )
        d += timedelta(days=1)
    return monthly


def run_rolling_scenario(
    rows: dict, params: BacktestParams, window: int, start: date | None = None
) -> dict[str, dict]:
    """Baseline: rolling N-day average consumption as the estimated need."""
    monthly: dict[str, dict] = {}
    date_set = set(rows.keys())
    recent: deque[float] = deque()

    d = start or params.start
    while d <= params.end:
        ds = d.isoformat()
        ym = ds[:7]
        ensure_month(monthly, ym)

        # Rebuild rolling window: last `window` valid non-gap days before d
        recent.clear()
        check = d - timedelta(days=1)
        while len(recent) < window and check >= (start or params.start) - timedelta(days=window * 2):
            cs = check.isoformat()
            if cs in date_set:
                recent.appendleft(rows[cs]["consumption_wh"])
            check -= timedelta(days=1)

        in_absence  = params.is_absence(d)
        lookup_date = (d - timedelta(days=365)).isoformat() if in_absence else ds
        row = rows.get(lookup_date)
        if row is None or len(recent) == 0:
            monthly[ym]["skipped"] += 1
            d += timedelta(days=1)
            continue

        avg_consumption_wh = sum(recent) / len(recent)
        soc        = adjusted_soc(row)
        battery_wh = soc / 100.0 * params.battery_wh
        min_soc_wh = params.hard_floor_frac * params.battery_wh
        avail_wh   = max(0.0, battery_wh - min_soc_wh)
        export_wh  = max(0.0, avail_wh - avg_consumption_wh)

        accum_night(
            monthly, ym, export_wh=export_wh,
            trough_soc=baseline_trough_soc(row, soc, params), params=params,
        )
        d += timedelta(days=1)
    return monthly


def run_blended_scenario(
    rows: dict, cfg: Config, params: BacktestParams, alpha: float, scale_buffer: bool,
    confidence: float = 0.90, start: date | None = None,
) -> dict[str, dict]:
    """Blended scenario: consumption = alpha * model + (1-alpha) * 3-day rolling average.

    alpha=1.0 is pure model, alpha=0.0 is pure 3-day average.
    If scale_buffer is True the uncertainty buffer is multiplied by alpha (shrinks toward
    zero as the rolling average dominates). If False the full model buffer is kept.
    """
    monthly: dict[str, dict] = {}
    date_set = set(rows.keys())

    d = start or params.start
    while d <= params.end:
        ds = d.isoformat()
        ym = ds[:7]
        ensure_month(monthly, ym)

        # Build 3-day rolling window of actual consumption before d
        recent: list[float] = []
        check = d - timedelta(days=1)
        while len(recent) < 3 and check >= (start or params.start) - timedelta(days=6):
            cs = check.isoformat()
            if cs in date_set:
                recent.append(rows[cs]["consumption_wh"])
            check -= timedelta(days=1)

        in_absence  = params.is_absence(d)
        lookup_date = (d - timedelta(days=365)).isoformat() if in_absence else ds
        row = rows.get(lookup_date)
        if row is None or len(recent) == 0:
            monthly[ym]["skipped"] += 1
            d += timedelta(days=1)
            continue

        inp = PredictInputs(
            soc_at_6pm=adjusted_soc(row),
            bom_temp_mean=row["bom_temp_mean"],
            bom_humidity_mean=row["bom_humidity_mean"],
            solcast_forecast_tomorrow_wh=row["solcast_forecast_tomorrow_wh"],
            confidence=confidence,
        )
        result = predict(inp, cfg)

        rolling_avg_wh = sum(recent) / len(recent)
        model_point_wh = result.predicted_consumption_kwh * 1000.0
        blended_point_wh = alpha * model_point_wh + (1.0 - alpha) * rolling_avg_wh

        buffer_wh = result.error_buffer_kwh * 1000.0
        if scale_buffer:
            buffer_wh *= alpha

        total_needed_wh = blended_point_wh + buffer_wh
        export_wh = max(0.0, result.available_discharge_wh - total_needed_wh)

        accum_night(
            monthly, ym,
            export_wh=export_wh,
            trough_soc=baseline_trough_soc(row, adjusted_soc(row), params),
            params=params,
        )
        d += timedelta(days=1)
    return monthly


def run_seasonal_fixed_scenario(
    rows: dict, params: BacktestParams, seasonal_medians: dict[str, float], start: date | None = None
) -> dict[str, dict]:
    """Baseline: seasonal fixed median consumption as the estimated need."""
    monthly: dict[str, dict] = {}
    d = start or params.start
    while d <= params.end:
        ds = d.isoformat()
        ym = ds[:7]
        ensure_month(monthly, ym)

        in_absence  = params.is_absence(d)
        lookup_date = (d - timedelta(days=365)).isoformat() if in_absence else ds
        row = rows.get(lookup_date)
        if row is None:
            monthly[ym]["skipped"] += 1
            d += timedelta(days=1)
            continue

        fixed_wh  = seasonal_medians[season(d)]
        soc       = adjusted_soc(row)
        battery_wh = soc / 100.0 * params.battery_wh
        min_soc_wh = params.hard_floor_frac * params.battery_wh
        avail_wh   = max(0.0, battery_wh - min_soc_wh)
        export_wh  = max(0.0, avail_wh - fixed_wh)

        accum_night(
            monthly, ym, export_wh=export_wh,
            trough_soc=baseline_trough_soc(row, soc, params), params=params,
        )
        d += timedelta(days=1)
    return monthly


SCENARIOS = [
    ("A", "Model — full-charge SoC, fixed P90"),
    ("B", "Model — full-charge SoC, seasonal Px (P95/P90/P75)"),
    ("H", "Model — full-charge SoC, fixed P75"),
    ("F", "Model — full-charge SoC, aggressive seasonal Px (P95/P75/P50)"),
    ("G", "Model — full-charge SoC, fixed P50"),
    ("C", "Baseline — 3-day rolling average"),
    ("D", "Baseline — 7-day rolling average"),
    ("E", "Baseline — seasonal fixed median"),
    ("I1", "Blended α=0.75 — 75% model + 25% 3-day avg, buffer fixed"),
    ("I2", "Blended α=0.75 — 75% model + 25% 3-day avg, buffer scaled"),
    ("I3", "Blended α=0.50 — 50% model + 50% 3-day avg, buffer fixed"),
    ("I4", "Blended α=0.50 — 50% model + 50% 3-day avg, buffer scaled"),
    ("I5", "Blended α=0.25 — 25% model + 75% 3-day avg, buffer fixed"),
    ("I6", "Blended α=0.25 — 25% model + 75% 3-day avg, buffer scaled"),
]

MONTH_NAMES = {
    "01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr",
    "05": "May", "06": "Jun", "07": "Jul", "08": "Aug",
    "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec",
}

SEASON_LABEL = {
    "06": "Winter P95", "07": "Winter P95", "08": "Winter P95",
    "11": "Summer P75", "12": "Summer P75", "01": "Summer P75",
    "02": "Summer P75", "03": "Summer P75",
}

SEASON_LABEL_AGGRESSIVE = {
    "06": "Winter P95", "07": "Winter P95", "08": "Winter P95",
    "11": "Summer P50", "12": "Summer P50", "01": "Summer P50",
    "02": "Summer P50", "03": "Summer P50",
}

def seasonal_fixed_label(mon: str, seasonal_medians: dict[str, float]) -> str:
    """Month → 'Winter 12.2kWh'-style label using computed seasonal medians (scenario E)."""
    if mon in ("06", "07", "08"):
        s = "winter"
    elif mon in ("11", "12", "01", "02", "03"):
        s = "summer"
    else:
        s = "shoulder"
    return f"{s.capitalize()} {seasonal_medians[s] / 1000:.1f}kWh"


def _capture(net: float, perfect_net: float) -> tuple[str, str, float]:
    """Net-capture cell as (display_text, css_class, sort_value).

    Returns '—' (undefined) when there was no opportunity to capture
    (perfect_net ≈ 0) — the ratio net/perfect_net is meaningless there, so
    showing 0% would wrongly imply the strategy missed something. Such rows
    sort to the bottom.
    """
    if perfect_net <= 1e-9:
        return ("—", "", float("-inf"))
    cap = net / perfect_net * 100.0
    cls = "eff-high" if cap >= 65 else "eff-mid" if cap >= 55 else "eff-low"
    return (f"{cap:.1f}%", cls, cap)


def _cell_class(val: float) -> str:
    if val > 0.5:
        return "pos"
    if val < -0.5:
        return "neg"
    return ""


def _recent_summary_rows(results: dict, label: str, nights_warn: int) -> str:
    """Return HTML rows for a recent-window summary table (all scenarios, single aggregated row each)."""
    rows_html = []

    computed = []
    for key, desc in SCENARIOS:
        m_data = results[key]
        tot_rev   = sum(m["revenue"]     for m in m_data.values())
        tot_short = sum(m["shortfall"]   for m in m_data.values())
        tot_perf  = sum(m["perfect_net"] for m in m_data.values())
        tot_nights = sum(m["nights"]     for m in m_data.values())
        net = tot_rev - tot_short
        cap_text, cap_cls, cap_sort = _capture(net, tot_perf)
        computed.append((cap_sort, cap_text, cap_cls, key, desc, tot_rev, tot_short, tot_perf, net, tot_nights))

    computed.sort(key=lambda c: c[0], reverse=True)  # net capture descending

    for cap_sort, cap_text, cap_cls, key, desc, tot_rev, tot_short, tot_perf, net, tot_nights in computed:
        is_model = key in ("A", "B", "F", "G", "H", "I1", "I2", "I3", "I4", "I5", "I6")
        badge = '<span class="model-badge">model</span>' if is_model else '<span class="baseline-badge">baseline</span>'
        warn = f' <span class="skipped">(n={tot_nights})</span>' if tot_nights <= nights_warn else f' <span style="color:#555;font-size:0.8rem">(n={tot_nights})</span>'
        rows_html.append(f"""
          <tr>
            <td>{badge} <strong>{key}</strong> &mdash; {desc}</td>
            <td class="num">${tot_rev:.2f}</td>
            <td class="num neg2">-${tot_short:.2f}</td>
            <td class="num {_cell_class(net)}"><strong>${net:.2f}</strong></td>
            <td class="num">${tot_perf:.2f}</td>
            <td class="num {cap_cls}">{cap_text}{warn}</td>
          </tr>""")
    return "".join(rows_html)


def consumption_accuracy_series(rows: dict, cfg: Config, params: BacktestParams) -> list[dict]:
    """Per-night predicted vs actual consumption over the backtest window.

    Predicted = the model's central (P50) consumption estimate, so it's a
    like-for-like usage comparison independent of the export buffer. Residual is
    actual − predicted (kWh): negative ⇒ model over-predicts (conservative),
    positive ⇒ under-predicts (less safety margin). Absence-period nights are
    skipped (their consumption is unrepresentative).
    """
    series = []
    d = params.start
    while d <= params.end:
        ds = d.isoformat()
        row = rows.get(ds)
        if row is not None and not params.is_absence(d):
            inp = PredictInputs(
                soc_at_6pm=row["soc_at_6pm"],
                bom_temp_mean=row["bom_temp_mean"],
                bom_humidity_mean=row["bom_humidity_mean"],
                solcast_forecast_tomorrow_wh=row["solcast_forecast_tomorrow_wh"],
                confidence=0.50,
            )
            res = predict(inp, cfg)
            pred = res.predicted_consumption_kwh
            actual = row["consumption_wh"] / 1000.0
            series.append({"date": ds, "zone": res.zone, "pred": pred,
                           "actual": actual, "resid": actual - pred})
        d += timedelta(days=1)
    return series


def _residual_svg(series: list[dict], roll: int = 14) -> str:
    """Inline SVG: per-night residual dots + a rolling-mean line + zero line."""
    if not series:
        return "<p>No data.</p>"
    W, H = 860, 240
    ml, mr, mt, mb = 44, 12, 12, 28          # margins
    pw, ph = W - ml - mr, H - mt - mb
    resids = [p["resid"] for p in series]
    lo = min(-3.0, min(resids))
    hi = max(3.0, max(resids))
    n = len(series)

    def x(i: int) -> float:
        return ml + (pw * i / max(1, n - 1))

    def y(v: float) -> float:
        return mt + ph * (hi - v) / (hi - lo)

    y0 = y(0.0)
    # rolling mean
    roll_pts = []
    for i in range(n):
        lo_i = max(0, i - roll + 1)
        window = resids[lo_i:i + 1]
        roll_pts.append((x(i), y(sum(window) / len(window))))
    roll_path = " ".join(f"{px:.1f},{py:.1f}" for px, py in roll_pts)
    dots = "".join(
        f'<circle cx="{x(i):.1f}" cy="{y(p["resid"]):.1f}" r="1.6" fill="#9bb8d8"/>'
        for i, p in enumerate(series)
    )
    # month gridlines/labels
    ticks = ""
    seen = set()
    for i, p in enumerate(series):
        ym = p["date"][:7]
        if ym not in seen:
            seen.add(ym)
            px = x(i)
            ticks += f'<line x1="{px:.1f}" y1="{mt}" x2="{px:.1f}" y2="{mt+ph}" stroke="#eee"/>'
            ticks += (f'<text x="{px:.1f}" y="{H-8}" font-size="9" fill="#999" '
                      f'text-anchor="middle">{MONTH_NAMES[ym[5:7]]}</text>')
    # y labels
    ylabels = ""
    for v in (hi, 0.0, lo):
        ylabels += (f'<text x="{ml-6}" y="{y(v)+3:.1f}" font-size="9" fill="#999" '
                    f'text-anchor="end">{v:+.0f}</text>')
    return f"""<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px" role="img">
      {ticks}
      <line x1="{ml}" y1="{y0:.1f}" x2="{ml+pw}" y2="{y0:.1f}" stroke="#bbb" stroke-dasharray="3,3"/>
      {ylabels}
      {dots}
      <polyline points="{roll_path}" fill="none" stroke="#c0392b" stroke-width="2"/>
    </svg>"""


def build_html(
    results: dict, recent_14: dict, recent_30: dict, accuracy: list[dict],
    params: BacktestParams, seasonal_medians: dict[str, float],
) -> str:
    months = sorted(next(iter(results.values())).keys())
    cell_class = _cell_class

    # Span of the (rolling) window in whole months, and how many days were served by
    # the prior-year absence proxy (window ∩ any configured absence period). When this
    # hits 0 the rolling window has moved past all absence periods and the proxy
    # handling can be retired.
    months_span = (params.end.year - params.start.year) * 12 + (params.end.month - params.start.month)
    proxied_days = 0
    for p in params.absence_periods:
        ov_start = max(params.start, p.start)
        ov_end   = min(params.end, p.end)
        if ov_start <= ov_end:
            proxied_days += (ov_end - ov_start).days + 1

    # Consumption prediction accuracy (drift monitor)
    def _mean_resid(k: int) -> float:
        tail = accuracy[-k:]
        return sum(p["resid"] for p in tail) / len(tail) if tail else 0.0
    acc_svg = _residual_svg(accuracy)
    acc_14, acc_30, acc_90 = _mean_resid(14), _mean_resid(30), _mean_resid(90)
    accuracy_section = f"""
<div class="summary-section">
  <h2>Consumption prediction accuracy <span style="font-size:0.8rem;font-weight:normal;color:#777">(drift monitor — residual = actual &minus; predicted, kWh)</span></h2>
  <p style="font-size:0.85rem;color:#555;margin:0 0 0.5rem">
    Mean residual &mdash; last 14 nights: <strong>{acc_14:+.2f}</strong> &nbsp;|&nbsp;
    30: <strong>{acc_30:+.2f}</strong> &nbsp;|&nbsp; 90: <strong>{acc_90:+.2f}</strong> kWh
  </p>
  {acc_svg}
  <p style="font-size:0.8rem;color:#666;margin:0.4rem 0 0">
    Dots = each night&rsquo;s residual; <span style="color:#c0392b">red line</span> = 14-night rolling mean; dashed = zero.
    Line drifting <strong>below</strong> zero ⇒ model increasingly over-predicts (conservative, e.g. reduced overnight heating).
    Drifting <strong>above</strong> zero ⇒ under-predicts (less safety margin — a retrain trigger).
  </p>
</div>"""

    scenario_tables = []
    for key, label in SCENARIOS:
        m_data   = results[key]
        is_model = key in ("A", "B", "F", "G", "H", "I1", "I2", "I3", "I4", "I5", "I6")
        rows_html = []
        tot = dict(revenue=0.0, shortfall=0.0, opportunity=0.0, perfect_net=0.0, nights=0)

        for ym in months:
            m   = m_data[ym]
            mon = ym[5:7]
            net         = m["revenue"] - m["shortfall"]
            cap_text, cap_cls, _ = _capture(net, m["perfect_net"])

            season_tag = ""
            if key == "B":
                lbl = SEASON_LABEL.get(mon)
                if lbl:
                    season_tag = f'<span class="season-tag">{lbl}</span>'
            elif key == "F":
                sl = SEASON_LABEL_AGGRESSIVE.get(mon, "Shoulder P75")
                season_tag = f'<span class="season-tag">{sl}</span>'
            elif key == "E":
                sl = seasonal_fixed_label(mon, seasonal_medians)
                season_tag = f'<span class="season-tag">{sl}</span>'

            skipped = f'<span class="skipped"> ({m["skipped"]} skipped)</span>' if m.get("skipped") else ""
            rows_html.append(f"""
              <tr>
                <td>{MONTH_NAMES[mon]} {ym[:4]}{season_tag}</td>
                <td>{m['nights']}{skipped}</td>
                <td class="num">${m['revenue']:.2f}</td>
                <td class="num neg2">-${m['shortfall']:.2f}</td>
                <td class="num {cell_class(net)}">${net:.2f}</td>
                <td class="num">${m['perfect_net']:.2f}</td>
                <td class="num {cap_cls}">{cap_text}</td>
              </tr>""")
            for k in ("revenue", "shortfall", "opportunity", "perfect_net", "nights"):
                tot[k] += m[k]

        tot_net = tot["revenue"] - tot["shortfall"]
        tot_cap_text, tot_cap_cls, _ = _capture(tot_net, tot["perfect_net"])
        badge   = '<span class="model-badge">model</span>' if is_model else '<span class="baseline-badge">baseline</span>'
        scenario_tables.append(f"""
        <section>
          <h2>Scenario {key}: {label} {badge}</h2>
          <table>
            <thead>
              <tr>
                <th>Month</th><th>Nights</th><th>Revenue</th>
                <th>Shortfall</th><th>Net</th><th>Safe net</th><th>Net capture</th>
              </tr>
            </thead>
            <tbody>
              {''.join(rows_html)}
            </tbody>
            <tfoot>
              <tr>
                <td><strong>Total</strong></td>
                <td>{tot['nights']}</td>
                <td class="num"><strong>${tot['revenue']:.2f}</strong></td>
                <td class="num neg2"><strong>-${tot['shortfall']:.2f}</strong></td>
                <td class="num {cell_class(tot_net)}"><strong>${tot_net:.2f}</strong></td>
                <td class="num"><strong>${tot['perfect_net']:.2f}</strong></td>
                <td class="num {tot_cap_cls}"><strong>{tot_cap_text}</strong></td>
              </tr>
            </tfoot>
          </table>
        </section>""")

    # Summary comparison table — winter (Jun–Aug) excluded as loss-making in all scenarios.
    # Sorted by net capture descending.
    summary_computed = []
    for key, label in SCENARIOS:
        m_data          = results[key]
        is_model        = key in ("A", "B", "F", "G", "H", "I1", "I2", "I3", "I4", "I5", "I6")
        non_winter      = {ym: m for ym, m in m_data.items() if ym[5:7] not in ("06", "07", "08")}
        tot_rev         = sum(m["revenue"]     for m in non_winter.values())
        tot_short       = sum(m["shortfall"]   for m in non_winter.values())
        tot_perfect_net = sum(m["perfect_net"] for m in non_winter.values())
        tot_net         = tot_rev - tot_short
        cap_text, cap_cls, cap_sort = _capture(tot_net, tot_perfect_net)
        summary_computed.append((cap_sort, cap_text, cap_cls, key, label, is_model,
                                 tot_rev, tot_short, tot_perfect_net, tot_net))

    summary_computed.sort(key=lambda c: c[0], reverse=True)  # net capture descending

    summary_rows = []
    for cap_sort, cap_text, cap_cls, key, label, is_model, tot_rev, tot_short, tot_perfect_net, tot_net in summary_computed:
        badge = '<span class="model-badge">model</span>' if is_model else '<span class="baseline-badge">baseline</span>'
        summary_rows.append(f"""
          <tr>
            <td>{badge} <strong>{key}</strong> — {label}</td>
            <td class="num">${tot_rev:.2f}</td>
            <td class="num neg2">-${tot_short:.2f}</td>
            <td class="num {cell_class(tot_net)}"><strong>${tot_net:.2f}</strong></td>
            <td class="num">${tot_perfect_net:.2f}</td>
            <td class="num {cap_cls}">{cap_text}</td>
          </tr>""")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Safe Export Backtest — {params.start} to {params.end}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 0.25rem; }}
  .subtitle {{ color: #555; font-size: 0.9rem; margin-bottom: 2rem; }}
  h2 {{ font-size: 1.05rem; margin: 2rem 0 0.5rem; border-bottom: 2px solid #ddd; padding-bottom: 0.25rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; margin-bottom: 1rem; }}
  th {{ background: #f0f0f0; text-align: left; padding: 0.4rem 0.6rem; border-bottom: 2px solid #ccc; }}
  td {{ padding: 0.35rem 0.6rem; border-bottom: 1px solid #eee; }}
  tfoot td {{ border-top: 2px solid #ccc; border-bottom: none; background: #fafafa; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .pos {{ color: #1a7a1a; font-weight: 600; }}
  .neg {{ color: #c0392b; font-weight: 600; }}
  .neg2 {{ color: #c0392b; }}
  .eff-high {{ color: #1a7a1a; font-weight: 600; }}
  .eff-mid  {{ color: #b06000; }}
  .eff-low  {{ color: #c0392b; }}
  .skipped  {{ color: #999; font-size: 0.8rem; }}
  .season-tag {{ font-size: 0.72rem; color: #555; background: #eee; border-radius: 3px;
                 padding: 1px 4px; margin-left: 6px; vertical-align: middle; }}
  .model-badge    {{ font-size: 0.72rem; color: #fff; background: #3a6fba; border-radius: 3px;
                     padding: 1px 5px; margin-right: 4px; vertical-align: middle; }}
  .baseline-badge {{ font-size: 0.72rem; color: #fff; background: #777; border-radius: 3px;
                     padding: 1px 5px; margin-right: 4px; vertical-align: middle; }}
  .summary-section {{ background: #f7f7f7; border: 1px solid #ddd; border-radius: 6px;
                       padding: 1rem 1.2rem; margin-bottom: 2rem; }}
  .summary-section h2 {{ border-bottom-color: #bbb; }}
  .rates {{ font-size: 0.85rem; color: #555; margin-bottom: 1.5rem; }}
  .note {{ font-size: 0.82rem; color: #666; margin-top: 1.5rem; border-top: 1px solid #eee; padding-top: 0.75rem; }}
</style>
</head>
<body>
<h1>Safe Export Backtest</h1>
<p class="subtitle">{params.start} to {params.end} &nbsp;|&nbsp; {months_span} months &nbsp;|&nbsp; Absence period: {proxied_days} days proxied (prior-year) &nbsp;|&nbsp; All scenarios: full-charge SoC</p>
<p class="rates">Export rate: <strong>${params.export_rate:.2f}/kWh</strong> &nbsp;&nbsp; Grid buyback: <strong>${params.buyback_rate:.2f}/kWh</strong></p>

<div class="summary-section recent-section">
  <h2>Recent performance &mdash; last 14 days <span style="font-size:0.8rem;font-weight:normal;color:#777">(small sample &mdash; treat as directional only)</span></h2>
  <table>
    <thead>
      <tr><th>Scenario</th><th>Revenue</th><th>Shortfall</th><th>Net</th><th>Safe net</th><th>Net capture (n)</th></tr>
    </thead>
    <tbody>{_recent_summary_rows(recent_14, "14-day", nights_warn=14)}</tbody>
  </table>
</div>

<div class="summary-section recent-section">
  <h2>Recent performance &mdash; last 30 days <span style="font-size:0.8rem;font-weight:normal;color:#777">(moderately noisy)</span></h2>
  <table>
    <thead>
      <tr><th>Scenario</th><th>Revenue</th><th>Shortfall</th><th>Net</th><th>Safe net</th><th>Net capture (n)</th></tr>
    </thead>
    <tbody>{_recent_summary_rows(recent_30, "30-day", nights_warn=20)}</tbody>
  </table>
</div>
{accuracy_section}
<div class="summary-section">
  <h2>Non-winter scenario summary <span style="font-size:0.8rem;font-weight:normal;color:#777">(Sep–May; winter Jun–Aug excluded — the model correctly idles in winter (≈zero export, zero shortfall), so its net capture is noisy/low-signal there, not loss-making. Monthly tables below still show winter.)</span></h2>
  <table>
    <thead>
      <tr><th>Scenario</th><th>Revenue</th><th>Shortfall</th><th>Net</th><th>Safe net</th><th>Net capture</th></tr>
    </thead>
    <tbody>{''.join(summary_rows)}</tbody>
  </table>
  <p style="font-size:0.82rem;color:#555;margin:0.5rem 0 0">
    Seasonal Px (B): Winter (Jun–Aug) P95 &nbsp;|&nbsp; Summer (Nov–Mar) P75 &nbsp;|&nbsp; Shoulder P90<br>
    Aggressive Px (F): Winter P95 &nbsp;|&nbsp; Shoulder P75 &nbsp;|&nbsp; Summer P50<br>
    Seasonal fixed (E): Winter {seasonal_medians['winter']:,.0f} Wh &nbsp;|&nbsp; Shoulder {seasonal_medians['shoulder']:,.0f} Wh &nbsp;|&nbsp; Summer {seasonal_medians['summer']:,.0f} Wh &nbsp;(dataset medians)<br>
    Net capture colour: <span style="color:#1a7a1a;font-weight:600">≥65%</span> &nbsp;|&nbsp; <span style="color:#b06000">≥55%</span> &nbsp;|&nbsp; <span style="color:#c0392b">&lt;55%</span>
  </p>
</div>

{''.join(scenario_tables)}

<p class="note">
  <strong>Metric: SoC trough.</strong> Each night is judged against the actual overnight SoC trough (<code>min_soc_overnight</code>), reconstructed to a no-export baseline by adding back real peak exports (<code>evening_grid_export_wh</code>) and the full-charge adjustment. Exporting <code>E</code> lowers the trough by <code>E&divide;capacity</code>; a shortfall (grid buyback) is charged only for the part that pushes the trough below the <strong>hard floor</strong> (min SoC), and only the extra breach the export causes.<br>
  <strong>Safe net</strong> = what a hindsight-perfect <em>but cautious</em> model would earn: export down to the <strong>soft floor</strong> (min SoC + 10pt cushion) every night, zero shortfall. It is a conservative benchmark, not a maximum &mdash; it deliberately leaves the 10pt cushion unexported. A model that dips into that cushion on a night it turns out not to need it earns real revenue on energy safe net left in the battery, so <strong>Net and Net capture can exceed Safe net / 100%</strong>. That is the cushion being spent for extra return; on a night where consumption runs higher than predicted, the same aggression is what produces a hard-floor shortfall. &ldquo;&mdash;&rdquo; net capture means there was no opportunity (safe net &approx; 0), so the ratio is undefined.<br>
  <strong>Net capture</strong> = net &divide; safe net &mdash; what fraction of that best-possible-yet-cautious outcome was achieved (over 100% means the model out-earned the cautious benchmark by spending the cushion, see above). Accounts for the rate asymmetry: shortfall costs $0.28/kWh to cover while missed export only foregoes $0.15/kWh, so aggressive-but-breaching strategies score lower than their raw revenue suggests.<br>
  <strong>Full-charge SoC</strong> = 6pm SoC adjusted upward by however short of 100% the prior day&rsquo;s peak fell, simulating GloBird overnight charging. Capacity and floor come from <code>config.yaml</code>.
</p>
</body>
</html>"""


def build_json(results: dict) -> dict:
    """Build a summary dict suitable for JSON output."""
    output = {}
    for key, label in SCENARIOS:
        m_data          = results[key]
        tot_rev         = sum(m["revenue"]     for m in m_data.values())
        tot_short       = sum(m["shortfall"]   for m in m_data.values())
        tot_perfect_net = sum(m["perfect_net"] for m in m_data.values())
        tot_net         = round(tot_rev - tot_short, 2)
        # None (not 0) when there was no opportunity to capture — the ratio is undefined.
        tot_net_capture = round(tot_net / tot_perfect_net * 100, 1) if tot_perfect_net > 1e-9 else None
        monthly = {
            ym: {
                "nights":      m["nights"],
                "revenue":     round(m["revenue"], 2),
                "shortfall":   round(m["shortfall"], 2),
                "net":         round(m["revenue"] - m["shortfall"], 2),
                "perfect_net": round(m["perfect_net"], 2),
                "net_capture": round(
                    (m["revenue"] - m["shortfall"]) / m["perfect_net"] * 100, 1
                ) if m["perfect_net"] > 1e-9 else None,
            }
            for ym, m in sorted(m_data.items())
        }
        output[key] = {
            "label": label,
            "total": {
                "revenue":     round(tot_rev, 2),
                "shortfall":   round(tot_short, 2),
                "net":         tot_net,
                "perfect_net": round(tot_perfect_net, 2),
                "net_capture": tot_net_capture,
            },
            "monthly": monthly,
        }
    return output


def main() -> None:
    cfg  = load_config(Path("config/config.yaml"))
    rows = load_rows(DB_PATH)

    # Rolling 12-month window anchored to the dataset's last available date, so the
    # backtest window stays a consistent 12 months instead of creeping as data grows.
    backtest_end   = last_dataset_date(DB_PATH)
    backtest_start = one_year_before(backtest_end)

    # Derive evaluation params from config (not hardcoded), so the backtest's
    # capacity/floor matches what predict() assumes for its decisions.
    params = BacktestParams.from_config(cfg, start=backtest_start, end=backtest_end)

    seasonal_medians = compute_seasonal_medians(rows, params)

    results = {}

    print("Running scenario A: Model — full-charge SoC, fixed P90...")
    results["A"] = run_model_scenario(rows, cfg, params, seasonal=False)

    print("Running scenario B: Model — full-charge SoC, seasonal Px...")
    results["B"] = run_model_scenario(rows, cfg, params, seasonal=True)

    print("Running scenario C: Baseline — 3-day rolling average...")
    results["C"] = run_rolling_scenario(rows, params, window=3)

    print("Running scenario D: Baseline — 7-day rolling average...")
    results["D"] = run_rolling_scenario(rows, params, window=7)

    print("Running scenario E: Baseline — seasonal fixed median...")
    results["E"] = run_seasonal_fixed_scenario(rows, params, seasonal_medians)

    print("Running scenario F: Model — full-charge SoC, aggressive seasonal Px...")
    results["F"] = run_model_scenario(rows, cfg, params, seasonal=False, confidence_fn=seasonal_confidence_aggressive)

    print("Running scenario H: Model — full-charge SoC, fixed P75...")
    results["H"] = run_model_scenario(rows, cfg, params, seasonal=False, confidence_fn=lambda d: 0.75)

    print("Running scenario G: Model — full-charge SoC, fixed P50...")
    results["G"] = run_model_scenario(rows, cfg, params, seasonal=False, confidence_fn=lambda d: 0.50)

    print("Running scenario I1: Blended a=0.75, buffer fixed...")
    results["I1"] = run_blended_scenario(rows, cfg, params, alpha=0.75, scale_buffer=False)
    print("Running scenario I2: Blended a=0.75, buffer scaled...")
    results["I2"] = run_blended_scenario(rows, cfg, params, alpha=0.75, scale_buffer=True)
    print("Running scenario I3: Blended a=0.50, buffer fixed...")
    results["I3"] = run_blended_scenario(rows, cfg, params, alpha=0.50, scale_buffer=False)
    print("Running scenario I4: Blended a=0.50, buffer scaled...")
    results["I4"] = run_blended_scenario(rows, cfg, params, alpha=0.50, scale_buffer=True)
    print("Running scenario I5: Blended a=0.25, buffer fixed...")
    results["I5"] = run_blended_scenario(rows, cfg, params, alpha=0.25, scale_buffer=False)
    print("Running scenario I6: Blended a=0.25, buffer scaled...")
    results["I6"] = run_blended_scenario(rows, cfg, params, alpha=0.25, scale_buffer=True)

    start_14 = backtest_end - timedelta(days=13)
    start_30 = backtest_end - timedelta(days=29)

    print("Running recent 14-day windows...")
    recent_14 = {
        "A":  run_model_scenario(rows, cfg, params, seasonal=False, start=start_14),
        "B":  run_model_scenario(rows, cfg, params, seasonal=True, start=start_14),
        "H":  run_model_scenario(rows, cfg, params, seasonal=False, confidence_fn=lambda d: 0.75, start=start_14),
        "F":  run_model_scenario(rows, cfg, params, seasonal=False, confidence_fn=seasonal_confidence_aggressive, start=start_14),
        "G":  run_model_scenario(rows, cfg, params, seasonal=False, confidence_fn=lambda d: 0.50, start=start_14),
        "C":  run_rolling_scenario(rows, params, window=3, start=start_14),
        "D":  run_rolling_scenario(rows, params, window=7, start=start_14),
        "E":  run_seasonal_fixed_scenario(rows, params, seasonal_medians, start=start_14),
        "I1": run_blended_scenario(rows, cfg, params, alpha=0.75, scale_buffer=False, start=start_14),
        "I2": run_blended_scenario(rows, cfg, params, alpha=0.75, scale_buffer=True, start=start_14),
        "I3": run_blended_scenario(rows, cfg, params, alpha=0.50, scale_buffer=False, start=start_14),
        "I4": run_blended_scenario(rows, cfg, params, alpha=0.50, scale_buffer=True, start=start_14),
        "I5": run_blended_scenario(rows, cfg, params, alpha=0.25, scale_buffer=False, start=start_14),
        "I6": run_blended_scenario(rows, cfg, params, alpha=0.25, scale_buffer=True, start=start_14),
    }

    print("Running recent 30-day windows...")
    recent_30 = {
        "A":  run_model_scenario(rows, cfg, params, seasonal=False, start=start_30),
        "B":  run_model_scenario(rows, cfg, params, seasonal=True, start=start_30),
        "H":  run_model_scenario(rows, cfg, params, seasonal=False, confidence_fn=lambda d: 0.75, start=start_30),
        "F":  run_model_scenario(rows, cfg, params, seasonal=False, confidence_fn=seasonal_confidence_aggressive, start=start_30),
        "G":  run_model_scenario(rows, cfg, params, seasonal=False, confidence_fn=lambda d: 0.50, start=start_30),
        "C":  run_rolling_scenario(rows, params, window=3, start=start_30),
        "D":  run_rolling_scenario(rows, params, window=7, start=start_30),
        "E":  run_seasonal_fixed_scenario(rows, params, seasonal_medians, start=start_30),
        "I1": run_blended_scenario(rows, cfg, params, alpha=0.75, scale_buffer=False, start=start_30),
        "I2": run_blended_scenario(rows, cfg, params, alpha=0.75, scale_buffer=True, start=start_30),
        "I3": run_blended_scenario(rows, cfg, params, alpha=0.50, scale_buffer=False, start=start_30),
        "I4": run_blended_scenario(rows, cfg, params, alpha=0.50, scale_buffer=True, start=start_30),
        "I5": run_blended_scenario(rows, cfg, params, alpha=0.25, scale_buffer=False, start=start_30),
        "I6": run_blended_scenario(rows, cfg, params, alpha=0.25, scale_buffer=True, start=start_30),
    }

    accuracy = consumption_accuracy_series(rows, cfg, params)
    html = build_html(results, recent_14, recent_30, accuracy, params, seasonal_medians)
    html_path = Path("tools/backtest_report.html")
    html_path.write_text(html, encoding="utf-8")
    print(f"Report written to {html_path}")

    json_path = Path("tools/backtest_report.json")
    json_path.write_text(json.dumps(build_json(results), indent=2), encoding="utf-8")
    print(f"JSON written to  {json_path}")


if __name__ == "__main__":
    main()
