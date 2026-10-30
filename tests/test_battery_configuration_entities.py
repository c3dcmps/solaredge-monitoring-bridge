"""Four read-only HA sensors, endpoint isolation and five-minute refresh."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_battery_configuration import _mode, _parse, _reserve
from test_ev_entities import (
    _Coordinator,
    _CoordinatorEntity,
    _load_boundary,
    _UpdateFailed,
)
from test_portal_client import _load

KEYS = ("mode", "reserve_pct", "manual_count", "tariff_count")


def _module():
    return _load_boundary(
        "battery_configuration_sensor.py",
        DataUpdateCoordinator=_Coordinator,
        CoordinatorEntity=_CoordinatorEntity,
        SensorEntity=type("Sensor", (), {}),
        UpdateFailed=_UpdateFailed,
        BatteryConfigurationSnapshot=_load(
            "battery_configuration"
        ).BatteryConfigurationSnapshot,
        DOMAIN="solaredge_one_bridge",
        timedelta=timedelta,
        SolarEdgePortalError=_load("portal_client").SolarEdgePortalError,
        _LOGGER=None,
        DEVICE_INFO={"identifiers": {("solaredge_one_bridge", "bridge")}},
        SENSOR_DESCRIPTIONS=[SimpleNamespace(key=key) for key in KEYS],
    )


def _entities(module, coordinator):
    return [
        module["SolarEdgeBatteryConfigurationSensor"](
            coordinator, "entry-1", SimpleNamespace(key=key)
        )
        for key in KEYS
    ]


def test_four_sensors_show_stored_programs_and_requested_reserve():
    module = _module()
    coordinator = SimpleNamespace(
        data=_parse(_mode(), _reserve()), last_update_success=True
    )
    mode, reserve, manual, tariff = _entities(module, coordinator)
    assert [e.native_value for e in (mode, reserve, manual, tariff)] == [
        "MANUAL_TOU",
        15,
        2,
        2,
    ]
    assert all(e.available for e in (mode, reserve, manual, tariff))
    assert len({e._attr_unique_id for e in (mode, reserve, manual, tariff)}) == 4
    assert manual.extra_state_attributes["selected"] is True
    assert tariff.extra_state_attributes["selected"] is False
    assert manual.extra_state_attributes["schedules"][0]["start"] == "16:00"
    assert tariff.extra_state_attributes["schedules"][0]["start"] == "07:00"
    assert reserve.extra_state_attributes["minimum_reserve_pct"] == 0
    assert (
        reserve.extra_state_attributes["weather_guard_status"]
        == "NOT_AVAILABLE_DUE_TO_REGION"
    )
    assert "backup_enabled" not in reserve.extra_state_attributes
    assert "private-secret" not in repr(
        [e.extra_state_attributes for e in (mode, reserve)]
    )


def test_endpoint_failure_hides_only_affected_entities_and_attributes():
    module = _module()
    coordinator = SimpleNamespace(
        data=_parse(_mode(), _reserve()), last_update_success=True
    )
    entities = _entities(module, coordinator)
    coordinator.data = _parse(None, _reserve())
    assert [e.available for e in entities] == [False, True, False, False]
    assert entities[0].extra_state_attributes == {}
    coordinator.data = _parse(_mode(), None)
    assert [e.available for e in entities] == [True, False, True, True]
    assert entities[1].extra_state_attributes == {}
    coordinator.last_update_success = False
    assert not any(e.available for e in entities)
    assert all(e.extra_state_attributes == {} for e in entities)


def test_refresh_recovers_at_five_minutes_without_fake_values():
    async def exercise():
        module = _module()
        client = SimpleNamespace(
            async_get_battery_configuration=AsyncMock(
                side_effect=[
                    _parse(),
                    _parse(_mode(), _reserve()),
                    _parse(None, _reserve()),
                ]
            )
        )
        coordinator = module["SolarEdgeBatteryConfigurationCoordinator"](
            None, None, client
        )
        assert coordinator.update_interval == timedelta(minutes=5)
        entities = _entities(module, coordinator)
        assert not any(e.available for e in entities)
        await coordinator.async_refresh()
        assert not any(e.available for e in entities)
        await coordinator.async_refresh()
        assert all(e.available for e in entities)
        await coordinator.async_refresh()
        assert entities[1].available and not entities[0].available

    asyncio.run(exercise())


def test_setup_creates_four_entities_once_in_portal_and_none_in_bridge():
    async def exercise():
        module = _module()
        entry = SimpleNamespace(
            entry_id="entry-1",
            runtime_data=SimpleNamespace(
                battery_configuration_coordinator=SimpleNamespace()
            ),
        )
        entities = []
        await module["async_setup_battery_configuration_sensors"](
            None, entry, entities.extend
        )
        assert len(entities) == 4
        entry.runtime_data.battery_configuration_coordinator = None
        no_entities = []
        await module["async_setup_battery_configuration_sensors"](
            None, entry, no_entities.extend
        )
        assert no_entities == []

    asyncio.run(exercise())


def test_unknown_mode_does_not_guess_whether_a_program_is_selected():
    payload = _mode()
    payload["batteryMode"] = "FUTURE_MODE"
    module = _module()
    coordinator = SimpleNamespace(data=_parse(payload), last_update_success=True)
    _, _, manual, tariff = _entities(module, coordinator)
    assert manual.extra_state_attributes["selected"] is None
    assert tariff.extra_state_attributes["selected"] is None
