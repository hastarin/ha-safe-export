"""Setup, entities, scheduling and fail-closed behaviour of the integration."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.safe_export.const import DOMAIN

from .common import SOC, SOLCAST, set_inputs

pytestmark = pytest.mark.asyncio

P50 = "sensor.safe_export_p50"
DETAIL = "sensor.safe_export_detail"
LAST_RUN = "sensor.safe_export_last_run"
RUN_NOW = "button.safe_export_run_now"


async def _press(hass: HomeAssistant) -> None:
    await hass.services.async_call("button", "press", {"entity_id": RUN_NOW}, blocking=True)
    await hass.async_block_till_done()


async def test_entities_created_and_unknown_until_first_run(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    assert setup_entry.state is ConfigEntryState.LOADED
    registry = er.async_get(hass)
    for entity_id in (P50, DETAIL, LAST_RUN, RUN_NOW):
        assert registry.async_get(entity_id) is not None, entity_id

    # Nothing is restored after a restart: no stale value until the next run.
    for entity_id in (P50, DETAIL, LAST_RUN):
        assert hass.states.get(entity_id).state == "unknown"

    p50 = hass.states.get(P50)
    assert p50.attributes["unit_of_measurement"] == "Wh"
    assert p50.attributes["state_class"] == "measurement"


async def test_run_now_publishes_prediction(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    # 15 °C, Solcast 30 kWh, SoC 100 %, floor 10 %, 13.8 kWh:
    # consumption = 22.5759 − 0.9593×15 − 0.016243×30 = 7.69911 kWh
    # available = 0.9 × 13.8 = 12.42 kWh
    # P50 = 12.42 − 7.69911 − 2.649×0.33 = 3.84672 kWh → 3847 Wh
    # P95 = 12.42 − 7.69911 − 2.649     = 2.07189 kWh → 2072 Wh
    await _press(hass)

    assert hass.states.get(P50).state == "3847"
    detail = hass.states.get(DETAIL)
    assert detail.state == "heating"
    assert detail.attributes["model_variant"] == "heating_with_solcast"
    assert detail.attributes["p50"]["safe_export"] == 3847
    assert detail.attributes["p95"]["safe_export"] == 2072
    assert detail.attributes["available_kwh"] == 12.42
    assert detail.attributes["reason"] is None
    assert hass.states.get(LAST_RUN).state != "unknown"


async def test_run_action_publishes_prediction(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    await hass.services.async_call(DOMAIN, "run", blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(P50).state == "3847"


async def test_solcast_in_wh_is_converted(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    set_inputs(hass, solcast=30000, solcast_unit="Wh")
    await _press(hass)
    assert hass.states.get(P50).state == "3847"


@pytest.mark.parametrize("bad", ["unavailable", "unknown", "not-a-number"])
async def test_missing_required_input_fails_closed(
    hass: HomeAssistant, setup_entry: MockConfigEntry, bad: str
) -> None:
    await _press(hass)
    assert hass.states.get(P50).state == "3847"
    first_run = hass.states.get(LAST_RUN).state

    set_inputs(hass, soc=bad)
    with patch(
        "custom_components.safe_export.runner.dt_util.now",
        return_value=dt_util.now() + timedelta(minutes=1),
    ):
        await _press(hass)

    # The previous good value is replaced by 0, never left in place.
    assert hass.states.get(P50).state == "0"
    detail = hass.states.get(DETAIL)
    assert detail.state == "error"
    assert "soc" in detail.attributes["reason"]
    for level in ("p50", "p75", "p90", "p95"):
        assert detail.attributes[level]["safe_export"] == 0
    assert hass.states.get(LAST_RUN).state != first_run


async def test_missing_input_entity_fails_closed(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    hass.states.async_remove(SOC)
    await _press(hass)
    assert hass.states.get(P50).state == "0"
    assert hass.states.get(DETAIL).state == "error"


async def test_model_exception_fails_closed(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    await _press(hass)
    with patch(
        "custom_components.safe_export.predictor.predict", side_effect=RuntimeError("boom")
    ):
        await _press(hass)
    assert hass.states.get(P50).state == "0"
    detail = hass.states.get(DETAIL)
    assert detail.state == "error"
    assert "boom" in detail.attributes["reason"]


async def test_solcast_unavailable_falls_back_to_temp_only(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    hass.states.async_set(SOLCAST, "unavailable")
    await _press(hass)
    detail = hass.states.get(DETAIL)
    assert detail.state == "heating"
    assert detail.attributes["model_variant"] == "heating_temp_only"
    # 22.4741 − 1.0043×15 = 7.4096; 12.42 − 7.4096 − 0.87417 = 4.13623 → 4136 Wh
    assert hass.states.get(P50).state == "4136"


async def test_humidity_unavailable_falls_back_to_temp_only(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    set_inputs(hass, temp=25.0, humidity="unavailable")
    await _press(hass)
    detail = hass.states.get(DETAIL)
    assert detail.state == "cooling"
    assert detail.attributes["model_variant"] == "cooling_temp_only"


async def test_runs_at_1800_local_time(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    assert hass.states.get(P50).state == "unknown"

    now = dt_util.now()
    six_pm = now.replace(hour=18, minute=0, second=0, microsecond=0)
    if six_pm <= now:
        six_pm += timedelta(days=1)

    async_fire_time_changed(hass, six_pm - timedelta(minutes=1))
    await hass.async_block_till_done()
    assert hass.states.get(P50).state == "unknown"

    async_fire_time_changed(hass, six_pm)
    await hass.async_block_till_done()
    assert hass.states.get(P50).state == "3847"
    run_at = datetime.fromisoformat(hass.states.get(LAST_RUN).state)
    assert run_at.tzinfo is not None


async def test_bad_coefficients_fail_setup(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    set_inputs(hass)
    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.safe_export.load_coefficients",
        side_effect=ValueError("missing keys"),
    ):
        assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_unload(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert setup_entry.state is ConfigEntryState.NOT_LOADED
