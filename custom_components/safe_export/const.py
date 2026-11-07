"""Constants for the Safe Export integration."""

from __future__ import annotations

DOMAIN = "safe_export"

CONF_TEMP_ENTITY = "temp_entity"
CONF_HUMIDITY_ENTITY = "humidity_entity"
CONF_SOLCAST_ENTITY = "solcast_entity"
CONF_SOC_ENTITY = "soc_entity"
CONF_MIN_SOC_ENTITY = "min_soc_entity"
CONF_BATTERY_CAPACITY_KWH = "battery_capacity_kwh"

INPUT_ENTITY_KEYS = (
    CONF_TEMP_ENTITY,
    CONF_HUMIDITY_ENTITY,
    CONF_SOLCAST_ENTITY,
    CONF_SOC_ENTITY,
    CONF_MIN_SOC_ENTITY,
)

# The live 6pm run, in Home Assistant's configured time zone.
RUN_HOUR = 18
RUN_MINUTE = 0

SERVICE_RUN = "run"

ZONE_ERROR = "error"
ZONES = ["heating", "warm_boundary", "mild", "cooling", ZONE_ERROR]
