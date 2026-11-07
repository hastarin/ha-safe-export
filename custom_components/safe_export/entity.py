"""Shared base entity for Safe Export."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .runner import SafeExportRunner


class SafeExportEntity(CoordinatorEntity[SafeExportRunner]):
    """Groups every Safe Export entity under one service device named "Safe Export".

    With has_entity_name, the device name prefixes each entity's name, which gives
    entity IDs such as sensor.safe_export_p50 and button.safe_export_run_now.
    """

    _attr_has_entity_name = True

    def __init__(self, runner: SafeExportRunner, key: str) -> None:
        super().__init__(runner)
        entry_id = runner.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Safe Export",
            entry_type=DeviceEntryType.SERVICE,
        )
