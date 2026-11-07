"""Safe Export: the four-zone battery export predictor, run inside Home Assistant.

Runs the bundled copy of src/model.py at 18:00 in Home Assistant's time zone (and
on demand) and publishes the P50 safe export, the full prediction detail, and a
run timestamp. Inverter control stays with the existing automations and scripts.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, RUN_HOUR, RUN_MINUTE, SERVICE_RUN
from .predictor import load_coefficients
from .runner import SafeExportRunner

PLATFORMS = [Platform.SENSOR, Platform.BUTTON]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the safe_export.run action."""

    async def _run(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            await entry.runtime_data.async_run()

    hass.services.async_register(DOMAIN, SERVICE_RUN, _run)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry[SafeExportRunner]) -> bool:
    """Set up Safe Export from a config entry."""
    try:
        coefficients = await hass.async_add_executor_job(load_coefficients)
    except (OSError, ValueError) as err:
        raise ConfigEntryError(f"Invalid coefficients.json: {err}") from err

    runner = SafeExportRunner(hass, entry, coefficients)
    entry.runtime_data = runner

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(
        async_track_time_change(
            hass, runner.async_run, hour=RUN_HOUR, minute=RUN_MINUTE, second=0
        )
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry[SafeExportRunner]) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry[SafeExportRunner]) -> None:
    """Reload after the options flow changes the input sensors or capacity."""
    await hass.config_entries.async_reload(entry.entry_id)
