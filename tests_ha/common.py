"""Shared helpers and fixtures for the integration tests (needs the HA harness)."""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.safe_export.const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_HUMIDITY_ENTITY,
    CONF_MIN_SOC_ENTITY,
    CONF_SOC_ENTITY,
    CONF_SOLCAST_ENTITY,
    CONF_TEMP_ENTITY,
    DOMAIN,
)

TEMP = "sensor.test_forecast_temp_mean"
HUMIDITY = "sensor.test_forecast_humidity_mean"
SOLCAST = "sensor.test_solcast_tomorrow"
SOC = "sensor.test_battery_soc"
MIN_SOC = "sensor.test_battery_soc_minimum"

ENTRY_DATA: dict[str, Any] = {
    CONF_TEMP_ENTITY: TEMP,
    CONF_HUMIDITY_ENTITY: HUMIDITY,
    CONF_SOLCAST_ENTITY: SOLCAST,
    CONF_SOC_ENTITY: SOC,
    CONF_MIN_SOC_ENTITY: MIN_SOC,
    CONF_BATTERY_CAPACITY_KWH: 13.8,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load custom_components/ in every test."""


def set_inputs(
    hass: HomeAssistant,
    *,
    temp: str | float = 15.0,
    humidity: str | float = 60.0,
    solcast: str | float = 30.0,
    solcast_unit: str = "kWh",
    soc: str | float = 100.0,
    min_soc: str | float = 10.0,
) -> None:
    """Set the five input states (pass "unavailable"/"unknown" to simulate an outage)."""
    hass.states.async_set(TEMP, str(temp), {"unit_of_measurement": "°C"})
    hass.states.async_set(HUMIDITY, str(humidity), {"unit_of_measurement": "%"})
    hass.states.async_set(SOLCAST, str(solcast), {"unit_of_measurement": solcast_unit})
    hass.states.async_set(SOC, str(soc), {"unit_of_measurement": "%"})
    hass.states.async_set(MIN_SOC, str(min_soc), {"unit_of_measurement": "%"})


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, title="Safe Export", data=dict(ENTRY_DATA))


@pytest_asyncio.fixture
async def setup_entry(hass: HomeAssistant, config_entry: MockConfigEntry) -> MockConfigEntry:
    """A loaded Safe Export entry with default (heating-zone) inputs in place."""
    await hass.config.async_set_time_zone("Australia/Melbourne")
    set_inputs(hass)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
