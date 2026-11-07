"""Read the configured input entities and publish a prediction to the entities."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_HUMIDITY_ENTITY,
    CONF_MIN_SOC_ENTITY,
    CONF_SOC_ENTITY,
    CONF_SOLCAST_ENTITY,
    CONF_TEMP_ENTITY,
    DOMAIN,
)
from .predictor import ModelInputs, RunResult, fail_closed, run_prediction

_LOGGER = logging.getLogger(__name__)

# Solcast's forecast_tomorrow is reported in kWh; convert anything else we recognise.
_ENERGY_TO_KWH = {"kWh": 1.0, "Wh": 0.001, "MWh": 1000.0}


def entry_settings(entry: ConfigEntry) -> dict[str, Any]:
    """Config entry data with any options-flow changes applied on top."""
    return {**entry.data, **entry.options}


def _number(state: State | None) -> float | None:
    """A state's numeric value, or None if missing, unknown, unavailable or non-numeric."""
    if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, ""):
        return None
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _energy_kwh(state: State | None) -> float | None:
    """An energy state in kWh. A missing unit is taken as kWh, as the Node-RED flow does."""
    value = _number(state)
    if value is None or state is None:
        return None
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) or "kWh"
    factor = _ENERGY_TO_KWH.get(unit)
    if factor is None:
        _LOGGER.warning(
            "%s has unsupported unit %r; treating Solcast as unavailable", state.entity_id, unit
        )
        return None
    return value * factor


def read_inputs(hass: HomeAssistant, settings: Mapping[str, Any]) -> ModelInputs:
    """Read the five configured input entities."""
    get = hass.states.get
    return ModelInputs(
        temp=_number(get(settings[CONF_TEMP_ENTITY])),
        humidity=_number(get(settings[CONF_HUMIDITY_ENTITY])),
        solcast_kwh=_energy_kwh(get(settings[CONF_SOLCAST_ENTITY])),
        soc=_number(get(settings[CONF_SOC_ENTITY])),
        min_soc=_number(get(settings[CONF_MIN_SOC_ENTITY])),
    )


class SafeExportRunner(DataUpdateCoordinator[RunResult]):
    """Holds the latest run result and notifies the entities when it changes.

    There is no polling: a run happens at 18:00, on the button, or on the
    safe_export.run action. Until the first run after a restart the entities are
    unknown rather than restoring yesterday's value, so nothing stale is published.
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, coefficients: dict[str, float]
    ) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=None)
        self.coefficients = coefficients

    async def async_run(self, now: datetime | None = None) -> RunResult:
        """Run a prediction now and publish it. Never raises."""
        when = dt_util.now()
        settings = entry_settings(self.config_entry)
        inputs: ModelInputs | None = None
        try:
            inputs = read_inputs(self.hass, settings)
            result = run_prediction(
                inputs,
                self.coefficients,
                float(settings[CONF_BATTERY_CAPACITY_KWH]),
                when,
            )
        except Exception as err:  # never leave the previous night's value in place
            result = fail_closed(inputs, f"Run error: {err!r}", when)

        if result.ok:
            _LOGGER.info(
                "Safe export %s Wh (zone %s, %s)",
                result.safe_export_wh,
                result.zone,
                result.detail["model_variant"],
            )
        else:
            _LOGGER.error("Safe export failed closed: %s", result.detail["reason"])
        self.async_set_updated_data(result)
        return result
