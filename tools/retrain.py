"""Refit the four-zone consumption model from the dataset DB.

Reproduces the methodology recorded in DECISIONS.md against the current
`daily_observations` table and prints:

  1. Held-out validation (every 5th night per zone -> test): coefficients fit on
     the train split, violation rates on the test split. Confirms the model still
     meets the <=5% violation target after a data change.
  2. Proposed config.yaml `model:` block, with coefficients / percentiles / P95
     buffers refit on ALL trainable nights (the deployment fit).
  3. confidence_scale drift: the P50/P75/P90-over-P95 residual ratios that back
     the constants in src/model.py, recomputed from the heating-zone residuals.

This is a dev/analysis tool. It writes nothing — copy the proposed block into
config.yaml yourself after reviewing. Requires the `tools` extra (numpy):

    .venv/Scripts/python -m pip install -e ".[tools]"
    .venv/Scripts/python -m tools.retrain
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import numpy as np

DB_PATH = "data/dataset.db"

# Zone boundaries on bom_temp_mean (must match src/model.py exactly).
HEATING_MAX = 17.0   # temp <  17        -> heating
WARM_MAX = 19.0      # 17 <= temp < 19   -> warm boundary
MILD_MAX = 21.0      # 19 <= temp <= 21  -> mild;  temp > 21 -> cooling

TEST_EVERY = 5       # hold out every Nth night per zone (sorted by date)


@dataclass
class Row:
    date: str
    temp: float
    consumption_kwh: float
    solcast_kwh: float | None
    humidity: float | None


def load_rows(db_path: str) -> list[Row]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    raw = conn.execute(
        """
        SELECT date, bom_temp_mean, consumption_wh,
               solcast_forecast_tomorrow_wh, bom_humidity_mean
        FROM daily_observations
        WHERE absence_period = 0
          AND (data_gap = 0 OR data_gap IS NULL)
          AND consumption_wh IS NOT NULL
          AND bom_temp_mean IS NOT NULL
        ORDER BY date
        """
    ).fetchall()
    conn.close()
    return [
        Row(
            date=r["date"],
            temp=r["bom_temp_mean"],
            consumption_kwh=r["consumption_wh"] / 1000.0,
            solcast_kwh=(
                r["solcast_forecast_tomorrow_wh"] / 1000.0
                if r["solcast_forecast_tomorrow_wh"] is not None
                else None
            ),
            humidity=r["bom_humidity_mean"],
        )
        for r in raw
    ]


def zone_of(temp: float) -> str:
    if temp < HEATING_MAX:
        return "heating"
    if temp < WARM_MAX:
        return "warm_boundary"
    if temp <= MILD_MAX:
        return "mild"
    return "cooling"


def split_train_test(rows: list[Row]) -> tuple[list[Row], list[Row]]:
    """Every TEST_EVERY-th night (sorted by date) -> test; rest -> train."""
    rows = sorted(rows, key=lambda r: r.date)
    train, test = [], []
    for i, r in enumerate(rows):
        (test if i % TEST_EVERY == TEST_EVERY - 1 else train).append(r)
    return train, test


def ols(x_cols: list[np.ndarray], y: np.ndarray) -> tuple[np.ndarray, float]:
    """Fit y = b0 + b1*x1 + ... Return (coefs incl. intercept first, R^2)."""
    a = np.column_stack([np.ones(len(y)), *x_cols])
    coefs, *_ = np.linalg.lstsq(a, y, rcond=None)
    resid = y - a @ coefs
    ss_res = float(np.sum(resid**2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return coefs, r2


def percentiles(values: np.ndarray) -> dict[str, float]:
    p = np.percentile(values, [50, 75, 90, 95])
    return {"p50": p[0], "p75": p[1], "p90": p[2], "p95": p[3]}


# ---------------------------------------------------------------------------
# Per-zone fitting
# ---------------------------------------------------------------------------


def fit_heating_like(rows: list[Row], predictor: str) -> dict:
    """Fit an OLS zone. predictor is 'solcast_kwh' or 'humidity'.

    Returns coefs for the primary (temp + predictor) and temp-only models, plus
    the P95 buffer from the primary model's |residual| distribution.
    """
    temp = np.array([r.temp for r in rows])
    y = np.array([r.consumption_kwh for r in rows])

    # temp-only: all rows in the zone
    (b0_t, b1_t), r2_t = ols([temp], y)

    # primary: only rows where the second predictor is present
    has = [getattr(r, predictor) is not None for r in rows]
    temp_p = temp[has]
    y_p = y[has]
    p_vals = np.array(
        [getattr(r, predictor) for r in rows if getattr(r, predictor) is not None]
    )
    (b0, b_temp, b_pred), r2 = ols([temp_p, p_vals], y_p)

    resid = np.abs(y_p - (b0 + b_temp * temp_p + b_pred * p_vals))
    buffer_p95 = float(np.percentile(resid, 95))

    return {
        "primary": (b0, b_temp, b_pred),
        "primary_r2": r2,
        "primary_n": int(has.count(True)) if isinstance(has, list) else int(sum(has)),
        "temp_only": (b0_t, b1_t),
        "temp_only_r2": r2_t,
        "temp_only_n": len(rows),
        "buffer_p95": buffer_p95,
        "resid_pctls": percentiles(resid),
    }


def predict_kwh_heating(coefs_primary, coefs_temp_only, r: Row, predictor: str) -> float:
    val = getattr(r, predictor)
    if val is not None:
        b0, bt, bp = coefs_primary
        return b0 + bt * r.temp + bp * val
    b0, bt = coefs_temp_only
    return b0 + bt * r.temp


def violation_rate(test: list[Row], pred_fn, buffer: float) -> tuple[float, int, int]:
    """Fraction of test nights where actual > predicted + buffer."""
    viol = 0
    for r in test:
        budget = pred_fn(r) + buffer
        if r.consumption_kwh > budget:
            viol += 1
    n = len(test)
    return (viol / n if n else float("nan")), viol, n


def empirical_violation(test: list[Row], threshold: float) -> tuple[float, int, int]:
    viol = sum(1 for r in test if r.consumption_kwh > threshold)
    n = len(test)
    return (viol / n if n else float("nan")), viol, n


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def main() -> None:
    rows = load_rows(DB_PATH)
    by_zone: dict[str, list[Row]] = {"heating": [], "warm_boundary": [], "mild": [], "cooling": []}
    for r in rows:
        by_zone[zone_of(r.temp)].append(r)

    print(f"Trainable nights: {len(rows)} "
          f"(heating {len(by_zone['heating'])}, warm {len(by_zone['warm_boundary'])}, "
          f"mild {len(by_zone['mild'])}, cooling {len(by_zone['cooling'])})\n")

    # ---- Held-out validation ------------------------------------------------
    print("=" * 70)
    print("HELD-OUT VALIDATION (fit on train, violation rate on test)")
    print("=" * 70)

    for zone, predictor in (("heating", "solcast_kwh"), ("cooling", "humidity")):
        train, test = split_train_test(by_zone[zone])
        fit = fit_heating_like(train, predictor)

        def pred_fn(r, f=fit, p=predictor):
            return predict_kwh_heating(f["primary"], f["temp_only"], r, p)

        rate, viol, n = violation_rate(test, pred_fn, fit["buffer_p95"])
        print(f"\n[{zone}] train={len(train)} test={n}")
        print(f"  primary  (temp+{predictor}): R2={fit['primary_r2']:.3f} n={fit['primary_n']}")
        print(f"  temp_only:                   R2={fit['temp_only_r2']:.3f} n={fit['temp_only_n']}")
        print(f"  P95 buffer (train resid):    {fit['buffer_p95']:.3f} kWh")
        print(f"  violation @ P95 buffer:      {rate*100:.1f}%  ({viol}/{n})   target <=5%")

    for zone in ("warm_boundary", "mild"):
        train, test = split_train_test(by_zone[zone])
        pct = percentiles(np.array([r.consumption_kwh for r in train]))
        print(f"\n[{zone}] train={len(train)} test={len(test)}  (empirical percentile table)")
        for lvl in ("p50", "p75", "p90", "p95"):
            rate, viol, n = empirical_violation(test, pct[lvl])
            print(f"  {lvl}={pct[lvl]:.3f} kWh -> violation {rate*100:.1f}% ({viol}/{n})")

    # ---- Deployment fit (all trainable nights) ------------------------------
    print("\n" + "=" * 70)
    print("PROPOSED config.yaml model: block  (fit on ALL trainable nights)")
    print("=" * 70)

    heat = fit_heating_like(by_zone["heating"], "solcast_kwh")
    cool = fit_heating_like(by_zone["cooling"], "humidity")
    warm = percentiles(np.array([r.consumption_kwh for r in by_zone["warm_boundary"]]))
    mild = percentiles(np.array([r.consumption_kwh for r in by_zone["mild"]]))

    hb0, hbt, hbs = heat["primary"]
    ht0, htt = heat["temp_only"]
    cb0, cbt, cbh = cool["primary"]
    ct0, ctt = cool["temp_only"]

    print(f"""
model:
  heating_intercept: {hb0:.4f}
  heating_b_temp: {hbt:.4f}
  heating_b_solcast: {hbs:.6f}
  heating_temp_only_intercept: {ht0:.4f}
  heating_temp_only_b_temp: {htt:.4f}
  cooling_intercept: {cb0:.4f}
  cooling_b_temp: {cbt:.4f}
  cooling_b_humidity: {cbh:.6f}
  cooling_temp_only_intercept: {ct0:.4f}
  cooling_temp_only_b_temp: {ctt:.4f}
  mild_p50: {mild['p50']:.3f}
  mild_p75: {mild['p75']:.3f}
  mild_p90: {mild['p90']:.3f}
  mild_p95: {mild['p95']:.3f}
  warm_boundary_p50: {warm['p50']:.3f}
  warm_boundary_p75: {warm['p75']:.3f}
  warm_boundary_p90: {warm['p90']:.3f}
  warm_boundary_p95: {warm['p95']:.3f}
  heating_p95_buffer_kwh: {heat['buffer_p95']:.3f}
  cooling_p95_buffer_kwh: {cool['buffer_p95']:.3f}""")

    print(f"\n  (heating primary R2={heat['primary_r2']:.3f} n={heat['primary_n']}, "
          f"temp_only R2={heat['temp_only_r2']:.3f} n={heat['temp_only_n']})")
    print(f"  (cooling primary R2={cool['primary_r2']:.3f} n={cool['primary_n']}, "
          f"temp_only R2={cool['temp_only_r2']:.3f} n={cool['temp_only_n']})")

    # ---- confidence_scale drift --------------------------------------------
    print("\n" + "=" * 70)
    print("confidence_scale check (heating residual percentiles / P95)")
    print("=" * 70)
    rp = heat["resid_pctls"]
    p95 = rp["p95"]
    print("  current src/model.py: {0.50: 0.31, 0.75: 0.58, 0.90: 0.87, 0.95: 1.00}")
    print(f"  recomputed:           {{0.50: {rp['p50']/p95:.2f}, 0.75: {rp['p75']/p95:.2f}, "
          f"0.90: {rp['p90']/p95:.2f}, 0.95: 1.00}}")


if __name__ == "__main__":
    main()
