"""Safe Export "Run now" button."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import SafeExportEntity
from .runner import SafeExportRunner


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[SafeExportRunner],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([SafeExportRunNowButton(entry.runtime_data, "run_now")])


class SafeExportRunNowButton(SafeExportEntity, ButtonEntity):
    """Run a prediction immediately, outside the 18:00 schedule."""

    async def async_press(self) -> None:
        await self.coordinator.async_run()
