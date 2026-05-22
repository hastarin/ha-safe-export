"""Shared test fixtures."""

from datetime import date
from zoneinfo import ZoneInfo

import pytest

from src.config import AbsencePeriod, Config, ModelConfig, ProviderPeriod, SensorConfig


@pytest.fixture(scope="session")
def test_cfg() -> Config:
    """Config matching the hardcoded constants from the original extract.py."""
    return Config(
        battery_capacity_wh=13800.0,
        battery_reserve_fraction=0.10,
        timezone=ZoneInfo("Australia/Melbourne"),
        sensors=SensorConfig(
            battery_soc="sensor.byd_battery_box_premium_hv_state_of_charge",
            pv="sensor.solarnet_power_photovoltaics",
            load="sensor.solarnet_power_load",
            grid_import="sensor.smart_meter_63a_1_real_energy_consumed",
            grid_export="sensor.smart_meter_63a_1_real_energy_produced",
            battery_charged="sensor.battery_energy_charged",
            battery_discharged="sensor.battery_energy_discharged",
            outdoor_temp="sensor.netatmo_outdoor_temperature",
            indoor_temp="sensor.netatmo_indoor_temperature",
            weather_temp="sensor.laverton_temp",
            weather_feels_like="sensor.laverton_temp_feels_like",
            weather_rain="sensor.laverton_rain_since_9am",
            weather_wind="sensor.laverton_wind_speed_kilometre",
            weather_gust="sensor.laverton_gust_speed_kilometre",
            weather_humidity="sensor.laverton_humidity",
            solcast="sensor.solcast_pv_forecast_forecast_tomorrow",
            guests="sensor.hastguests",
            median_temp="sensor.median_temperature",
            median_humidity="sensor.median_humidity",
        ),
        providers=[
            ProviderPeriod(name="ea", start_date=date(2023, 11, 28)),
            ProviderPeriod(name="amber", start_date=date(2025, 8, 16)),
            ProviderPeriod(name="globird", start_date=date(2026, 5, 5)),
        ],
        absence_periods=[
            AbsencePeriod(start=date(2025, 9, 28), end=date(2025, 11, 3)),
        ],
        data_gap_dates=frozenset([
            date(2026, 2, 22),
            date(2026, 2, 23),
            date(2026, 2, 24),
            date(2026, 5, 5),
            date(2026, 5, 6),
        ]),
        # Kept in sync with config.yaml — refit 2026-05-22 (tools/retrain.py).
        model=ModelConfig(
            heating_intercept=22.5759,
            heating_b_temp=-0.9593,
            heating_b_solcast=-0.016243,
            heating_temp_only_intercept=22.4741,
            heating_temp_only_b_temp=-1.0043,
            cooling_intercept=-9.6822,
            cooling_b_temp=0.7163,
            cooling_b_humidity=0.028676,
            cooling_temp_only_intercept=-5.1760,
            cooling_temp_only_b_temp=0.5965,
            mild_p50=6.796,
            mild_p75=7.840,
            mild_p90=8.514,
            mild_p95=9.002,
            warm_boundary_p50=5.979,
            warm_boundary_p75=6.808,
            warm_boundary_p90=7.783,
            warm_boundary_p95=8.783,
            heating_p95_buffer_kwh=2.649,
            cooling_p95_buffer_kwh=2.431,
        ),
    )
