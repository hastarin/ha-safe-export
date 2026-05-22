# DECISIONS.md

This document records the _why_ behind each significant design choice in the project. Its purpose is to prevent regression: future agents (or future-you) should not undo a decision listed as **Locked** without first proposing a change, citing new evidence, and getting explicit agreement.

Each entry has the form:

> **Decision:** Brief statement of what was chosen.
> **Status:** Locked / Open / Superseded.
> **Rationale, alternatives, evidence** as needed.

---

## Data architecture

### Add BOM weather and Solcast forecast features (v1.1.0)

**Decision:** Extend the dataset with 9 additional columns: BOM weather station data (temp, feels-like, rain, wind, gust), Solcast PV forecast, median indoor temperature, and max temperature.
**Status:** Locked.
**Date:** 2026-05-07

**Rationale:** The original dataset (v1.0.0) captured overnight consumption and solar generation but lacked direct weather features beyond outdoor/indoor temperature. Adding:

- **BOM station data** provides more granular weather context (wind affects heat loss, rain affects morning solar)
- **Solcast forecast** is what the model will consume at inference time — including it in training data allows calibration of forecast vs actual
- **Median indoor temp** is more representative of whole-home climate than a single bedroom sensor
- **Max temperature** captures peak cooling load potential

**Coverage:** BOM sensors available from April 2023 (full dataset coverage). Solcast from Oct 2024 (NULL before). Median temp from Jan 2024 (NULL before). The model can handle partial coverage via NULL-aware training.

**Implementation note:** The weather rain sensor and Solcast sensor store values in `state` only — `mean`/`min`/`max` are NULL in HA statistics. Rain uses `MAX(CAST(state AS REAL))`; Solcast reads `state` at the 17:00 bucket on the prior day.

**Evidence:** All sensors verified present in HA statistics table with sufficient history.

---

### Add humidity features (v1.2.0)

**Decision:** Extend the dataset with 3 additional columns: `bom_humidity_mean`, `bom_humidity_max` (from the configured weather humidity sensor), and `median_indoor_humidity` (from the configured median humidity sensor).
**Status:** Locked.
**Date:** 2026-05-07

**Rationale:** The ENERGY_ANALYSIS.md document identified humidity as the primary unexplained variance in the cooling model (Zone 3, R²=0.38). AC load is driven by apparent comfort, which depends on both temperature and humidity. Having humidity data starting from this point will allow the cooling model to be re-evaluated once enough hot-night data accumulates.

**Coverage:** The weather humidity sensor should have full dataset coverage if sourced from a BOM-type integration. The median humidity sensor is NULL before the sensor was introduced — same boundary as `median_indoor_temp`, no new NULL region introduced.

**Implementation note:** Both sensors store values in `mean`/`min`/`max` normally (unlike rain and Solcast which use `state` only). `bom_humidity_mean` uses `AVG(mean)`, `bom_humidity_max` uses `MAX(max)`, `median_indoor_humidity` uses `AVG(mean)` — all over the standard 6pm–11am overnight window.

**Evidence:** Both sensors verified present in HA statistics table. Fixture values spot-checked across all three validation dates (Feb 7 2026, Mar 20 2026, Jul 17 2025). All 3 fixture tests pass after backfill.

---

### Use SQLite for the derived dataset, not CSV

**Decision:** The extraction script writes to a SQLite database, not a CSV file.
**Status:** Locked.

**Rationale:** A CSV is a dead artifact — every update requires rewriting the whole file. A SQLite DB supports incremental `INSERT OR REPLACE` for one row per day, type-safe NULLs, and rich querying during modelling. It also matches the natural eventual home of this code: HA's recorder is itself SQLite, so Phase 3's integration code path stays consistent.

**Alternatives considered:** CSV (rejected: not incrementally updateable, no type information, can't store metadata cleanly), Parquet (rejected: overkill at this scale, awkward for daily appends), the HA recorder DB itself (rejected: read-only contract — never write to it).

### Use two separate databases (HA read-only, project read-write)

**Decision:** The extraction script opens the HA DB read-only and writes to a separate project SQLite file.
**Status:** Locked.

**Rationale:** The HA DB is a live operational store managed by HA itself. Writing into it would couple our schema to HA's lifecycle (restarts, migrations, purges) and risk corruption. A separate file keeps our dataset durable, portable, and version-controllable independently. The connection string `file:{path}?mode=ro` enforces this.

---

## Window definition

### 6pm-prior to 11am-current local time, 17 hours

**Decision:** Each row aggregates data from 18:00 prior day to 11:00 row date in Australia/Melbourne local time.
**Status:** Locked.

**Rationale:** 6pm is the start of the evening peak export window — the moment the export-limit decision must be made. 11am is the natural recovery point: it is the start of the GloBird free-power window when active, and otherwise the point at which morning solar has typically supplied enough to absorb the day's load. Anything after 11am is reliably recoverable; anything before 6pm is the prior day's problem.

**Alternatives considered:** 6pm to 6pm (rejected: 24h windows hide the morning recovery dynamic that matters for the safety constraint), 6pm to sunrise (rejected: sunrise varies significantly, complicates comparison across seasons), 9pm to 9am (rejected: misses the peak export period itself).

### Each row indexed by the 11am-endpoint date (the "morning date")

**Decision:** The `date` column is the date of the 11am end of the window, not the 6pm start.
**Status:** Locked.

**Rationale:** The morning date is the date the prediction is _evaluated against_ (did we hit the safety threshold by 11am on this date?). Indexing by the start date would make queries like "all rows where the SoC was below 20% by morning" awkward.

### Daylight-only window (06:00–18:00) for curtailment detection

**Decision:** `max_soc_prev_daylight` only considers the 6am–6pm slice of the prior day, not the full 24 hours.
**Status:** Locked.

**Rationale:** We're detecting same-day solar curtailment ("did the battery hit 100% because of today's solar generation?"). Including overnight hours would pick up battery state inherited from the _previous_ day's solar, producing false positives on cloudy days where overnight SoC remained high from carryover.

**Evidence:** On 2026-03-19 the battery began the day at ~82% (carryover) but only reached 64% during daylight. The full-day max of 82% would falsely suggest curtailment; the daylight-only max of 64% correctly indicates a low-solar day.

**Alternatives considered:** Sunrise-to-sunset (rejected: complicates the window calc with no real benefit at Melbourne's latitudes), prior-day calendar max (rejected: see evidence above).

---

## Timezone handling

### Compute boundaries in `Australia/Melbourne` via `zoneinfo`, never hardcode offsets

**Decision:** All window boundaries are computed by constructing a local-time `datetime` and converting to UTC via `zoneinfo.ZoneInfo("Australia/Melbourne")`.
**Status:** Locked.

**Rationale:** The HA DB stores timestamps in UTC. The local-to-UTC offset is +10 hours in winter (AEST) and +11 hours in summer (AEDT), with transitions on the first Sunday of October and first Sunday of April. Hardcoding an offset will silently produce wrong windows for half the year.

**Evidence:** During exploratory work in this conversation, an early query used local-time strings interpreted by SQLite as UTC, shifting the Feb 4 2026 window by 11 hours and capturing the daytime of Feb 4 instead of the overnight period. This bug was only caught by cross-checking SoC values against an HA history chart.

### Do not use SQLite's `'localtime'` modifier on this database

**Decision:** Never use `datetime(start_ts, 'unixepoch', 'localtime')` anywhere in the extraction code.
**Status:** Locked.

**Rationale:** SQLite's `'localtime'` modifier applies the _server's_ local timezone. The HA system runs in UTC, so the modifier silently does nothing — it returns UTC time but renames it. This produced the Feb 4 bug above. Even if the server timezone is changed later, code that depends on it is fragile across environments.

---

## Sensor selection

### `sensor.solarnet_power_load` over `sensor.solarnet_power_load_consumed`

**Decision:** Use `solarnet_power_load` (with `ABS()` on the negative-signed values) for raw consumption integration. Note: the integrated value is QA-only; the primary consumption column is balance-derived (see below).
**Status:** Locked.

**Rationale:** Both sensors carry the same underlying measurement, but `solarnet_power_load` exists from system commissioning (2023-11-27) while `solarnet_power_load_consumed` only starts 2024-07-04. Using the older sensor extends the dataset by ~7 months of valuable winter/spring data.

**Evidence:** Side-by-side comparison during the overlap period confirmed identical magnitudes with opposite signs.

### `sensor.solarnet_power_photovoltaics` over `sensor.solar_power`

**Decision:** Use `solarnet_power_photovoltaics` for solar generation, integrating `MAX(mean, 0) × 1h`.
**Status:** Locked.

**Rationale:** `sensor.solar_power` is named misleadingly — it is the inverter's total AC output, which on a battery-equipped system includes battery discharge contributions, not pure PV.

**Evidence:** On Feb 3 2026, `sensor.solar_power` showed ~7.82 kWh for the 6pm-Feb-3 to 11am-Feb-4 window while `solarnet_power_photovoltaics` integrated to ~10.2 kWh. The 2.4 kWh shortfall corresponded to the 6–8pm AEDT period when PV output was low and battery was discharging — `solar_power` "froze" while battery discharge masked the true solar production. The PV-only sensor is correct.

### Smart meter `real_energy_*` sensors for grid

**Decision:** Use `sensor.smart_meter_63a_1_real_energy_consumed` and `_real_energy_produced` for grid import and export respectively, taking deltas of the cumulative `sum` column.
**Status:** Locked.

**Rationale:** These are proper cumulative Wh sensors with full history from 2023-11-27. They match the HA Energy Dashboard's own grid figures exactly to the watt-hour.

**Alternatives considered:** Integrating `sensor.solarnet_power_grid` (W) (rejected: same noise problems as load integration), the Fronius gen24 lifetime energy sensors (rejected: only available from 2026-04, late start).

### Rejected: Fronius gen24 sensor family

**Decision:** Do not use `sensor.fronius_primo_gen24_*` or `sensor.fronius_smart_meter_63a_1_*` (the meter-1-prefixed versions) for primary data.
**Status:** Locked.

**Rationale:** All sensors in this family were only registered in HA from 2026-04, giving only ~1 month of history at the time of this writing. The older sensor families (above) provide equivalent data with full system-lifetime coverage.

### Use `sum`, not `state`, for cumulative-Wh sensors

**Decision:** When computing window deltas, read the `sum` column from `statistics`, not `state`.
**Status:** Locked.

**Rationale:** `state` is the raw cumulative meter reading. `sum` is the HA-corrected cumulative value that handles meter resets, inverter swaps, and similar discontinuities. For our smart meter, `sum` and `state` differ by ~10 kWh, indicating HA absorbed at least one reset event during the system's lifetime. Using `state` would put a step in our dataset.

### Hourly granularity is sufficient

**Decision:** Use HA's hourly `statistics` table, not the sub-hourly `states` table.
**Status:** Locked.

**Rationale:** The window we care about (6pm–11am) is 17 hours long. Aggregations at 1-hour granularity are appropriate and the cumulative-sum sensors give exact energy deltas regardless. Using `states` would multiply query cost ~12-60× with no gain in accuracy for our specific aggregations.

**Evidence:** The Feb 7 2026 row's energy balance closes to within ~1.5% using hourly data — well below the model's prediction error budget.

---

## Computation methodology

### Balance-derived consumption is primary; integrated power is QA-only

**Decision:** `consumption_wh = solar_wh + grid_import_wh + battery_discharged_wh − grid_export_wh − battery_charged_wh`. The raw `SUM(ABS(load.mean))` integration is stored separately as `consumption_wh_load` for QA only.
**Status:** Locked.

**Rationale:** Hourly mean-power integration loses information about within-hour spikes, and the bias direction varies day-to-day. The energy balance, by contrast, is a sum of cumulative-meter deltas — exact to the watt-hour by construction, and matches HA's Energy Dashboard's own home-consumption calculation.

**Evidence:** Imbalance (`consumption_wh_load − consumption_wh`) measured across our three validation samples:

- Feb 7 2026 (sunny, 17h overnight): +192 Wh (+4.0%)
- Mar 20 2026 (cloudy, 17h overnight): +1429 Wh (+27.5%)
- Jul 17 2025 (winter, 17h overnight): +341 Wh (+2.7%)

Across the full 2-day Mar 19–20 window, integrated load was 25.82 kWh while the balance gave 28.38 kWh — a 9% under-report. The HA Energy Dashboard reported 28.2 kWh, which only matches the balance-derived value. We are aligning with HA's own methodology rather than the noisier integration.

### "Value at 6pm" = mean of the 17:00 hourly bucket

**Decision:** `soc_at_6pm` and `soc_at_11am` use the mean of the bucket whose `start_ts` is one hour earlier (17:00 and 10:00 respectively).
**Status:** Locked.

**Rationale:** HA buckets are labeled by their start time and represent the average over the following hour. The bucket starting at 17:00 represents the average from 17:00 to 18:00 — i.e. the value visually displayed _to the left of_ the 6pm tick on a chart. This matches what a human reads when they look at the SoC graph at 6pm.

**Evidence:** Cross-checked against the HA history chart for Feb 4 2026: chart showed ~79% at 11am AEDT, our `soc_at_11am` calculation returned 79.4% from the 10:00 bucket mean.

### Cumulative-sum boundary: read the bucket one hour earlier

**Decision:** To read the cumulative meter value **at** a boundary hour H, query the `sum` column of the bucket labelled `H−1h` (i.e. `start_ts = H−1h`). Concretely: `grid_import_wh`, `grid_export_wh`, `battery_charged_wh`, `battery_discharged_wh` are all computed as `sum @ 10:00 row date − sum @ 17:00 prior day` — not `sum @ 11:00 − sum @ 18:00`.
**Status:** Locked.
**Date:** 2026-05-22

**Rationale:** HA's hourly bucket with `start_ts = T` stores the cumulative meter reading at the **end** of that bucket (time `T+1h`), not the start. This was confirmed empirically: the delta between the `start_ts=16:00` and `start_ts=17:00` `sum` values for the grid-consumed sensor (`metadata_id=251`) on 19 May 2026 AEST was +5 Wh, and the HA chart confirmed 5 Wh was consumed during the **17:00–18:00** hour — i.e. the energy for `[T, T+1h)` lands in the bucket ending at `T+1h`, proving `sum @ T = reading at T+1h`.

**Note:** This is the cumulative-sensor analogue of the "Value at 6pm = mean of the 17:00 bucket" decision above, which applies to _mean_ sensors. Both use `H−1h` to read the value "at" hour H, but for different reasons: mean sensors use the 17:00 bucket because it is the last complete hour before 18:00; cumulative sensors use the 17:00 bucket because its stored `sum` is the reading at 18:00.

**Impact:** The prior code used `sum @ 18:00 − sum @ 11:00`, which was the delta over `[19:00 prior, 12:00 today]` instead of the correct `[18:00 prior, 11:00 today]` — missing the 18:00–19:00 hour and wrongly including the 11:00–12:00 hour. The error is small on quiet boundary hours but large whenever there is significant grid or battery activity at 11:00–12:00 (e.g. GloBird free-power charging). This bug was found on 20 May 2026 when `grid_import_wh` showed 2,584 Wh against a known actual of ~43 Wh; the 2,541 Wh excess was a grid spike during 11:00–12:00 that the wrong boundary included. The fix was applied in the same conversation and the dataset rebuilt.

**Supersedes:** the wrong statement in DATASET.md (now corrected) that `sum @ start_ts=18:00` is the reading "immediately at the start of bucket [18:00, 19:00)".

---

### Curtailment threshold at 99%, not 100%

**Decision:** `curtailment_likely = 1` when `max_soc_prev_daylight ≥ 99`.
**Status:** Locked.

**Rationale:** SoC measurements have minor noise and do not always report exactly 100% even at full charge. A 99% threshold catches near-100% correctly while remaining a strong signal that the battery filled (which is the actual condition driving curtailment).

**Alternatives considered:** 100% (rejected: brittle to measurement noise), 95% (rejected: would catch near-full days where curtailment did not actually occur).

---

## Data quality and coverage

### Flag the absence period; do not exclude its rows

**Decision:** Rows whose date falls within a configured absence period are written with `absence_period = 1` but otherwise computed normally. Absence periods are defined in `config.yaml`.
**Status:** Locked.

**Rationale:** Excluding the rows from the dataset would create gaps that complicate downstream code (e.g. time-series operations). Writing them with a flag preserves chronological completeness while letting the model trainer filter cleanly with `WHERE absence_period = 0`. The data itself remains useful for QA and pattern comparison.

### Flag data gaps; do not delete their rows

**Decision:** Rows with known sensor outages are written with `data_gap = 1` and kept in the dataset. New gaps are added to `data_gap_dates` in `config.yaml` and the extraction script re-run with `--from <date>` to backfill.
**Status:** Locked.

**Rationale:** Same reasoning as the absence period flag — deleting rows creates chronological gaps that complicate downstream code. A flag lets the model trainer filter cleanly with `WHERE data_gap = 0` while preserving the rows for QA.

The extraction script detects likely new gaps automatically: a large energy imbalance (>3000 Wh between balance-derived and load-integrated consumption) combined with near-zero battery throughput despite a significant SOC swing, or zero solar before 11am (reliable even in Melbourne mid-winter), triggers a warning and a ±1 day investigation prompt. High-cycling days produce large imbalances without these signatures and are not warned on.

### Guests column is NULL before 2026-03-08

**Decision:** When the guests sensor has no data for a row's window, store `guests = NULL`, not 0. The guests sensor is configured via `config.yaml` (`sensors.guests`).
**Status:** Locked.

**Rationale:** Distinguishing "no guests" from "we don't know" matters for modelling. A NULL forces the trainer to make an explicit choice about how to handle pre-sensor rows (impute, exclude, treat as 0); zero-filling silently makes that choice and biases the feature. Note: the guests column is not yet used by the model — it is stored for future use once enough positive examples have accumulated.

### Energy imbalance is logged, not corrected

**Decision:** When `|consumption_wh_load − consumption_wh| > 3000 Wh` for a row, the script logs a warning but writes the row anyway.
**Status:** Locked.

**Rationale:** Large imbalances indicate either rapid power swings (legitimately noisy days) or sensor anomalies (data we may want to investigate but not silently discard). Auto-correction would mask both. The warning gives us a trail for follow-up without blocking the pipeline.

---

## Validation fixtures

### Three days, deliberately diverse

**Decision:** The validation suite contains exactly three known-good fixtures: Feb 7 2026 (AEDT, sunny, full battery, amber, curtailed), Mar 20 2026 (AEDT, cloudy, depleted battery, amber, no curtailment), Jul 17 2025 (AEST, winter, depleted, ea, no curtailment).
**Status:** Locked for these three; additional fixtures may be added.

**Rationale:** Three is the minimum to cover both DST regimes (AEST and AEDT), both providers active during validation (ea and amber), both battery states (full and depleted), and curtailment / no-curtailment. A larger fixture set was tempting but each fixture must be validated against the HA history chart by hand; three is the point of diminishing returns.

**Evidence:** Each fixture's primary fields were cross-checked against HA history charts and the HA Energy Dashboard during the design conversation. The values are documented in DATASET.md § Validation samples.

---

## Project structure

### Documentation split: CLAUDE.md (agent) + SPEC.md (what) + DATASET.md (data) + DECISIONS.md (why)

**Decision:** Four documents, each with a single clear purpose; cross-references rather than duplication.
**Status:** Locked.

**Rationale:** CLAUDE.md is loaded as standing context every agent session, so it must be brief. The deeper docs are referenced when needed. Splitting _what_ (DATASET) from _why_ (DECISIONS) means an agent implementing extraction has an unambiguous spec without having to wade through rationale, while still being able to find the rationale on demand to avoid undoing decisions.

**Alternatives considered:** Single SPEC.md with everything (rejected: would be unwieldy and hard to navigate), README-only (rejected: would either be too long or too shallow).

### Phase 1 standalone Python before HA integration

**Decision:** Phase 1 produces a standalone Python pipeline; HA integration is Phase 3.
**Status:** Locked.

**Rationale:** Building an HA integration first would conflate two unrelated risks: (1) does our methodology produce a useful predictor, and (2) does our integration package work in HACS. By keeping Phase 1 standalone, we can iterate on the extraction logic against fixtures without HA in the loop, then reuse the same `extract.py` and `predict.py` functions inside the integration in Phase 3.

---

## Modelling (Phase 2)

### Four-zone model for overnight consumption

**Decision:** Use four separate models keyed by forecast overnight mean temperature (`bom_temp_mean`): Heating (< 17°C, OLS regression), Warm boundary (17–19°C, empirical percentile table), Mild (19–21°C, empirical percentile table), Cooling (> 21°C, OLS regression).
**Status:** Locked.
**Date:** 2026-05-11 (split from original three-zone decision dated 2026-05-07)

**Rationale:** The original heating zone spanned < 19°C as a single OLS model. Analysis of held-out residuals revealed a systematic +1.47 kWh mean bias in the 17–19°C sub-band, with 19 of 118 nights showing errors > 3 kWh. Investigation ruled out all available weather signals as explanatory: temperature range, humidity, wind, Solcast level, indoor temperature, indoor–outdoor delta, and season all showed near-zero correlation with the residuals (r ≤ 0.10). The variance is driven by human behaviour (whether heating ran hard on a given night) which no external measurement can predict.

With no predictive signal, an OLS regression in this band is no better than a mean estimate but carries the overhead of coefficients that can mislead. An empirical percentile table is more honest: it accurately represents the historical distribution and provides correctly-sized safety buffers without implying a spurious relationship with temperature.

**Coefficients and percentiles (config.yaml) — refit 2026-05-22 after the cum-delta boundary fix (see "Zone bands retained at 17/19/21 after retraining" below). Original values in parentheses:**

- Heating with Solcast: `22.5759 − 0.9593×temp − 0.016243×solcast_kwh` (R²=0.83, n=352, temp < 17°C; was `19.7258 − 0.7756×temp − 0.0703×solcast_kwh`, R²=0.77)
- Heating temp-only: `22.4741 − 1.0043×temp` (R²=0.82, n=602; was `18.8039 − 0.8614×temp`, R²=0.71)
- Warm boundary empirical: P50=5.98, P75=6.81, P90=7.78, P95=8.78 kWh (n=114, 17–19°C; was 4.76/6.00/6.99/8.05)
- Mild empirical: P50=6.80, P75=7.84, P90=8.51, P95=9.00 kWh (n=78, 19–21°C; was 4.60/6.58/7.83/8.43)
- Cooling with humidity: `−9.6822 + 0.7163×temp + 0.028676×humidity_pct` (R²=0.52, n=64; was `−13.4046 + 0.7231×temp + 0.0595×humidity_pct`, R²=0.37)
- Cooling temp-only: `−5.1760 + 0.5965×temp` (was `−6.756 + 0.660×temp`)
- P95 buffers: heating 2.649 kWh (was 3.562), cooling 2.431 kWh (was 3.136) — both shrank because the boundary fix removed spurious 11:00–12:00 activity that was inflating residual noise.

**Held-out test performance (stratified, every 5th night per zone):** post-refit violation rate 0.8% heating / 0.0% cooling at the P95 buffer (target ≤5%). The pre-fix figure was 2.4% at a larger (3.56 kWh) buffer.

**Known weakness:** The cooling zone has only ~64 training nights (one summer). R²=0.52 is expected to improve materially once a second summer of data is available.

---

### Zone bands retained at 17/19/21 after retraining

**Decision:** Keep the four-zone boundaries at 17 / 19 / 21 °C (on `bom_temp_mean`) unchanged after the 2026-05-22 retrain. Do not merge, move, or rename them.
**Status:** Locked.
**Date:** 2026-05-22

**Context:** The cum-delta boundary bug fix (see "Cumulative-sum boundary" decision) changed every `consumption_wh` value, so the model was retrained from the rebuilt dataset (`tools/retrain.py`, 858 trainable nights = `absence_period=0 AND data_gap=0`). The retrain surfaced a result worth scrutinising: the **mild** table (19–21 °C) now sits _consistently above_ the **warm-boundary** table (17–19 °C) — e.g. P50 6.80 vs 5.98 kWh. That looks backwards (warmer band → less heating → ought to be lower).

**Why the bands are nonetheless correct.** A 1 °C-bin consumption profile shows the true consumption minimum sits at **17–19 °C** (~5.9–6.0 kWh median), which is exactly the warm-boundary band. The 19–21 °C band (~6.8 kWh) is already on the _cooling_ upslope — a 19–21 °C overnight _mean_ in summer typically implies a warm 6–9 pm evening with some AC load. So mild > warm is a real, physical feature, not a defect:

| 1 °C bin | median kWh |     | 1 °C bin | median kWh |
| -------- | ---------- | --- | -------- | ---------- |
| 15–16    | 6.41       |     | 19–20    | 6.85       |
| 16–17    | 6.22       |     | 20–21    | 6.76       |
| 17–18    | 5.92 (min) |     | 21–22    | 7.41       |
| 18–19    | 6.01       |     | 22–23    | 8.10       |

**"Mild" is a retained misnomer.** The 19–21 °C band is really a _low-cooling shoulder_, not a sweet spot. Renaming it was rejected: the zone name is hardcoded in three independent model implementations (`src/model.py`, `tools/predictor.html`, `tools/nodered-flow.json`) plus `config.py`/`config.yaml` fields and tests, so a rename is pure cosmetic churn against the Phase 2→3 contract. The misnomer is instead documented in code (`src/model.py` module docstring + inline comment).

**Alternatives considered:**

- _Merge warm + mild into one 17–21 °C shoulder table_ — rejected: blurs the ~0.8 kWh step at 19 °C, over-buffering 17–19 °C nights and under-buffering 19–21 °C ones.
- _Extend the cooling OLS down to 19 °C_ — rejected: within 19–21 °C consumption is essentially flat (6.85 / 6.76), so the fitted slope ≈ 0 — no better than the current empirical median, while losing the percentile tail the safety buffer relies on.
- _Move boundaries (e.g. heating cut to 16 °C)_ — rejected: the profile shows 17/19/21 already align with the heating downslope / flat minimum / cooling upslope structure.

**Revisit when:** a second full summer of cooling data lands (already an open decision), or live operation shows a specific zone systematically miscalibrated.

---

### Solcast full-day forecast as cloud-cover proxy only; no explicit solar credit in export formula

**Decision:** Use `solcast_forecast_tomorrow_wh` as a cloud-cover proxy feature in the consumption regression only. The safe-export formula does **not** include a solar credit term.
**Status:** Locked.
**Date:** 2026-05-11

**Rationale:** The SPEC formula includes a `+ predicted_solar` term, but after evaluation this was found to be unsafe in practice. Morning solar arrives in a ~3-hour burst (roughly 8–11am), but the battery must survive the full 17-hour overnight window on its own. Adding the full `solcast × 0.21` credit to the export formula produced an 86.6% safety violation rate on the stratified test set because it inflated recommendations beyond what the battery could sustain through the night.

A capped variant (`min(solcast × 0.21, total_needed × 3/17)`) — crediting only the solar that covers the 3-hour morning window fraction of overnight load — was evaluated and reduced violations to 12.6% vs the ≤5% target. Analysis showed those violations were driven by warm-boundary consumption model errors (now addressed by the four-zone split), but the evaluation complexity grew high enough that a conservative decision was made: exclude solar credit for now and revisit once the model is stable in live operation.

The Solcast coefficient in the consumption regression (−0.070291 kWh per kWh Solcast) continues to absorb an implicit partial solar signal via its cloud-cover role.

**Revisit when:** at least one full season of live operation data confirms the violation rate and utilisation, and the solar credit formula can be evaluated against real outcomes rather than held-out historical data.

---

### Confidence via scaled P95 residual buffer, not a formal quantile model

**Decision:** Uncertainty is expressed as a single-sided buffer scaled from the training-set P95 absolute residual. The scaling to lower confidence levels is linear over the empirical percentile ladder (P50/P75/P90/P95).
**Status:** Locked.
**Date:** 2026-05-07

**Rationale:** At 845 nights of data (heating zone: 574, cooling zone: 49), a formal quantile regression or conformal prediction framework would add code complexity without providing meaningfully better calibration than the empirical approach. The P95 buffer already covers 92% of held-out test errors, which is within the ±5pp calibration target in SPEC.md. Revisit when dataset exceeds ~2000 nights or if calibration degrades in live monitoring.

**Alternatives considered:** Quantile regression (deferred: worth revisiting with more data), conformal prediction (deferred: same reasoning), Bayesian posterior over coefficients (rejected: over-engineered for current sample sizes).

---

### `min_soc` as the single discharge floor; no separate safety threshold

**Decision:** `PredictInputs.min_soc` is the battery's configured grid-discharge floor (default 10%). There is no additional "safety threshold" on top. The confidence buffer (P90/P95 error margin) is the sole probabilistic safety mechanism.
**Status:** Locked.
**Date:** 2026-05-07

**Rationale:** A separate safety threshold would double-count the uncertainty that the confidence buffer already handles, leaving systematic unexploited headroom on every night. The two concerns are distinct: `min_soc` is an operational hard floor (can be 10% normally, 20–30% in storm mode), while the confidence buffer handles model uncertainty. Conflating them into one parameter produces a model that is simultaneously conservative for the wrong reason and miscalibrated.

**Note:** The battery's hardware absolute floor is ~7% (maintained even during grid outages). `min_soc` controls the grid-connected discharge limit, which is always ≥ 7% in practice. The `predict()` function does not enforce the 7% hardware floor — that is the inverter's responsibility.

---

---

### Calendar features: evaluated and rejected at current data size

**Decision:** Day-of-week, season, and other calendar features are not included in `PredictInputs` or the model.
**Status:** Locked (revisit at ~1500+ nights).
**Date:** 2026-05-10

**Rationale:** ENERGY_ANALYSIS.md evaluated calendar features against the dataset and found no meaningful signal — overnight consumption is driven by temperature and HVAC load, not the day of the week. The U-shaped temperature relationship (heating below 19°C, cooling above 21°C) already captures the seasonal variation. Adding calendar features at current dataset size (~845 nights) would add parameters without predictive benefit and risk overfitting to incidental patterns in a short history.

**What was considered:**

- _Day of week_ — no prior evidence that weekends drive materially different overnight loads vs weekdays in a single-occupant household.
- _Season_ — already implicitly captured by `bom_temp_mean` zone selection.
- _Heating-effort delta (`avg_indoor_temp − bom_temp_mean`)_ — theoretically appealing as a proxy for heating aggressiveness, but tested and found to be redundant. `r(bom_temp_mean, delta) = −0.948`: on cold nights the outdoor temp is low and the indoor–outdoor gap is automatically large, so the two variables move together almost perfectly. Frisch-Waugh partial regression (n=414 heating-zone nights with Solcast) shows R² gain of only +0.036 (0.70 → 0.74), and the mean model residual in the highest-delta tercile (10.4°C gap, +0.36 kWh) is indistinguishable from the lowest-delta tercile (4.5°C gap, +0.35 kWh). The delta carries no independent signal after `bom_temp_mean` is controlled for. Would only become useful if heating behaviour varied significantly — e.g. a configurable thermostat setpoint sensor were added.

**Revisit when:** dataset exceeds ~1500 nights, or when a clear residual pattern appears in heating-zone model errors stratified by day-of-week.

---

### Backtest results: model is viable from September, not worth deploying in winter (Jun–Aug)

**Decision:** Do not deploy safe-export recommendations during June, July, and August until a winter-specific fix is in place. Target deployment from September 2026.
**Status:** NEEDS REVISIT — Open. The "categorically loss-making" rationale below was an artifact of the old consumption-based metric (it charged the model for the _unavoidable_ winter grid draws). Under the SoC-trough metric (Backtest v3, 2026-05-22) the model is **break-even, not loss-making** in winter: it correctly recommends ≈zero export, so it earns almost nothing **and loses nothing** (zero export-caused shortfall at every confidence). Running the export model in winter is therefore harmless (not profitable). Do NOT re-decide off backtest numbers alone — revisit with live winter data; the winter `perfect_net` also leans on the full-charge assumption, so winter net-capture % is low-signal.
**Date:** 2026-05-11 (rationale below); revisit flagged 2026-05-22

**Rationale:** A full-year backtest (`tools/backtest.py`, covering 2025-05-11 to 2026-05-08) evaluated four scenarios across 353 nights:

| Scenario                        | Revenue  | Shortfall | Net         |
| ------------------------------- | -------- | --------- | ----------- |
| A: Actual SoC, fixed P90        | $104.98  | -$133.45  | **-$28.47** |
| B: Full-charge SoC, fixed P90   | $110.52  | -$41.30   | **+$69.22** |
| C: Actual SoC, seasonal Px      | see HTML | see HTML  | see HTML    |
| D: Full-charge SoC, seasonal Px | see HTML | see HTML  | see HTML    |

Rates used: $0.15/kWh export, $0.28/kWh grid buyback. Absence period (Sep 28–Nov 3 2025) used prior-year same-date proxy.

**Winter (Jun–Aug) is categorically loss-making under all scenarios.** Even with a fully charged battery (scenario B), June–August net is −$33. The P90 buffer is insufficient to contain actual winter consumption variance. The model's consumption prediction for cold nights is correct on average, but the tail events are expensive: under-predicted consumption nights require buying back grid power at $0.28/kWh, which more than cancels any export revenue.

**Spring through autumn (Sep–May) is solidly positive** in all full-charge scenarios: net +$102 in scenario B across 9 months, with summer months reaching 68–70% efficiency.

**The full-charge assumption matters enormously.** Scenario A (actual SoC) net is −$28 for the year; scenario B (GloBird overnight top-up to 100%) is +$69. The ~$97 swing is entirely attributable to winter nights where the battery didn't fully charge during the day and 6pm SoC was too low to support any export. Under the current EA/Amber tariff structure without overnight charging, winter export is even riskier.

**Seasonal confidence (Px) tuning helps at the margins** but does not fix the structural winter problem. The main lever is either: (a) block export entirely in winter, or (b) develop a better winter model.

**What "full charge" means in practice:** `soc_at_6pm + (100 − max_soc_prev_daylight)` — i.e. the battery is assumed to peak at 100% each day. The delta between actual and adjusted SoC is the shortfall GloBird's overnight charge would have filled.

**Next steps before winter deployment:**

1. Collect live operation data through at least one full winter with the four-zone model running (observe-only, no export).
2. Investigate whether a winter export block (`bom_temp_mean < 15°C` or `available_discharge_wh < predicted_consumption × 1.2`) eliminates shortfalls without sacrificing shoulder-season revenue.
3. Re-evaluate once GloBird overnight charging is active — the full-charge scenario shows that daily 100% top-up is the biggest single lever.

**Tool:** Full backtest report at `tools/backtest_report.html`. Regenerate with `.venv/Scripts/python -m tools.backtest`.

---

### Backtest v2: actual-SoC scenarios dropped; baselines added; net capture metric introduced

**Decision:** The backtest now evaluates only full-charge SoC scenarios (GloBird overnight charging assumed). Naive baselines are included for comparison. The efficiency column is replaced by "net capture" throughout.
**Status:** Locked.
**Date:** 2026-05-11

**Rationale and findings from a second round of backtest analysis:**

**Dropped actual-SoC scenarios:** With GloBird overnight charging active from 2026-05-05, the actual-SoC scenarios (old A and C) no longer represent the operating reality. They are removed to reduce noise in the report.

**Baseline scenarios added:** Three naive baselines were added — 3-day rolling average consumption, 7-day rolling average, and seasonal fixed median (dataset medians: Winter 12,163 Wh, Shoulder 6,481 Wh, Summer 5,859 Wh). These were added to test whether the model adds value over a rule-of-thumb approach. Key finding: **the baselines outperformed the model on raw net dollars** on the training data, but for the wrong reason. They export more aggressively, incur substantially more shortfall cost, and only come out ahead because the training data is the same data the model learned from. The model's conservatism (the buffer) is the primary drag, not its consumption estimate.

**Net capture replaces efficiency:** The old "efficiency" metric was `revenue ÷ (revenue + opportunity gap)` — blind to the rate asymmetry ($0.28/kWh buyback vs $0.15/kWh export). A scenario that blew past the consumption estimate every night would score 100% efficiency while losing money on buyback costs. Net capture = `net ÷ perfect_net`, where perfect net is what a hindsight-perfect model would earn (export exactly `max(0, available − actual)`, zero shortfall). This correctly penalises shortfall because the perfect benchmark avoids it. Under this metric, the baselines score ~63–64% (non-winter) while the model at P50 scores 69.7% — confirming the model adds genuine value when conservatism is relaxed.

**Winter excluded from summary:** Winter (Jun–Aug) nets are negative under all scenarios and structurally unchanged by any confidence-level choice. Including winter in the summary totals obscures the meaningful non-winter comparison. The per-scenario monthly tables still show winter for completeness.

**Net capture colour thresholds (non-winter context):** ≥65% green, ≥55% amber, <55% red. These are calibrated to the observed non-winter range (51–70%); they are not meaningful for full-year totals including winter.

**Full scenario listing (non-winter net / net capture):**

| Scenario                                      | Net capture | Net  |
| --------------------------------------------- | ----------- | ---- |
| A: Model P90                                  | 51.6%       | $102 |
| B: Model seasonal Px (P95/P90/P75)            | 58.3%       | $115 |
| H: Model fixed P75                            | 64.0%       | $127 |
| F: Model aggressive seasonal Px (P95/P75/P50) | 64.4%       | $127 |
| G: Model fixed P50                            | 69.7%       | $138 |
| C: 3-day rolling average                      | 64.2%       | $127 |
| D: 7-day rolling average                      | 63.3%       | $125 |
| E: Seasonal fixed median                      | 63.3%       | $125 |

**Caution:** All scenarios are evaluated on training data. The baselines' apparent competitiveness vs the model is flattering; on unseen data they will regress as they have no mechanism for unusual nights (weather events, temperature extremes).

---

### Backtest v3: SoC-trough metric + evening-export reconstruction

**Decision:** The backtest evaluates an export decision against the actual overnight **SoC trough** (`min_soc_overnight`), not against total `consumption_wh`. The no-export baseline trough is reconstructed by adding back real peak exports (`evening_grid_export_wh`) and the full-charge adjustment. The "perfect" benchmark drains to a **soft floor** (hard floor + 10 pts); a shortfall (grid buyback) is charged only for export that pushes the trough below the **hard floor**, and only the _incremental_ breach the export causes (a night already short with no export is not blamed on the export). The hard floor and capacity are read from `config.yaml`.
**Status:** Locked (metric); the deployment-confidence conclusion remains Open (below).
**Date:** 2026-05-22

**Rationale:** The previous metric used `actual_wh = consumption_wh` and "perfect = drain to exactly the floor". That (a) ignored morning solar, (b) ignored overnight SoC timing, and (c) charged shortfall on nights the battery was short even with zero export — visible as an identical "baked-in" shortfall across all confidence levels in the recent window. The SoC trough is the real safety constraint, so evaluating against it is correct.

**Why the new `evening_grid_export_wh` column was needed:** `min_soc_overnight` is the _actual_ trough, depressed by any real battery-to-grid export. The user exports sporadically across all tariff eras (EA, Amber, GloBird), so there is no clean "no-export" period to restrict to. Adding back the peak-window grid export (`evening_grid_export_wh`, the `produced.sum` delta over 18:00–21:00; see DATASET.md) recovers the no-export baseline trough on every night. Caveat: in high summer the 6–9pm window can include some PV→grid, slightly over-crediting headroom; negligible outside summer evenings.

**Findings (corrected data + retrained model):**

- **Recent 14 days (real GloBird, the trustworthy lens):** the model exports with **zero** floor breaches at every confidence level; P50 captures the most (57.8%), matching the 3-day-rolling baseline's net but with zero shortfall vs the baseline's $3.17.
- **Non-winter full period:** the model looks **safe but conservative** — it under-exports relative to the headroom the trough shows the battery actually had (morning charging/solar means it rarely drops to the consumption-based reservation). Aggressive strategies capture more net because breaches are rare and export revenue ($0.15) outweighs occasional buyback ($0.28).
- **Caveat:** full-period capture numbers lean heavily on the full-charge (GloBird-to-100%) assumption, which inflates reconstructed headroom, and the baselines are evaluated in-sample. Trust the recent-14-day result far more than the full-period baseline ranking.

**Summary tables** in the HTML report are now sorted by net capture descending.

---

### Deployment confidence level: keep Open; Node-RED runs P50 for live testing

**Decision:** Deployment confidence is **not yet settled**. The Node-RED flow's default output is set to **P50** so live operation exercises the most-capturing level, but no fixed deployment confidence is locked.
**Status:** Open (only validated on a 14-day window so far).
**Date:** 2026-05-22 (supersedes the 2026-05-11 "start at P75" position below)

**Rationale:** Under the corrected data + SoC-trough metric, P50 is the best model confidence on net capture and showed **zero floor breaches** over the recent 14 days, with room to possibly push more aggressive. But 14 days is far too little to lock a deployment policy, and the full-period numbers are caveated (full-charge assumption, in-sample baselines). The Node-RED flow exporting P50 is a _live test_, not a locked decision — revisit after a meaningful run of real nights. The earlier P75 starting position is superseded but kept below for history.

---

### (Superseded 2026-05-22) Deployment confidence level: start at P75, evaluate P50 after one season

**Decision:** Deploy at fixed P75 from September 2026. Evaluate moving to P50 after one full shoulder/summer season of live data.
**Status:** Superseded by the entry above (P50 live-test, confidence still Open).
**Date:** 2026-05-11

**Rationale:** The backtest suggests P50 is the best-performing fixed confidence level on net capture (69.7% non-winter). However this is training-data territory. P75 is the more conservative starting point: it nets $127 non-winter at 64% capture, incurs only $19 shortfall, and sits at the same level as the aggressive baselines. It is a defensible first deployment that limits downside on nights where the model is wrong, while still materially outperforming P90 (+$25 net non-winter).

The P50 vs P75 tradeoff in practice: going from P75 to P50 costs an additional $30/year in shortfall risk to gain $11/year in net. Whether that tradeoff is worthwhile depends on how well-calibrated the model proves to be on live data — if actual violations at P75 are rare, P50 becomes more attractive; if they are frequent, staying at P75 or tightening to P90 is the right call.

**Note on intermediate confidence levels (P60, P65, P70):** The model's empirical percentile tables only have entries at P50/P75/P90/P95. Values between these snap to the nearest defined entry (P60 and P70 both produce the same result as P75; P80 snaps to P75). Adding intermediate entries (P60, P65) would require recomputing the percentile tables from the dataset. This is not worth doing until live data confirms the P75 vs P50 choice is the right axis to refine — the training-data backtest cannot tell us that.

---

## Open / deferred decisions

These are explicitly _not_ settled. They are recorded so that an agent asked to make one of these choices recognises it as a real decision requiring discussion, not a default.

### Sensibo HVAC integration — setpoint and mode as future features

Sensibo AC integration added to HA on 2026-05-10. Once sufficient history accumulates, evaluate whether HVAC setpoint and mode (heat/cool/off) from Sensibo sensors can improve model accuracy — particularly:

- _Setpoint_ as a direct measure of heating/cooling aggressiveness, replacing the indirect and redundant `avg_indoor_temp − bom_temp_mean` delta (see "Calendar features" decision above).
- _HVAC mode_ as a binary flag to distinguish heating nights from cooling nights more reliably than the temperature-zone boundaries, and to detect nights where the system was off entirely (e.g. mild nights where the user chose not to run AC despite temperature crossing the 19°C or 21°C thresholds).

**Pre-conditions before evaluating:** at least one full heating season (winter 2026) and one full cooling season of Sensibo data; verify that HA is recording setpoint and mode to `statistics` (not just `states`) so they can be extracted at hourly granularity.

- **Solar credit formula.** No solar credit is applied in the current export formula (see locked decision above). Variant C (capped credit `min(solcast×0.21, needed×3/17)`) met the utilisation target but not the violation target in testing with the old three-zone model. Now that warm-boundary nights have their own empirical zone, variant C should be re-evaluated against a full season of live operation data before being reconsidered.
- **Provider handling.** Provider is recorded in the dataset but is not passed to `predict()`. Separate models per provider, or provider as a quantitative feature? Deferred until enough data under each tariff exists to evaluate.
- **Cooling model improvement.** Only ~49 training nights (one summer). Re-evaluate coefficients and consider adding `median_indoor_humidity` once a second full summer is in the dataset (expected: late 2026).
- **Retraining cadence.** Daily, weekly, monthly? Triggered by error spikes or scheduled? Deferred to Phase 3.
- **Live deployment safety.** What backstops protect against a model going haywire in production (e.g. an absolute reserve-floor hardstop independent of the model output)? Deferred to Phase 3.
- **Generalisation to other hardware.** Battery capacity, reserve fraction, and sensor names are now configurable via `config.yaml`. Phase 3 will surface this configuration through the HA integration UI so users don't need to edit YAML manually.

---

## Superseded decisions

(Empty as of v1. When a decision is superseded, move its entry here with the date and a pointer to the new decision.)
