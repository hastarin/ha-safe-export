# TODO — cum-delta window boundary bug fix

Status: **diagnosis complete, fix not yet applied.** Read this whole file before starting.

## TL;DR

The extraction reads the wrong hourly buckets when computing energy deltas for the 4
cumulative-sum sensors (grid import, grid export, battery charged, battery discharged).
Every energy column in the dataset is computed over a window shifted one hour too late.
The fix is a 4-line change in `src/extract.py`, plus doc updates, a dataset rebuild,
fixture re-verification, and (separately, later) model retraining.

## The bug

`sum @ start_ts = T` in HA's `statistics` table is the cumulative meter reading at the
**END** of bucket `[T, T+1h)` — i.e. the reading at time `T+1h`. (DATASET.md currently
claims it is the reading at the *start* of the bucket — that statement is **wrong**.)

Therefore, to read the cumulative value at a window boundary you must read the bucket
labelled **one hour earlier**:

- cumulative at 18:00 (window start) = `sum @ start_ts = 17:00` = `ts_17_prior`
- cumulative at 11:00 (window end)   = `sum @ start_ts = 10:00` = `ts_10_today`

The current code in [src/extract.py:110-113](src/extract.py#L110-L113) uses
`w.ts_18_prior` and `w.ts_11_today`, which read the cumulative value at **19:00 prior**
and **12:00 today**. That means every row's energy delta is computed over
`19:00 prior → 12:00 today` instead of the correct `18:00 prior → 11:00 today`:
it **misses the 18:00–19:00 hour** and **wrongly includes the 11:00–12:00 hour**.

### How it was found

Backtest showed shortfalls on nights the user knew were fine. Traced to `grid_import_wh`
= 2,584 Wh for the night of 19→20 May 2026, when the HA energy dashboard showed only
~43 Wh of real grid import in the window. The 2,541 Wh discrepancy was a grid spike that
actually occurred in the **11:00–12:00** hour (battery charging after the GloBird free
window opens at 11am) — outside the window, but pulled in by the off-by-one end boundary.

### Evidence (do not re-derive from scratch; this is verified)

Empirical confirmation, grid-consumed sensor (`metadata_id = 251`), 19 May 2026 AEST:
the delta between the 16:00 and 17:00 buckets' `sum` was +5 Wh, and the user confirmed
from the HA chart that 5 Wh was consumed during **17:00–18:00** — i.e. the energy for
hour `[T, T+1h)` lands in the delta ending at the `T+1h` bucket, proving `sum @ T` =
reading at end of bucket. (Note HA was set to UTC; Melbourne was AEST = UTC+10 in May.)

Worked impact examples (current → fixed):
- **20 May 2026** (AEST) grid_import: 2,584 Wh → **43 Wh** (huge — anomaly in 11–12 bucket)
- **20 Mar 2026** (AEDT) grid_import:   772 Wh → **768 Wh** (tiny — quiet boundary)

The bug's magnitude is small on most nights but large whenever there is significant grid
or battery activity in the 11:00–12:00 hour — which is now common under GloBird midday
charging. So it matters most for recent GloBird-era data, which is what we care about.

## The fix

### 1. src/extract.py (the actual bug)

Change the 4 cum-delta calls at [src/extract.py:110-113](src/extract.py#L110-L113) from
`w.ts_18_prior, w.ts_11_today` to `w.ts_17_prior, w.ts_10_today`:

```python
grid_import_wh        = _cum_delta(ha, ids["grid_import"],        w.ts_17_prior, w.ts_10_today)
grid_export_wh        = _cum_delta(ha, ids["grid_export"],        w.ts_17_prior, w.ts_10_today)
battery_charged_wh    = _cum_delta(ha, ids["battery_charged"],    w.ts_17_prior, w.ts_10_today)
battery_discharged_wh = _cum_delta(ha, ids["battery_discharged"], w.ts_17_prior, w.ts_10_today)
```

`ts_17_prior` and `ts_10_today` already exist in `DayWindows` (used for soc_at_6pm /
soc_at_11am) — no change to `windows.py` logic needed, only its comments.

**Do NOT change the mean/min/max aggregations.** Those use
`start_ts >= ts_18_prior AND start_ts <= ts_10_today`, and for mean sensors the bucket
label IS the start of the period it averages, so that range correctly spans 18:00→11:00.
Only the cumulative `sum`-delta reads are wrong. (solar_wh, consumption_wh_load, all
weather/SoC aggregations are fine and must stay as-is.)

### 2. src/windows.py — fix the now-misleading comments

- `ts_18_prior`: currently says "cum-delta start" — it is NOT. It's only the agg lower bound.
- `ts_11_today`: currently says "cum-delta end" — it is NOT used at all after the fix.
- `ts_17_prior` / `ts_10_today`: note these are ALSO the cum-delta start/end.

Consider whether `ts_11_today` is still referenced anywhere after the fix; if not, it can
be removed from `DayWindows` (and `windows_for_date`). Grep first.

### 3. docs/DATASET.md — correct the wrong convention + formula

- The "HA hourly bucket convention" section states `sum @ start_ts=18:00` is the reading
  "immediately at the start of bucket [18:00, 19:00)". **This is wrong** — it's the reading
  at the *end* (19:00). Rewrite this.
- The formula `window_energy = sum_at(start_ts = 11:00) − sum_at(start_ts = 18:00)` must
  become `sum_at(start_ts = 10:00 row date) − sum_at(start_ts = 17:00 prior day)`.
- The per-column formula rows for grid_import_wh / grid_export_wh / battery_charged_wh /
  battery_discharged_wh (all say "@ 11:00 − @ 18:00 prior") must be updated to
  "@ 10:00 row date − @ 17:00 prior".

### 4. docs/DECISIONS.md — add a new entry

Add a locked decision documenting the cum-delta boundary convention (sum is end-of-bucket;
read one hour earlier for cumulative boundaries). Note it supersedes the wrong statement
that was in DATASET.md. Cross-reference the existing "Value at 6pm = mean of 17:00 bucket"
decision — that one is for *mean* sensors and remains correct; this new one is the
*cumulative* analogue. Bump schema/changelog as appropriate.

### 5. Rebuild the dataset

`.venv/Scripts/python -m src.extract data/home-assistant_v2.db --rebuild`

Note: `extract_all` currently writes `schema_version = "1.3.0"` in extraction_meta (line
~372) even though migrations go to 1.4.0 — pre-existing inconsistency, check/fix while here.

### 6. Re-verify and update fixtures

The 3 fixtures in [tests/fixtures.py](tests/fixtures.py) and the matching tables in
DATASET.md § Validation samples were computed with the BROKEN boundaries, so their energy
columns (grid_import_wh, grid_export_wh, battery_charged_wh, battery_discharged_wh,
consumption_wh) are wrong and the tests will fail after the fix.

For EACH fixture date (2026-02-07, 2026-03-20, 2025-07-17): query the HA DB at the
corrected boundaries, confirm the new values are sane against the energy balance, and
update BOTH tests/fixtures.py AND DATASET.md. The non-energy columns (SoC, temps,
humidity, solcast) are unaffected — only the 4 cum-delta columns + consumption_wh change.
Watch DST: Feb/Mar are AEDT (UTC+11), Jul is AEST (UTC+10).

`pytest` must pass before considering the fix done.

## Downstream consequences (SEPARATE follow-up, discuss before doing)

- **Model retraining.** The model coefficients + percentile tables in `config.yaml` were
  fit on the wrong `consumption_wh`. After the rebuild they should be retrained. This is a
  separate task — do not bundle it into the boundary fix. Flag it and discuss scope.
- **Backtest re-run.** All prior backtest numbers (and the tables in DECISIONS.md's
  "Backtest results" / "Backtest v2" entries) are based on the wrong data. Re-run after
  the rebuild + retrain. Don't trust any existing backtest figure until then.

## Backtest work that was IN PROGRESS when the bug was found (do NOT lose)

While investigating, we had already added (uncommitted, in `tools/backtest.py`):
1. `BACKTEST_END` moved from 2026-05-08 to **2026-05-20**.
2. Six **blended scenarios** I1–I6: consumption = α·model + (1−α)·3-day-rolling-avg, for
   α ∈ {0.75, 0.50, 0.25}, each in buffer-fixed and buffer-scaled variants. Added
   `run_blended_scenario()` + SCENARIOS entries + main() wiring.
3. **Recent-window sections**: 14-day and 30-day summary tables at the top of the HTML
   report. Added `start` param to all 4 run_* functions, `_recent_summary_rows()` helper,
   and the two new HTML sections in `build_html(results, recent_14, recent_30)`.

Open design question that was being discussed (still unresolved): the backtest's shortfall
& perfect-export maths. We were debating whether `actual_wh` should be total home
consumption (`consumption_wh`) vs **net battery draw** (`battery_discharged_wh −
battery_charged_wh`). The user's stated goal: **the battery alone must carry the house to
11am with NO grid draw in the window**. Resolve this AFTER the data is correct — the bad
data is what derailed the previous discussion. Also wanted: use actual `min_soc_overnight`
rather than a hardcoded 10% floor, and sort summary tables by net capture descending, and
reconsider the "perfect" benchmark (maybe min_soc + 10% headroom rather than draining to
exactly min_soc). All of that is downstream of correct data — do not start until the fix +
rebuild + retrain are done.

## Suggested order of work

1. extract.py fix (4 lines) → windows.py comments → DATASET.md → DECISIONS.md
2. `--rebuild`
3. Re-verify + update the 3 fixtures (HA DB queries) → `pytest` green
4. STOP. Discuss model retraining scope with the user.
5. Retrain model → re-run backtest → revisit backtest maths design questions.
