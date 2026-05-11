"""
Backtest the four-zone model against the last year of observations.

All scenarios use full-charge SoC (GloBird overnight charging assumed).

Model scenarios:
  A) Full-charge SoC, fixed P90
  B) Full-charge SoC, seasonal Px (P95 winter / P90 shoulder / P75 summer)

Baseline scenarios (bypass model — use consumption estimate directly):
  C) 3-day rolling average consumption
  D) 7-day rolling average consumption
  E) Seasonal fixed median (Winter 12163 Wh / Shoulder 6481 Wh / Summer 5859 Wh)

Absence period nights use same calendar date one year prior.
Rates: export $0.15/kWh, grid buyback $0.28/kWh
Output: HTML report.
"""

import json
import sqlite3
from collections import deque
from datetime import date, timedelta
from pathlib import Path

from src.config import load_config
from src.model import PredictInputs, predict

DB_PATH = "data/dataset.db"
EXPORT_RATE  = 0.15   # $/kWh
BUYBACK_RATE = 0.28   # $/kWh

ABSENCE_START  = date(2025, 9, 28)
ABSENCE_END    = date(2025, 11, 3)
BACKTEST_START = date(2025, 5, 11)
BACKTEST_END   = date(2026, 5, 8)

# Seasonal medians from dataset (consumption_wh, data-gap rows excluded)
SEASONAL_FIXED_WH = {
    "winter":   12163,
    "shoulder":  6481,
    "summer":    5859,
}


def season(d: date) -> str:
    m = d.month
    if m in (6, 7, 8):         return "winter"
    if m in (11, 12, 1, 2, 3): return "summer"
    return "shoulder"


def seasonal_confidence(d: date) -> float:
    s = season(d)
    if s == "winter":   return 0.95
    if s == "summer":   return 0.75
    return 0.90


def seasonal_confidence_aggressive(d: date) -> float:
    s = season(d)
    if s == "winter":   return 0.95   # unchanged — winter is loss-making regardless
    if s == "summer":   return 0.50
    return 0.75


def load_rows(db_path: str) -> dict[str, dict]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT date, soc_at_6pm, max_soc_prev_daylight, bom_temp_mean, bom_humidity_mean,
               solcast_forecast_tomorrow_wh, consumption_wh, data_gap
        FROM daily_observations
        WHERE (data_gap = 0 OR data_gap IS NULL)
          AND soc_at_6pm IS NOT NULL
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


def accum_night(monthly: dict, ym: str, avail_wh: float, predicted_export_wh: float, actual_wh: float) -> None:
    """Record one night's economics into the monthly accumulator."""
    perfect_export = max(0.0, avail_wh - actual_wh)
    revenue        = (predicted_export_wh / 1000) * EXPORT_RATE
    shortfall_wh   = max(0.0, actual_wh - (avail_wh - predicted_export_wh))
    shortfall_cost = (shortfall_wh / 1000) * BUYBACK_RATE
    opportunity    = max(0.0, (perfect_export - predicted_export_wh) / 1000) * EXPORT_RATE
    perfect_net    = (perfect_export / 1000) * EXPORT_RATE

    monthly[ym]["revenue"]     += revenue
    monthly[ym]["shortfall"]   += shortfall_cost
    monthly[ym]["opportunity"] += opportunity
    monthly[ym]["perfect_net"] += perfect_net
    monthly[ym]["nights"]      += 1


def ensure_month(monthly: dict, ym: str) -> None:
    if ym not in monthly:
        monthly[ym] = dict(revenue=0.0, shortfall=0.0, opportunity=0.0, perfect_net=0.0, nights=0, skipped=0)


def run_model_scenario(
    rows: dict, cfg, seasonal: bool, confidence_fn=None
) -> dict[str, dict]:
    """Model-based scenario (full-charge SoC, fixed P90 or seasonal Px).

    If confidence_fn is provided it takes a date and returns a confidence float,
    overriding the seasonal/fixed logic.
    """
    monthly: dict[str, dict] = {}
    d = BACKTEST_START
    while d <= BACKTEST_END:
        ds = d.isoformat()
        ym = ds[:7]
        ensure_month(monthly, ym)

        in_absence  = ABSENCE_START <= d <= ABSENCE_END
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
        accum_night(monthly, ym, result.available_discharge_wh, result.safe_export_wh, row["consumption_wh"])
        d += timedelta(days=1)
    return monthly


def run_rolling_scenario(rows: dict, window: int) -> dict[str, dict]:
    """Baseline: rolling N-day average consumption as the estimated need."""
    monthly: dict[str, dict] = {}
    # Sorted list of all valid dates for building the rolling window
    sorted_dates = sorted(rows.keys())
    date_set     = set(sorted_dates)
    recent: deque[float] = deque()

    d = BACKTEST_START
    while d <= BACKTEST_END:
        ds = d.isoformat()
        ym = ds[:7]
        ensure_month(monthly, ym)

        # Rebuild rolling window: last `window` valid non-gap days before d
        recent.clear()
        check = d - timedelta(days=1)
        while len(recent) < window and check >= BACKTEST_START - timedelta(days=window * 2):
            cs = check.isoformat()
            if cs in date_set:
                recent.appendleft(rows[cs]["consumption_wh"])
            check -= timedelta(days=1)

        in_absence  = ABSENCE_START <= d <= ABSENCE_END
        lookup_date = (d - timedelta(days=365)).isoformat() if in_absence else ds
        row = rows.get(lookup_date)
        if row is None or len(recent) == 0:
            monthly[ym]["skipped"] += 1
            d += timedelta(days=1)
            continue

        avg_consumption_wh = sum(recent) / len(recent)
        soc        = adjusted_soc(row)
        cfg_obj    = None  # not used
        battery_wh = soc / 100.0 * 13800  # BYD 13.8 kWh
        min_soc_wh = 0.10 * 13800         # 10% min SoC
        avail_wh   = max(0.0, battery_wh - min_soc_wh)
        export_wh  = max(0.0, avail_wh - avg_consumption_wh)

        accum_night(monthly, ym, avail_wh, export_wh, row["consumption_wh"])
        d += timedelta(days=1)
    return monthly


def run_seasonal_fixed_scenario(rows: dict) -> dict[str, dict]:
    """Baseline: seasonal fixed median consumption as the estimated need."""
    monthly: dict[str, dict] = {}
    d = BACKTEST_START
    while d <= BACKTEST_END:
        ds = d.isoformat()
        ym = ds[:7]
        ensure_month(monthly, ym)

        in_absence  = ABSENCE_START <= d <= ABSENCE_END
        lookup_date = (d - timedelta(days=365)).isoformat() if in_absence else ds
        row = rows.get(lookup_date)
        if row is None:
            monthly[ym]["skipped"] += 1
            d += timedelta(days=1)
            continue

        fixed_wh  = SEASONAL_FIXED_WH[season(d)]
        soc       = adjusted_soc(row)
        battery_wh = soc / 100.0 * 13800
        min_soc_wh = 0.10 * 13800
        avail_wh   = max(0.0, battery_wh - min_soc_wh)
        export_wh  = max(0.0, avail_wh - fixed_wh)

        accum_night(monthly, ym, avail_wh, export_wh, row["consumption_wh"])
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

SEASONAL_FIXED_LABEL = {
    "06": "Winter 12.2kWh", "07": "Winter 12.2kWh", "08": "Winter 12.2kWh",
    "11": "Summer 5.9kWh",  "12": "Summer 5.9kWh",  "01": "Summer 5.9kWh",
    "02": "Summer 5.9kWh",  "03": "Summer 5.9kWh",
}


def build_html(results: dict) -> str:
    months = sorted(next(iter(results.values())).keys())

    def cell_class(val: float) -> str:
        if val > 0.5:  return "pos"
        if val < -0.5: return "neg"
        return ""

    def eff_class(val: float) -> str:
        if val >= 65: return "eff-high"
        if val >= 55: return "eff-mid"
        return "eff-low"

    scenario_tables = []
    for key, label in SCENARIOS:
        m_data   = results[key]
        is_model = key in ("A", "B", "F", "G", "H", "I")
        rows_html = []
        tot = dict(revenue=0.0, shortfall=0.0, opportunity=0.0, perfect_net=0.0, nights=0)

        for ym in months:
            m   = m_data[ym]
            mon = ym[5:7]
            net         = m["revenue"] - m["shortfall"]
            net_capture = net / m["perfect_net"] * 100 if m["perfect_net"] > 0 else 0.0

            season_tag = ""
            if key == "B":
                lbl = SEASON_LABEL.get(mon)
                if lbl:
                    season_tag = f'<span class="season-tag">{lbl}</span>'
            elif key == "F":
                sl = SEASON_LABEL_AGGRESSIVE.get(mon, "Shoulder P75")
                season_tag = f'<span class="season-tag">{sl}</span>'
            elif key == "E":
                sl  = "Winter 12.2kWh" if mon in ("06","07","08") else ("Summer 5.9kWh" if mon in ("11","12","01","02","03") else "Shoulder 6.5kWh")
                season_tag = f'<span class="season-tag">{sl}</span>'

            skipped = f'<span class="skipped"> ({m["skipped"]} skipped)</span>' if m.get("skipped") else ""
            rows_html.append(f"""
              <tr>
                <td>{MONTH_NAMES[mon]} {ym[:4]}{season_tag}</td>
                <td>{m['nights']}{skipped}</td>
                <td class="num">${m['revenue']:.2f}</td>
                <td class="num neg2">-${m['shortfall']:.2f}</td>
                <td class="num {cell_class(net)}">${net:.2f}</td>
                <td class="num {eff_class(net_capture)}">{net_capture:.1f}%</td>
              </tr>""")
            for k in ("revenue", "shortfall", "opportunity", "perfect_net", "nights"):
                tot[k] += m[k]

        tot_net         = tot["revenue"] - tot["shortfall"]
        tot_net_capture = tot_net / tot["perfect_net"] * 100 if tot["perfect_net"] > 0 else 0.0
        badge   = '<span class="model-badge">model</span>' if is_model else '<span class="baseline-badge">baseline</span>'
        scenario_tables.append(f"""
        <section>
          <h2>Scenario {key}: {label} {badge}</h2>
          <table>
            <thead>
              <tr>
                <th>Month</th><th>Nights</th><th>Revenue</th>
                <th>Shortfall</th><th>Net</th><th>Net capture</th>
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
                <td class="num {eff_class(tot_net_capture)}"><strong>{tot_net_capture:.1f}%</strong></td>
              </tr>
            </tfoot>
          </table>
        </section>""")

    # Summary comparison table — winter (Jun–Aug) excluded as loss-making in all scenarios
    summary_rows = []
    for key, label in SCENARIOS:
        m_data          = results[key]
        is_model        = key in ("A", "B", "F", "G")
        non_winter      = {ym: m for ym, m in m_data.items() if ym[5:7] not in ("06", "07", "08")}
        tot_rev         = sum(m["revenue"]     for m in non_winter.values())
        tot_short       = sum(m["shortfall"]   for m in non_winter.values())
        tot_perfect_net = sum(m["perfect_net"] for m in non_winter.values())
        tot_net         = tot_rev - tot_short
        tot_net_capture = tot_net / tot_perfect_net * 100 if tot_perfect_net > 0 else 0.0
        badge           = '<span class="model-badge">model</span>' if is_model else '<span class="baseline-badge">baseline</span>'
        summary_rows.append(f"""
          <tr>
            <td>{badge} <strong>{key}</strong> — {label}</td>
            <td class="num">${tot_rev:.2f}</td>
            <td class="num neg2">-${tot_short:.2f}</td>
            <td class="num {cell_class(tot_net)}"><strong>${tot_net:.2f}</strong></td>
            <td class="num {eff_class(tot_net_capture)}">{tot_net_capture:.1f}%</td>
          </tr>""")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Safe Export Backtest — {BACKTEST_START} to {BACKTEST_END}</title>
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
<p class="subtitle">{BACKTEST_START} to {BACKTEST_END} &nbsp;|&nbsp; {len(months)} months &nbsp;|&nbsp; Absence period uses prior-year proxy &nbsp;|&nbsp; All scenarios: full-charge SoC</p>
<p class="rates">Export rate: <strong>$0.15/kWh</strong> &nbsp;&nbsp; Grid buyback: <strong>$0.28/kWh</strong></p>

<div class="summary-section">
  <h2>Scenario summary <span style="font-size:0.8rem;font-weight:normal;color:#777">(winter Jun–Aug excluded — loss-making in all scenarios)</span></h2>
  <table>
    <thead>
      <tr><th>Scenario</th><th>Revenue</th><th>Shortfall</th><th>Net</th><th>Net capture</th></tr>
    </thead>
    <tbody>{''.join(summary_rows)}</tbody>
  </table>
  <p style="font-size:0.82rem;color:#555;margin:0.5rem 0 0">
    Seasonal Px (B): Winter (Jun–Aug) P95 &nbsp;|&nbsp; Summer (Nov–Mar) P75 &nbsp;|&nbsp; Shoulder P90<br>
    Aggressive Px (F): Winter P95 &nbsp;|&nbsp; Shoulder P75 &nbsp;|&nbsp; Summer P50<br>
    Seasonal fixed (E): Winter 12,163 Wh &nbsp;|&nbsp; Shoulder 6,481 Wh &nbsp;|&nbsp; Summer 5,859 Wh &nbsp;(dataset medians)<br>
    Net capture colour: <span style="color:#1a7a1a;font-weight:600">≥65%</span> &nbsp;|&nbsp; <span style="color:#b06000">≥55%</span> &nbsp;|&nbsp; <span style="color:#c0392b">&lt;55%</span>
  </p>
</div>

{''.join(scenario_tables)}

<p class="note">
  <strong>Net capture</strong> = net &divide; perfect net &mdash; what fraction of the best possible outcome (hindsight-perfect export, zero shortfall) was actually achieved.
  Unlike a raw efficiency metric, this accounts for the rate asymmetry: shortfall costs $0.28/kWh to cover while missed export only foregoes $0.15/kWh.
  A scenario that exports aggressively and incurs heavy shortfall will score lower than one that is more conservative, even if its revenue is higher.<br>
  <strong>Perfect net</strong> = what a hindsight-perfect model would earn: export exactly <code>max(0, available &minus; actual)</code> every night, zero shortfall.<br>
  <strong>Full-charge SoC</strong> = 6pm SoC adjusted upward by however short of 100% the prior day&rsquo;s peak fell, simulating GloBird overnight charging.<br>
  <strong>Baseline battery maths</strong>: available = SoC% &times; 13.8 kWh &minus; 10% min SoC; export = max(0, available &minus; estimated consumption).
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
        tot_net_capture = round(tot_net / tot_perfect_net * 100, 1) if tot_perfect_net > 0 else 0.0
        monthly = {
            ym: {
                "nights":      m["nights"],
                "revenue":     round(m["revenue"], 2),
                "shortfall":   round(m["shortfall"], 2),
                "net":         round(m["revenue"] - m["shortfall"], 2),
                "perfect_net": round(m["perfect_net"], 2),
                "net_capture": round(
                    (m["revenue"] - m["shortfall"]) / m["perfect_net"] * 100, 1
                ) if m["perfect_net"] > 0 else 0.0,
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

    results = {}

    print("Running scenario A: Model — full-charge SoC, fixed P90...")
    results["A"] = run_model_scenario(rows, cfg, seasonal=False)

    print("Running scenario B: Model — full-charge SoC, seasonal Px...")
    results["B"] = run_model_scenario(rows, cfg, seasonal=True)

    print("Running scenario C: Baseline — 3-day rolling average...")
    results["C"] = run_rolling_scenario(rows, window=3)

    print("Running scenario D: Baseline — 7-day rolling average...")
    results["D"] = run_rolling_scenario(rows, window=7)

    print("Running scenario E: Baseline — seasonal fixed median...")
    results["E"] = run_seasonal_fixed_scenario(rows)

    print("Running scenario F: Model — full-charge SoC, aggressive seasonal Px...")
    results["F"] = run_model_scenario(rows, cfg, seasonal=False, confidence_fn=seasonal_confidence_aggressive)

    print("Running scenario H: Model — full-charge SoC, fixed P75...")
    results["H"] = run_model_scenario(rows, cfg, seasonal=False, confidence_fn=lambda d: 0.75)

    print("Running scenario G: Model — full-charge SoC, fixed P50...")
    results["G"] = run_model_scenario(rows, cfg, seasonal=False, confidence_fn=lambda d: 0.50)

    html = build_html(results)
    html_path = Path("tools/backtest_report.html")
    html_path.write_text(html, encoding="utf-8")
    print(f"Report written to {html_path}")

    json_path = Path("tools/backtest_report.json")
    json_path.write_text(json.dumps(build_json(results), indent=2), encoding="utf-8")
    print(f"JSON written to  {json_path}")


if __name__ == "__main__":
    main()
