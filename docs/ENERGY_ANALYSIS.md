# Energy Consumption Prediction Model Analysis

## Overview

Analysis of 845 non-hospital nights of energy consumption data to build predictive models for overnight HVAC load and grid import requirements. The goal is to predict consumption accurately enough to make daily export decisions during evening peak periods while maintaining safe battery reserves.

## Key Findings

### The U-Shaped Consumption Curve

Overnight consumption is driven primarily by HVAC (heating and cooling), creating a U-shaped relationship with temperature:

- **Heating load dominates** below ~15°C: consumption increases sharply as temperature drops
- **Sweet spot** at 15-19°C: HVAC minimally active, consumption ~4.7-5 kWh (background load only)
- **Cooling load dominates** above ~23°C: consumption climbs again as temperature rises
- **Minimum consumption** observed at 17°C mean temperature: ~4.68 kWh average

This U-shape means a single linear model across all temperatures is fundamentally wrong (R² = 0.44). Temperature and Solcast together don't capture cooling well because AC is discretionary and noisy.

### Three-Zone Model Architecture

Instead of one model, use three separate models optimized for each thermal regime:

#### Zone 1: Heating (Mean temp < 19°C)

- **Sample size:** 414 nights (good)
- **Model:** `consumption_kwh = 18.754 − (0.717 × mean_temp) − (0.049 × solcast_kwh)`
- **R²:** 0.70 (70% of variance explained — genuinely good)
- **Error percentiles (absolute residuals):**
  - P50 (median): ±1.1 kWh
  - P75: ±2.13 kWh
  - P90: ±3.09 kWh
  - P95: ±3.58 kWh (use as safety buffer)
- **Interpretation:** For every 1°C warmer, consumption drops 0.72 kWh. Higher Solcast (clearer skies) slightly reduces load, probably due to less radiative cooling on clear cold nights.

#### Zone 2: Mild (Mean temp 19–21°C)

- **Sample size:** 59 nights
- **Model:** None — don't model, just use empirical distribution
- **R²:** 0.026 (useless — HVAC isn't running)
- **Percentile distribution (from 59 historical nights):**
  - P50: 5.66 kWh
  - P75: 6.78 kWh
  - P90: 8.02 kWh
  - P95: 8.47 kWh
- **Interpretation:** In the sweet spot, consumption is basically random noise (background load varies by day). Temperature doesn't predict it because temperature isn't driving the load. Use historical percentiles directly.

#### Zone 3: Cooling (Mean temp > 21°C)

- **Sample size:** 48 nights (small, weak)
- **Model:** `consumption_kwh = −6.756 + (0.660 × mean_temp) − (0.058 × solcast_kwh)`
- **R²:** 0.38 (explains 38% of variance — moderate, but limited by small sample)
- **Error percentiles (absolute residuals):**
  - P50 (median): ±1.1 kWh
  - P75: ±2.01 kWh
  - P90: ±3.39 kWh
  - P95: ±3.59 kWh
- **Interpretation:** AC load increases with temperature (0.66 kWh per °C). The cooling model is weaker than heating because: (1) fewer hot nights in dataset, (2) AC is discretionary — you might choose to run it or not, and (3) humidity (not yet in model) probably matters more than temperature for cooling. **Expect this model to improve significantly once you have a full summer of data.**

## Variable Analysis

### Strong Predictors

| Variable                       | Correlation | Notes                                                |
| ------------------------------ | ----------- | ---------------------------------------------------- |
| `median_indoor_temp`           | -0.654      | Strongest single predictor, but NULL before Jan 2024 |
| `bom_temp_mean`                | -0.628      | Average overnight temp, best temperature metric      |
| `min_outdoor_temp`             | -0.640      | Your existing forecast variable                      |
| `bom_feels_like_min`           | -0.626      | Wind chill, similar to temp_mean                     |
| `solcast_forecast_tomorrow_wh` | -0.459      | Cloud cover proxy (522 nights, starting Oct 2024)    |
| `solar_wh_before_11am`         | -0.418      | Actual solar generation (retrospective)              |

### Weak/Useless Predictors

| Variable          | Correlation | Notes                                                   |
| ----------------- | ----------- | ------------------------------------------------------- |
| `bom_rain_max`    | +0.006      | Completely useless — keep for reference but don't model |
| `bom_wind_mean`   | -0.076      | Negligible signal                                       |
| `bom_gust_max`    | -0.168      | Weak                                                    |
| `bom_temp_min`    | -0.225      | Surprisingly weak (temp_mean is better)                 |
| `avg_indoor_temp` | -0.153      | Captures outcome, not driver                            |

### Not Yet Analyzed (Plan to Add)

- `bom_humidity_mean` — Expected to help cooling model (humidity drives AC load)
- `bom_humidity_max` — Peak humidity, might matter more than average
- `median_indoor_humidity` — Captures occupancy/cooking load

## Data Coverage and Gaps

### Overall Dataset

- **Total nights:** 882
- **Non-hospital nights:** 845 (37 hospital nights excluded — HVAC off, ~3 kWh baseline)
- **Date range:** Nov 29, 2023 to May 4, 2026
- **Summer coverage (heating irrelevant):** Excluded from analysis

### Solcast Availability

- **Available from:** Oct 19, 2024 onward (559 nights)
- **By zone:**
  - Heating with Solcast: ~300 nights (good)
  - Cooling with Solcast: 48 nights (weak — only one summer so far)
  - Mild with Solcast: ~59 nights

### Indoor Temp (median_indoor_temp)

- **Available from:** Jan 2024 onward (804 nights)
- **Missing:** First 2 months (Dec 2023)

### Humidity

- **Not yet captured** — needs to be added going forward
- **Worth adding because:** Humidity is the second-order variable that explains cooling model variance once you have multi-year data

## Hospital Period Baseline

37 nights when the user was in hospital (HVAC off, minimal occupancy):

- **Average consumption:** 3.08 kWh
- **Range:** -1.01 to 8.81 kWh
- **Temperature range:** 9–19.4°C (all mild/shoulder season)
- **Interpretation:** ~3 kWh represents background load (fridge, router, standby, some activity from mother on some nights). The range suggests the house was sometimes empty (<1 kWh) and sometimes occupied with guest (3-8 kWh). Not clean enough to extract a precise baseline, but confirms HVAC is the dominant load on non-mild nights.

## Model Validation Status

**Not yet validated** — models built on same data they're tested against (in-sample R²). Should hold back 20% of nights and validate on held-back test set before using for real export decisions. Expect real-world R² to be 5-10 percentage points lower than reported.

## Export Strategy Implications

### Breakeven Temperatures

Using the heating model with 92% SOC, 10% min SOC = 11.3 kWh available discharge:

- **Below 8°C:** Always grid import required, no export possible
- **8-10°C:** Marginal export (0.5-3 kWh) — conservative only
- **10-12°C:** Growing export window (2-4 kWh)
- **Above 12°C:** Solid export opportunity (4+ kWh)
- **19-21°C (sweet spot):** Maximum export flexibility (8+ kWh surplus likely)

### Safe Export Framework

For a given night's forecast, predicted consumption uses the appropriate model, then:

- **Available discharge** = (6pm SOC − min SOC) × 13.8 kWh
- **Grid import** = max(0, consumption − available discharge)
- **Safe export** = max(0, available discharge − consumption − error_buffer)

Use P95 error buffer (±3.6 kWh for heating/cooling) for conservative overnight planning. P50 for aggressive peak export decisions if you're comfortable with occasional grid import.

## Known Limitations and Caveats

1. **Heating model is robust, cooling model is weak** — Only 48 hot nights, and AC is discretionary. Will improve after a full summer.

2. **Linear models don't capture the U-shape perfectly** — Mild zone doesn't follow the regression at all (R²≈0). Using empirical percentiles there instead is correct.

3. **Solcast is a proxy for cloud cover** — Good signal (r=-0.42) but imperfect. On clear cold nights consumption is higher (more radiative cooling), on cloudy nights lower. Once humidity is added, might reduce importance of Solcast.

4. **Humidity not yet integrated** — Expected to improve cooling model from R²=0.38 to something better once you have 100+ hot nights and humidity data.

5. **Behaviour can shift the model** — If you start exporting aggressively based on this model and therefore accepting more grid import on cold nights, you might adjust your comfort setpoint or usage patterns, which changes the underlying consumption patterns. Models built on past behaviour assume behaviour is stable.

6. **Time-of-night variation not captured** — This model predicts total overnight consumption but doesn't distinguish peak vs off-peak hours. You might be able to export more aggressively if you know the peak period (6-9pm?) has different load than post-midnight.

## Recommended Next Steps

### Short Term (Next Few Weeks)

1. Add `bom_humidity_mean`, `bom_humidity_max`, and `median_indoor_humidity` columns to dataset
2. Use the HTML widget tool for daily export decisions (P50 for median estimate, add P95 safety buffer manually if unsure)
3. Start logging which nights you actually export, how much, and how close the predictions were

### Medium Term (Every 1-2 Months)

1. Validate the heating model against held-back 20% test set
2. Check if any new patterns emerge (day-of-week effects, seasonal shifts)
3. Monitor cooling model performance — should improve as you accumulate more hot nights

### Before Next Summer (Late Spring)

1. Re-run the full analysis with another full winter of Solcast data
2. Integrate humidity correlation analysis — should show whether it tightens the cooling model
3. Consider time-of-night breakdown (peak vs off-peak consumption) as a refinement
4. Update the models if coefficients shift significantly

### Lower Priority (Only If It Pays Off)

- Machine learning models (random forests, etc.) — only worth it if you get to 1000+ nights and want to squeeze the last 5% R²
- Day-of-week and seasonal decomposition
- Heat pump efficiency vs outdoor humidity curves (very detailed, probably overkill)

## Model Equations (For Implementation)

### Heating Zone (mean_temp < 19°C)

```text
consumption_kwh = 18.754 − (0.717 × mean_temp) − (0.049 × solcast_kwh)
error_buffer_kwh = 3.58  # P95 confidence
```

### Mild Zone (19°C ≤ mean_temp ≤ 21°C)

```text
# Use empirical percentiles, don't model
p50 = 5.66
p75 = 6.78
p90 = 8.02
p95 = 8.47
```

### Cooling Zone (mean_temp > 21°C)

```text
consumption_kwh = −6.756 + (0.660 × mean_temp) − (0.058 × solcast_kwh)
error_buffer_kwh = 3.59  # P95 confidence
```

## Data Quality Notes

- **Negative consumption values:** A few (~3 instances) appear in hospital period. Likely data collection artifacts or solar export being counted as negative import. Don't worry about these for modelling.
- **Solcast NULL values:** Data only from Oct 2024 onward. Older nights show NULL.
- **Indoor temp NULL values:** Data only from Jan 2024 onward.
- **Hospital period excluded:** 37 nights with very different load profile. Keep separate for baseline reference only.

## Files and References

- **Dataset:** `daily_observations` table in SQLite, 29 columns
- **Interactive widget:** `overnight_predictor_v3.html` (downloaded from Claude)
- **This document:** Runbook for Claude Code exploration and further analysis
