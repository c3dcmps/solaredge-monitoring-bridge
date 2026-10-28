"""Exercise EV entity and coordinator behavior at the Home Assistant boundary."""

from __future__ import annotations

import ast
import asyncio
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_portal import SolarEdgePortalError
from test_portal_client import _load

EVChargerSnapshot = _load("ev_charger").EVChargerSnapshot

COMPONENT = Path(__file__).parents[1] / "custom_components" / "solaredge_one_bridge"


class _UpdateFailed(Exception):
    pass


class _Coordinator:
    """Minimal external HA boundary; execute production update/discovery logic."""

    def __class_getitem__(cls, _):
        return cls

    def __init__(self, hass=None, logger=None, **kwargs):
        self.data = {}
        self.last_update_success = True
        self.update_interval = kwargs.get("update_interval")
        self.listeners = []

    async def async_refresh(self):
        try:
            self.data = await self._async_update_data()
            self.last_update_success = True
        except _UpdateFailed:
            self.last_update_success = False
        for listener in list(self.listeners):
            listener()

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)


class _CoordinatorEntity:
    def __class_getitem__(cls, _):
        return cls

    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def available(self):
        return self.coordinator.last_update_success


def _load_boundary(filename, **namespace):
    """Load real entry points with only framework objects supplied externally."""
    path = COMPONENT / filename
    tree = ast.parse(path.read_text())
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0
    )
    module = ast.fix_missing_locations(
        ast.Module(body=[future, *nodes], type_ignores=[])
    )
    namespace = {"__name__": __name__, **namespace}
    exec(compile(module, str(path), "exec"), namespace)
    return namespace


def _coordinator_class():
    return _load_boundary(
        "ev_coordinator.py",
        DataUpdateCoordinator=_Coordinator,
        UpdateFailed=_UpdateFailed,
        SolarEdgePortalError=SolarEdgePortalError,
        EVChargerSnapshot=EVChargerSnapshot,
        DOMAIN="solaredge_one_bridge",
        _LOGGER=None,
    )["SolarEdgeEVCoordinator"]


def _entity_module():
    return _load_boundary(
        "ev_sensor.py",
        CoordinatorEntity=_CoordinatorEntity,
        SensorEntity=type("Sensor", (), {}),
        DeviceInfo=dict,
        DOMAIN="solaredge_one_bridge",
        callback=lambda function: function,
        SolarEdgeEVCoordinator=_coordinator_class(),
        SENSOR_DESCRIPTIONS=[
            SimpleNamespace(key=key) for key in ("status", "activation_mode", "power_w")
        ],
    )


def test_ev_coordinator_failure_is_independent_and_recovers() -> None:
    async def exercise():
        charger = EVChargerSnapshot("a" * 64, "PLUGGED_IN", "AUTO", 0)
        client = SimpleNamespace(
            async_get_ev_chargers=AsyncMock(
                side_effect=[
                    SolarEdgePortalError("synthetic failure"),
                    {charger.key: charger},
                ]
            )
        )
        coordinator = _coordinator_class()(
            None, SimpleNamespace(), client, update_interval=timedelta(seconds=60)
        )
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.data[charger.key].status == "PLUGGED_IN"
        assert coordinator.update_interval == timedelta(seconds=60)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "error", [ValueError("malformed"), SolarEdgePortalError("rejected")]
)
def test_ev_coordinator_reports_sanitized_failures(error) -> None:
    async def exercise():
        client = SimpleNamespace(async_get_ev_chargers=AsyncMock(side_effect=error))
        coordinator = _coordinator_class()(
            None, SimpleNamespace(), client, update_interval=timedelta(seconds=60)
        )
        with pytest.raises(_UpdateFailed, match="EV charger data is unavailable"):
            await coordinator._async_update_data()

    asyncio.run(exercise())


def test_entities_follow_live_values_missing_devices_and_network_failures() -> None:
    module = _entity_module()
    coordinator = _Coordinator()
    charger = EVChargerSnapshot("a" * 64, "PLUGGED_IN", "AUTO", 0)
    coordinator.data = {charger.key: charger}
    entities = [
        module["SolarEdgeEVSensor"](coordinator, charger.key, SimpleNamespace(key=key))
        for key in ("status", "activation_mode", "power_w")
    ]
    assert [entity.native_value for entity in entities] == ["PLUGGED_IN", "AUTO", 0]
    assert all(entity.available for entity in entities)
    assert len({entity._attr_unique_id for entity in entities}) == 3
    assert entities[0]._attr_device_info["identifiers"] == {
        ("solaredge_one_bridge", "ev_charger_" + charger.key)
    }
    coordinator.data[charger.key] = EVChargerSnapshot(
        charger.key, "CHARGING", None, None
    )
    assert entities[0].native_value == "CHARGING"
    assert entities[0].available
    assert not entities[1].available and not entities[2].available
    coordinator.last_update_success = False
    assert not entities[0].available
    coordinator.last_update_success = True
    coordinator.data = {}
    assert not any(entity.available for entity in entities)
    assert all(entity.native_value is None for entity in entities)


def test_setup_discovers_later_arrivals_once_and_unregisters_listener() -> None:
    async def exercise():
        module = _entity_module()
        charger = EVChargerSnapshot("a" * 64, "PLUGGED_IN", "AUTO", 0)
        client = SimpleNamespace(
            async_get_ev_chargers=AsyncMock(
                side_effect=[
                    SolarEdgePortalError("offline"),
                    {charger.key: charger},
                    {charger.key: charger},
                    {},
                ]
            )
        )
        callbacks = []
        site = SimpleNamespace(
            portal_client=client, update_interval=timedelta(seconds=60)
        )
        entry = SimpleNamespace(runtime_data=site, async_on_unload=callbacks.append)
        added = []
        await module["async_setup_ev_sensors"](None, entry, added.extend)
        assert added == []
        ev = site.ev_coordinator
        await ev.async_refresh()
        assert len(added) == 3
        await ev.async_refresh()
        assert len(added) == 3
        await ev.async_refresh()
        assert not any(entity.available for entity in added)
        for remove in callbacks:
            remove()
        assert not ev.listeners

    asyncio.run(exercise())


def test_local_bridge_does_not_start_ev_polling() -> None:
    async def exercise():
        module = _entity_module()
        site = SimpleNamespace(portal_client=None)
        entry = SimpleNamespace(runtime_data=site)
        await module["async_setup_ev_sensors"](
            None, entry, lambda _: pytest.fail("EV created")
        )
        assert not hasattr(site, "ev_coordinator")

    asyncio.run(exercise())
