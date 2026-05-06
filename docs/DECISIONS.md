# DECISIONS.md

This document records the _why_ behind each significant design choice in the project. Its purpose is to prevent regression: future agents (or future-you) should not undo a decision listed as **Locked** without first proposing a change, citing new evidence, and getting explicit agreement.

Each entry has the form:

> **Decision:** Brief statement of what was chosen.
> **Status:** Locked / Open / Superseded.
> **Rationale, alternatives, evidence** as needed.

---

## Data architecture

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

### Curtailment threshold at 99%, not 100%

**Decision:** `curtailment_likely = 1` when `max_soc_prev_daylight ≥ 99`.
**Status:** Locked.

**Rationale:** SoC measurements have minor noise and do not always report exactly 100% even at full charge. A 99% threshold catches near-100% correctly while remaining a strong signal that the battery filled (which is the actual condition driving curtailment).

**Alternatives considered:** 100% (rejected: brittle to measurement noise), 95% (rejected: would catch near-full days where curtailment did not actually occur).

---

## Data quality and coverage

### Flag the hospital period; do not exclude its rows

**Decision:** Rows where `2025-09-28 ≤ date ≤ 2025-11-03` are written with `hospital_period = 1` but otherwise computed normally.
**Status:** Locked.

**Rationale:** Excluding the rows from the dataset would create gaps that complicate downstream code (e.g. time-series operations). Writing them with a flag preserves chronological completeness while letting the model trainer filter cleanly with `WHERE hospital_period = 0`. The data itself remains useful for QA and pattern comparison.

### Guests column is NULL before 2026-03-08

**Decision:** When the `sensor.hastguests` sensor doesn't yet exist for the row's date, store `guests = NULL`, not 0.
**Status:** Locked.

**Rationale:** Distinguishing "no guests" from "we don't know" matters for modelling. A NULL forces the trainer to make an explicit choice about how to handle pre-sensor rows (impute, exclude, treat as 0); zero-filling silently makes that choice and biases the feature.

**Evidence:** Across the period 2026-03-08 to today, only one date (2026-04-17) recorded `guests = 1`. The positive class is so rare that naive treatment of pre-sensor rows as 0 would be effectively indistinguishable from the actual label distribution but would still bias any model that learns from `guests`.

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

## Open / deferred decisions

These are explicitly _not_ settled. They are recorded so that an agent asked to make one of these choices recognises it as a real decision requiring discussion, not a default.

- **Model architecture for Phase 2.** Single end-to-end vs decomposed (separate solar / consumption models). Classical (gradient-boosted) vs sequence model. See SPEC.md § Open questions.
- **Provider handling.** Separate models per provider, or provider as a feature? The transition from `ea` to `amber` to `globird` represents real tariff-structure differences that may make a single model fragile.
- **Forecast input quality.** How well does Solcast's forecast match observed PV at our specific site? Quantifying this affects how much of our prediction uncertainty is irreducible.
- **Confidence interval method.** Quantile regression, conformal prediction, or model-based variance estimation? Any of these can produce the "≥90% confidence" output SPEC.md requires.
- **Retraining cadence.** Daily, weekly, monthly? Triggered by error spikes or scheduled?
- **Live deployment safety.** What backstops protect against a model going haywire in production (e.g. reserve-floor hardstop independent of the model)?

---

## Superseded decisions

(Empty as of v1. When a decision is superseded, move its entry here with the date and a pointer to the new decision.)
