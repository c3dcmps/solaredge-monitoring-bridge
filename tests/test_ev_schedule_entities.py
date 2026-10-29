"""Independent schedule polling and read-only Home Assistant entities."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from test_ev_entities import (
    _Coordinator,
    _CoordinatorEntity,
    _device_info,
    _load_boundary,
    _UpdateFailed,
)
from test_ev_schedules import _device, _parse, _slot
from test_portal import SolarEdgePortalError
from test_portal_client import _load

KEY = "a" * 64


def _module():
    return _load_boundary(
        "ev_schedule_sensor.py",
        DataUpdateCoordinator=_Coordinator,
        CoordinatorEntity=_CoordinatorEntity,
        SensorEntity=type("Sensor", (), {}),
        DeviceInfo=_device_info,
        DOMAIN="solaredge_one_bridge",
        timedelta=timedelta,
        UpdateFailed=_UpdateFailed,
        SolarEdgePortalError=SolarEdgePortalError,
        callback=lambda function: function,
        _LOGGER=None,
        EVScheduleSnapshot=_load("ev_schedule").EVScheduleSnapshot,
        SENSOR_DESCRIPTIONS=[
            SimpleNamespace(key=key) for key in ("schedule_count", "next_start")
        ],
    )


def test_schedule_polling_is_five_minutes_and_recovers_without_affecting_ev_controls():
    async def exercise():
        module = _module()
        snapshot = _parse(_device())
        client = SimpleNamespace(
            async_get_ev_schedules=AsyncMock(
                side_effect=[
                    SolarEdgePortalError("private-error"),
                    {KEY: snapshot},
                    {},
                ]
            )
        )
        coordinator = module["SolarEdgeEVScheduleCoordinator"](None, None, client)
        assert coordinator.update_interval == timedelta(seconds=300)
        entity = module["SolarEdgeEVScheduleSensor"](
            coordinator, KEY, SimpleNamespace(key="schedule_count")
        )
        await coordinator.async_refresh()
        assert not entity.available
        assert entity.extra_state_attributes == {}
        await coordinator.async_refresh()
        assert entity.available
        assert entity.native_value == 1
        await coordinator.async_refresh()
        assert not entity.available
        assert entity.native_value is None
        assert entity.extra_state_attributes == {}

    asyncio.run(exercise())


@pytest.mark.parametrize("slots", [[], [_slot(False)]])
def test_empty_and_disabled_schedules_are_available_without_a_fake_next_timestamp(
    slots,
):
    module = _module()
    coordinator = SimpleNamespace(
        data={KEY: _parse(_device(slots))}, last_update_success=True
    )
    entity = module["SolarEdgeEVScheduleSensor"](
        coordinator, KEY, SimpleNamespace(key="next_start")
    )
    assert entity.available
    assert entity.native_value is None
    assert entity.extra_state_attributes["reason"] == "no_enabled_schedule"
    assert entity.extra_state_attributes["source"] is None


def test_schedule_attributes_include_disabled_slots_but_not_private_identifiers():
    module = _module()
    snapshot = _parse(_device([_slot(False), _slot()]))
    coordinator = SimpleNamespace(data={KEY: snapshot}, last_update_success=True)
    entity = module["SolarEdgeEVScheduleSensor"](
        coordinator, KEY, SimpleNamespace(key="schedule_count")
    )
    assert entity.extra_state_attributes["schedules"] == [
        {
            "enabled": False,
            "days": ["THURSDAY", "FRIDAY"],
            "start": "22:00",
            "end": "06:00",
            "crosses_midnight": True,
        },
        {
            "enabled": True,
            "days": ["THURSDAY", "FRIDAY"],
            "start": "22:00",
            "end": "06:00",
            "crosses_midnight": True,
        },
    ]
    assert entity.extra_state_attributes["enabled_count"] == 1
    assert entity.extra_state_attributes["time_zone"] == "Europe/Zurich"
    assert "private" not in repr(entity.extra_state_attributes)
    next_entity = module["SolarEdgeEVScheduleSensor"](
        coordinator, KEY, SimpleNamespace(key="next_start")
    )
    assert next_entity.native_value == snapshot.next_start
    assert next_entity.extra_state_attributes["end"] == snapshot.next_end.isoformat()
    assert next_entity.extra_state_attributes["source"] == "solaredge"


def test_missing_configuration_is_unavailable_and_not_zero():
    module = _module()
    coordinator = SimpleNamespace(
        data={KEY: _parse(_device(deviceTriggers=None))}, last_update_success=True
    )
    entity = module["SolarEdgeEVScheduleSensor"](
        coordinator, KEY, SimpleNamespace(key="schedule_count")
    )
    assert not entity.available
    assert entity.native_value is None


def test_late_discovery_creates_two_entities_once_and_bridge_creates_none():
    async def exercise():
        module = _module()
        coordinator = _Coordinator()
        coordinator.data = {}
        entry = SimpleNamespace(
            runtime_data=SimpleNamespace(ev_schedule_coordinator=coordinator),
            async_on_unload=Mock(),
        )
        entities = []
        await module["async_setup_ev_schedule_sensors"](None, entry, entities.extend)
        coordinator.data = {KEY: _parse(_device())}
        for listener in coordinator.listeners:
            listener()
            listener()
        assert len(entities) == 2
        assert {entity.native_value for entity in entities} == {
            1,
            coordinator.data[KEY].next_start,
        }
        entry.runtime_data.ev_schedule_coordinator = None
        bridge = []
        await module["async_setup_ev_schedule_sensors"](None, entry, bridge.extend)
        assert bridge == []

    asyncio.run(exercise())
