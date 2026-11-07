"""Config and options flow tests."""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.safe_export.const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_HUMIDITY_ENTITY,
    CONF_TEMP_ENTITY,
    DOMAIN,
)

from .common import ENTRY_DATA, TEMP, set_inputs

pytestmark = pytest.mark.asyncio


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    set_inputs(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], dict(ENTRY_DATA))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Safe Export"
    assert result["data"] == ENTRY_DATA


async def test_user_flow_rejects_duplicate_entities(hass: HomeAssistant) -> None:
    set_inputs(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**ENTRY_DATA, CONF_HUMIDITY_ENTITY: TEMP}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "duplicate_entity"}


async def test_single_instance(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow_changes_inputs_and_reloads(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    hass.states.async_set("sensor.other_temp", "20.0")
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**ENTRY_DATA, CONF_TEMP_ENTITY: "sensor.other_temp", CONF_BATTERY_CAPACITY_KWH: 10.0},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert setup_entry.options[CONF_TEMP_ENTITY] == "sensor.other_temp"
    assert setup_entry.state is config_entries.ConfigEntryState.LOADED

    # The reloaded runner reads the new sensor: 20 °C is the mild zone.
    await hass.services.async_call(DOMAIN, "run", blocking=True)
    assert hass.states.get("sensor.safe_export_detail").state == "mild"
