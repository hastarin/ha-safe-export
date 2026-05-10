"""
Backtest the P90 four-zone model against the last year of observations.

Runs four scenarios side by side:
  A) Actual SoC,       fixed P90
  B) Full-charge SoC,  fixed P90
  C) Actual SoC,       seasonal confidence (P95 winter / P90 shoulder / P75 summer)
  D) Full-charge SoC,  seasonal confidence

Absence period nights use same calendar date one year prior.
Rates: export $0.15/kWh, grid buyback $0.28/kWh
Output: HTML report with monthly tables for all four scenarios.
"""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from src.config import load_config
from src.model import PredictInputs, predict

DB_PATH = "data/dataset.db"
EXPORT_RATE  = 0.15   # $/kWh
BUYBACK_RATE = 0.28   # $/kWh

ABSENCE_START = date(2025, 9, 28)
ABSENCE_END   = date(2025, 11, 3)
BACKTEST_START = date(2025, 5, 11)
BACKTEST_END   = date(2026, 5, 8)

# Seasonal confidence: month -> confidence level
def seasonal_confidence(d: date) -> float:
    m = d.month
    if m in (6, 7, 8):        return 0.95   # winter
    if m in (11, 12, 1, 2, 3): return 0.75  # summer
    return 0.90                               # shoulder (Apr, May, Sep, Oct)


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


def run_scenario(rows: dict, cfg, full_charge: bool, seasonal: bool) -> dict[str, dict]:
    """Return monthly accumulators for one scenario."""
    monthly: dict[str, dict] = {}
    d = BACKTEST_START
    while d <= BACKTEST_END:
        ds = d.isoformat()
        ym = ds[:7]
        if ym not in monthly:
            monthly[ym] = dict(revenue=0.0, shortfall=0.0, opportunity=0.0, nights=0, skipped=0)

        in_absence = ABSENCE_START <= d <= ABSENCE_END
        lookup_date = (d - timedelta(days=365)).isoformat() if in_absence else ds
        row = rows.get(lookup_date)
        if row is None:
            monthly[ym]["skipped"] += 1
            d += timedelta(days=1)
            continue

        soc      = row["soc_at_6pm"]
        max_soc  = row["max_soc_prev_daylight"]
        if full_charge and max_soc is not None and max_soc < 100.0:
            soc = min(100.0, soc + (100.0 - max_soc))

        confidence = seasonal_confidence(d) if seasonal else 0.90

        inp = PredictInputs(
            soc_at_6pm=soc,
            bom_temp_mean=row["bom_temp_mean"],
            bom_humidity_mean=row["bom_humidity_mean"],
            solcast_forecast_tomorrow_wh=row["solcast_forecast_tomorrow_wh"],
            confidence=confidence,
        )
        result   = predict(inp, cfg)
        avail_wh = result.available_discharge_wh
        p_export = result.safe_export_wh
        actual_wh = row["consumption_wh"]

        perfect_export = max(0.0, avail_wh - actual_wh)
        revenue        = (p_export / 1000) * EXPORT_RATE
        shortfall_wh   = max(0.0, actual_wh - (avail_wh - p_export))
        shortfall_cost = (shortfall_wh / 1000) * BUYBACK_RATE
        opportunity    = max(0.0, (perfect_export - p_export) / 1000) * EXPORT_RATE

        monthly[ym]["revenue"]     += revenue
        monthly[ym]["shortfall"]   += shortfall_cost
        monthly[ym]["opportunity"] += opportunity
        monthly[ym]["nights"]      += 1
        d += timedelta(days=1)
    return monthly


SCENARIOS = [
    ("A", "Actual SoC, fixed P90",        False, False),
    ("B", "Full-charge SoC, fixed P90",   True,  False),
    ("C", "Actual SoC, seasonal Px",      False, True),
    ("D", "Full-charge SoC, seasonal Px", True,  True),
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


def build_html(results: dict) -> str:
    months = sorted(next(iter(results.values())).keys())

    def cell_class(val: float) -> str:
        if val > 0.5:  return "pos"
        if val < -0.5: return "neg"
        return ""

    def eff_class(val: float) -> str:
        if val >= 65: return "eff-high"
        if val >= 40: return "eff-mid"
        return "eff-low"

    scenario_tables = []
    for key, label, full_charge, seasonal in SCENARIOS:
        m_data = results[key]
        rows_html = []
        tot = dict(revenue=0.0, shortfall=0.0, opportunity=0.0, nights=0)

        for ym in months:
            m = m_data[ym]
            mon = ym[5:7]
            net = m["revenue"] - m["shortfall"]
            eff = m["revenue"] / (m["revenue"] + m["opportunity"]) * 100 if (m["revenue"] + m["opportunity"]) > 0 else 0.0
            season_tag = ""
            if seasonal:
                lbl = SEASON_LABEL.get(mon)
                if lbl:
                    season_tag = f'<span class="season-tag">{lbl}</span>'
            skipped = f'<span class="skipped"> ({m["skipped"]} skipped)</span>' if m["skipped"] else ""
            rows_html.append(f"""
              <tr>
                <td>{MONTH_NAMES[mon]} {ym[:4]}{season_tag}</td>
                <td>{m['nights']}{skipped}</td>
                <td class="num">${m['revenue']:.2f}</td>
                <td class="num neg2">-${m['shortfall']:.2f}</td>
                <td class="num {cell_class(net)}">${net:.2f}</td>
                <td class="num">${m['opportunity']:.2f}</td>
                <td class="num {eff_class(eff)}">{eff:.1f}%</td>
              </tr>""")
            for k in tot: tot[k] += m[k]

        tot_net = tot["revenue"] - tot["shortfall"]
        tot_eff = tot["revenue"] / (tot["revenue"] + tot["opportunity"]) * 100 if (tot["revenue"] + tot["opportunity"]) > 0 else 0.0
        scenario_tables.append(f"""
        <section>
          <h2>Scenario {key}: {label}</h2>
          <table>
            <thead>
              <tr>
                <th>Month</th><th>Nights</th><th>Revenue</th>
                <th>Shortfall</th><th>Net</th><th>Opp. gap</th><th>Efficiency</th>
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
                <td class="num"><strong>${tot['opportunity']:.2f}</strong></td>
                <td class="num {eff_class(tot_eff)}"><strong>{tot_eff:.1f}%</strong></td>
              </tr>
            </tfoot>
          </table>
        </section>""")

    # Summary comparison table
    summary_rows = []
    for key, label, _, _ in SCENARIOS:
        m_data = results[key]
        tot_rev = sum(m["revenue"] for m in m_data.values())
        tot_short = sum(m["shortfall"] for m in m_data.values())
        tot_opp = sum(m["opportunity"] for m in m_data.values())
        tot_net = tot_rev - tot_short
        tot_eff = tot_rev / (tot_rev + tot_opp) * 100 if (tot_rev + tot_opp) > 0 else 0.0
        summary_rows.append(f"""
          <tr>
            <td><strong>{key}</strong> — {label}</td>
            <td class="num">${tot_rev:.2f}</td>
            <td class="num neg2">-${tot_short:.2f}</td>
            <td class="num {cell_class(tot_net)}"><strong>${tot_net:.2f}</strong></td>
            <td class="num">${tot_opp:.2f}</td>
            <td class="num {eff_class(tot_eff)}">{tot_eff:.1f}%</td>
          </tr>""")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Safe Export Backtest — {BACKTEST_START} to {BACKTEST_END}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 860px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
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
  .summary-section {{ background: #f7f7f7; border: 1px solid #ddd; border-radius: 6px;
                       padding: 1rem 1.2rem; margin-bottom: 2rem; }}
  .summary-section h2 {{ border-bottom-color: #bbb; }}
  .rates {{ font-size: 0.85rem; color: #555; margin-bottom: 1.5rem; }}
  .note {{ font-size: 0.82rem; color: #666; margin-top: 1.5rem; border-top: 1px solid #eee; padding-top: 0.75rem; }}
</style>
</head>
<body>
<h1>Safe Export Backtest</h1>
<p class="subtitle">{BACKTEST_START} to {BACKTEST_END} &nbsp;|&nbsp; {len(months)} months &nbsp;|&nbsp; Absence period uses prior-year proxy</p>
<p class="rates">Export rate: <strong>$0.15/kWh</strong> &nbsp;&nbsp; Grid buyback: <strong>$0.28/kWh</strong></p>

<div class="summary-section">
  <h2>Scenario summary</h2>
  <table>
    <thead>
      <tr><th>Scenario</th><th>Revenue</th><th>Shortfall</th><th>Net</th><th>Opp. gap</th><th>Efficiency</th></tr>
    </thead>
    <tbody>{''.join(summary_rows)}</tbody>
  </table>
  <p style="font-size:0.82rem;color:#555;margin:0">
    Seasonal Px: Winter (Jun–Aug) P95 &nbsp;|&nbsp; Summer (Nov–Mar) P75 &nbsp;|&nbsp; Shoulder P90
  </p>
</div>

{''.join(scenario_tables)}

<p class="note">
  <strong>Efficiency</strong> = revenue &divide; (revenue + opportunity gap) — how much of the theoretically perfect export the model captured.<br>
  <strong>Opportunity gap</strong> = what a perfect hindsight model would have earned above what P-x recommended, at $0.15/kWh.<br>
  <strong>Full-charge SoC</strong> = 6pm SoC adjusted upward by however short of 100% the prior day&rsquo;s peak fell, simulating GloBird overnight charging.
</p>
</body>
</html>"""


def main() -> None:
    cfg  = load_config(Path("config/config.yaml"))
    rows = load_rows(DB_PATH)

    results = {}
    for key, label, full_charge, seasonal in SCENARIOS:
        print(f"Running scenario {key}: {label}...")
        results[key] = run_scenario(rows, cfg, full_charge, seasonal)

    html = build_html(results)
    out_path = Path("tools/backtest_report.html")
    out_path.write_text(html, encoding="utf-8")
    print(f"\nReport written to {out_path}")


if __name__ == "__main__":
    main()
