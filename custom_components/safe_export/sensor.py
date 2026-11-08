"""Safe Export sensors: P50 safe export, prediction detail, and last run time."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoredExtraData, RestoreEntity
from homeassistant.util import dt as dt_util

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


class _RestoringSensor(SafeExportEntity, SensorEntity, RestoreEntity):
    """A sensor that keeps its last published value across a Home Assistant restart.

    Until the first run after a restart the previous run's value is shown with
    `restored: true`; a fresh run replaces it and sets `restored: false`. Anything
    acting on the value (the copy automation) must require `restored` to be false,
    or it will act on a previous night's decision.
    """

    _restored: dict[str, Any] | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        stored = await self.async_get_last_extra_data()
        if stored is None:
            return
        data = stored.as_dict()
        if self._valid_restored(data.get("value")):
            self._restored = data

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        if self.coordinator.data is not None:
            return RestoredExtraData(
                {"value": self._live_stored_value(), "attributes": self._live_attributes()}
            )
        # Not run since the restart: pass on what was restored, so a second restart
        # before the next run does not lose it.
        return RestoredExtraData(self._restored or {})

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is not None:
            return self._live_value()
        if self._restored is not None:
            return self._restored_value(self._restored["value"])
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.coordinator.data is not None:
            return {**self._live_attributes(), "restored": False}
        if self._restored is not None:
            return {**self._restored.get("attributes", {}), "restored": True}
        return None

    def _valid_restored(self, value: Any) -> bool:
        raise NotImplementedError

    def _restored_value(self, value: Any) -> Any:
        return value

    def _live_value(self) -> Any:
        raise NotImplementedError

    def _live_stored_value(self) -> Any:
        return self._live_value()

    def _live_attributes(self) -> dict[str, Any]:
        return {}


class SafeExportP50Sensor(_RestoringSensor):
    """P50 safe export in Wh: the number the export chain acts on.

    A measurement with a state_class, so it reaches long-term statistics and a
    past night's decision can still be audited after the states table is purged.
    """

    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def _valid_restored(self, value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

    def _live_value(self) -> int:
        return self.coordinator.data.safe_export_wh


class SafeExportDetailSensor(_RestoringSensor):
    """The zone as state; every input, confidence level and the reason as attributes."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ZONES

    def _valid_restored(self, value: Any) -> bool:
        return value in ZONES

    def _live_value(self) -> str:
        return self.coordinator.data.zone

    def _live_attributes(self) -> dict[str, Any]:
        return dict(self.coordinator.data.detail)


class SafeExportLastRunSensor(_RestoringSensor):
    """When the last run finished. Changes on every run, even if the result does not."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def _valid_restored(self, value: Any) -> bool:
        return isinstance(value, str) and dt_util.parse_datetime(value) is not None

    def _restored_value(self, value: Any) -> datetime | None:
        return dt_util.parse_datetime(value)

    def _live_value(self) -> datetime:
        return self.coordinator.data.updated_at

    def _live_stored_value(self) -> str:
        return self.coordinator.data.updated_at.isoformat()
