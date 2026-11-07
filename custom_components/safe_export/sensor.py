"""Safe Export sensors: P50 safe export, prediction detail, and last run time."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ZONES
from .entity import SafeExportEntity
from .runner import SafeExportRunner


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[SafeExportRunner],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runner = entry.runtime_data
    async_add_entities(
        [
            SafeExportP50Sensor(runner, "p50"),
            SafeExportDetailSensor(runner, "detail"),
            SafeExportLastRunSensor(runner, "last_run"),
        ]
    )


class SafeExportP50Sensor(SafeExportEntity, SensorEntity):
    """P50 safe export in Wh: the number the export chain acts on.

    A measurement with a state_class, so it reaches long-term statistics and a
    past night's decision can still be audited after the states table is purged.
    """

    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    @property
    def native_value(self) -> int | None:
        return None if self.coordinator.data is None else self.coordinator.data.safe_export_wh


class SafeExportDetailSensor(SafeExportEntity, SensorEntity):
    """The zone as state; every input, confidence level and the reason as attributes."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ZONES

    @property
    def native_value(self) -> str | None:
        return None if self.coordinator.data is None else self.coordinator.data.zone

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return None if self.coordinator.data is None else self.coordinator.data.detail


class SafeExportLastRunSensor(SafeExportEntity, SensorEntity):
    """When the last run finished. Changes on every run, even if the result does not."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        return None if self.coordinator.data is None else self.coordinator.data.updated_at
