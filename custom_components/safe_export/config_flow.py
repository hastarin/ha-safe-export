"""Config flow for Safe Export: pick the five input sensors and the battery capacity."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_HUMIDITY_ENTITY,
    CONF_MIN_SOC_ENTITY,
    CONF_SOC_ENTITY,
    CONF_SOLCAST_ENTITY,
    CONF_TEMP_ENTITY,
    DOMAIN,
    INPUT_ENTITY_KEYS,
)

_SENSOR = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
_NUMERIC = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=["sensor", "number", "input_number"])
)
_CAPACITY = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0.1,
        max=1000,
        step=0.1,
        unit_of_measurement="kWh",
        mode=selector.NumberSelectorMode.BOX,
    )
)


def _schema(defaults: Mapping[str, Any]) -> vol.Schema:
    def key(name: str) -> vol.Required:
        if name in defaults:
            return vol.Required(name, default=defaults[name])
        return vol.Required(name)

    return vol.Schema(
        {
            key(CONF_TEMP_ENTITY): _SENSOR,
            key(CONF_HUMIDITY_ENTITY): _SENSOR,
            key(CONF_SOLCAST_ENTITY): _SENSOR,
            key(CONF_SOC_ENTITY): _SENSOR,
            key(CONF_MIN_SOC_ENTITY): _NUMERIC,
            key(CONF_BATTERY_CAPACITY_KWH): _CAPACITY,
        }
    )


def _validate(user_input: Mapping[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    chosen = [user_input[k] for k in INPUT_ENTITY_KEYS]
    if len(set(chosen)) != len(chosen):
        errors["base"] = "duplicate_entity"
    return errors


class SafeExportConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                return self.async_create_entry(title="Safe Export", data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=_schema(user_input or {}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SafeExportOptionsFlow:
        return SafeExportOptionsFlow()


class SafeExportOptionsFlow(OptionsFlow):
    """Change the input sensors or battery capacity after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                return self.async_create_entry(data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init", data_schema=_schema(user_input or current), errors=errors
        )
