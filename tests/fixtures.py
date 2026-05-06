"""Known-good validation rows from DATASET.md § Validation samples.

Each entry is keyed by morning date (YYYY-MM-DD) and contains the exact
expected column values. Tests assert within the tolerances in CLAUDE.md:
  ±0.1 for REAL columns (%, °C)
  ±1 for INTEGER columns (Wh)
"""

FIXTURES: dict[str, dict] = {
    # Feb 7, 2026 — AEDT, sunny, full battery, amber, curtailed
    "2026-02-07": {
        "provider": "amber",
        "hospital_period": 0,
        "guests": None,  # sensor didn't exist until 2026-03-08
        "soc_at_6pm": 100.0,
        "min_soc_overnight": 73.9,
        "max_soc_prev_daylight": 100.0,
        "soc_at_11am": 98.9,
        "min_outdoor_temp": 17.6,
        "avg_indoor_temp": 21.7,
        "solar_wh_before_11am": 9612,
        "consumption_wh_load": 4949,
        "grid_import_wh": 24,
        "grid_export_wh": 4677,
        "battery_charged_wh": 3600,
        "battery_discharged_wh": 3398,
        "consumption_wh": 4757,
        "curtailment_likely": 1,
    },
    # Mar 20, 2026 — AEDT, cloudy, deep discharge, amber, no curtailment
    "2026-03-20": {
        "provider": "amber",
        "hospital_period": 0,
        "guests": 0,
        "soc_at_6pm": 63.2,
        "min_soc_overnight": 20.0,
        "max_soc_prev_daylight": 64.4,
        "soc_at_11am": 25.1,
        "min_outdoor_temp": 16.8,
        "avg_indoor_temp": 23.2,
        "solar_wh_before_11am": 2779,
        "consumption_wh_load": 6624,
        "grid_import_wh": 772,
        "grid_export_wh": 6,
        "battery_charged_wh": 3304,
        "battery_discharged_wh": 4954,
        "consumption_wh": 5195,
        "curtailment_likely": 0,
    },
    # Jul 17, 2025 — AEST, winter, depleted, ea, no curtailment
    "2025-07-17": {
        "provider": "ea",
        "hospital_period": 0,
        "guests": None,  # sensor didn't exist until 2026-03-08
        "soc_at_6pm": 58.7,
        "min_soc_overnight": 6.5,
        "max_soc_prev_daylight": 65.7,
        "soc_at_11am": 6.5,
        "min_outdoor_temp": 10.0,
        "avg_indoor_temp": 19.4,
        "solar_wh_before_11am": 1007,
        "consumption_wh_load": 13040,
        "grid_import_wh": 6969,
        "grid_export_wh": 1,
        "battery_charged_wh": 327,
        "battery_discharged_wh": 5051,
        "consumption_wh": 12699,
        "curtailment_likely": 0,
    },
}

# Tolerances from CLAUDE.md
REAL_TOL = 0.1   # %, °C
WH_TOL = 1       # Wh
