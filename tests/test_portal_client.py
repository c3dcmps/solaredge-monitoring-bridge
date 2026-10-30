"""Tests for the aggregate-only portal snapshot adapter."""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import sys
import types
from datetime import UTC, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock, sentinel
from zoneinfo import ZoneInfo

import pytest
from aiohttp import ClientSession, TCPConnector
from yarl import URL

COMPONENT = Path(__file__).parents[1] / "custom_components" / "solaredge_one_bridge"
PACKAGE = "custom_components.solaredge_one_bridge"
if PACKAGE not in sys.modules:
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(COMPONENT)]
    sys.modules[PACKAGE] = package


def _load(name: str):
    qualified = f"{PACKAGE}.{name}"
    if qualified in sys.modules:
        return sys.modules[qualified]
    spec = importlib.util.spec_from_file_location(qualified, COMPONENT / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module


_load("const")
_load("model")
_load("portal")
adapter = _load("portal_client")


def test_portal_mapping_exposes_only_aggregate_snapshot_contract() -> None:
    flow = {
        "components": {
            "solar_production": {"current_power_w": 5100},
            "consumption": {"current_power_w": 3300},
            "grid": {"current_power_w": 900, "status": "Export"},
            "dc_storage": {
                "current_power_w": 900,
                "status": "Charging",
                "chargeLevel": 74.5,
                "serialNumber": "must-not-propagate",
            },
        },
        "last_update_time": "2026-08-31T12:30:00-07:00",
        "storage_operating_plan": {
            "plan": "Time of Use",
            "is_active": True,
            "block_count": 3,
            "raw_policy": {"must": "not propagate"},
        },
        "site_id": "must-not-propagate",
    }
    lifetime = {
        "totals_kwh": {
            "production": 12345.6,
            "consumption": 9876.5,
            "import": 2222.2,
            "export": 4444.4,
        },
        "production_distribution": {"production_to_battery_kwh": 850.1},
        "consumption_distribution": {"consumption_from_battery_kwh": 771.2},
        "account": {"email": "must-not-propagate"},
    }

    payload = adapter._build_snapshot_payload(
        flow, lifetime, ZoneInfo("America/Los_Angeles")
    )

    assert payload["provider"] == "solaredge_monitoring_portal"
    assert payload["observed_at"] == "2026-08-31T19:30:00+00:00"
    assert payload["site"] == {
        "production_power_w": 5100.0,
        "consumption_power_w": 3300.0,
        "grid_import_power_w": 0.0,
        "grid_export_power_w": 900.0,
        "battery_charge_power_w": 900.0,
        "battery_discharge_power_w": 0.0,
        "battery_state_of_energy_pct": 74.5,
        "production_energy_kwh": 12345.6,
        "consumption_energy_kwh": 9876.5,
        "grid_import_energy_kwh": 2222.2,
        "grid_export_energy_kwh": 4444.4,
        "battery_charge_energy_kwh": 850.1,
        "battery_discharge_energy_kwh": 771.2,
        "storage_operating_plan": "Time of Use",
        "storage_operating_plan_active": True,
        "storage_operating_plan_block_count": 3,
    }
    rendered = repr(payload)
    assert "serialNumber" not in rendered
    assert "site_id" not in rendered
    assert "email" not in rendered
    assert "raw_policy" not in rendered


def test_direction_split_never_double_counts_power() -> None:
    base = {
        "components": {
            "grid": {"current_power_w": 1200, "direction": "from_grid"},
            "ac_storage": {"current_power_w": 700, "direction": "discharging"},
        },
        "storage_operating_plan": {},
    }
    payload = adapter._build_snapshot_payload(base, {}, UTC)
    assert payload["site"]["grid_import_power_w"] == 1200
    assert payload["site"]["grid_export_power_w"] == 0
    assert payload["site"]["battery_charge_power_w"] == 0
    assert payload["site"]["battery_discharge_power_w"] == 700


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        ("2026-03-08T01:30:00", "2026-03-08T09:30:00+00:00"),
        ("2026-03-08T03:30:00", "2026-03-08T10:30:00+00:00"),
        ("2026-11-01T01:30:00", "2026-11-01T08:30:00+00:00"),
        ("2026-11-01T02:30:00", "2026-11-01T10:30:00+00:00"),
        ("2026-11-01T01:30:00-08:00", "2026-11-01T09:30:00+00:00"),
    ],
)
def test_snapshot_timestamps_reuse_resolved_zone(timestamp, expected) -> None:
    payload = adapter._build_snapshot_payload(
        {"last_update_time": timestamp}, {}, ZoneInfo("America/Los_Angeles")
    )
    assert payload["observed_at"] == expected


def _async_entrypoint(filename: str, name: str, **dependencies):
    """Exercise actual async entry points without importing the HA runtime."""
    path = COMPONENT / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if getattr(node, "name", None) == name)
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0
    )
    module = ast.fix_missing_locations(
        ast.Module(body=[future, function], type_ignores=[])
    )
    namespace = {**vars(_load("const")), **dependencies}
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[name]


@pytest.mark.parametrize("valid_zone", [True, False])
def test_config_flow_resolves_zone_before_portal_access(valid_zone) -> None:
    time_zone = ZoneInfo("America/Los_Angeles") if valid_zone else None
    resolver = AsyncMock(return_value=time_zone)
    client = types.SimpleNamespace(live_power_flow=AsyncMock())
    factory = Mock(return_value=client)
    session = Mock()
    create_session = Mock(return_value=session)
    validate = _async_entrypoint(
        "config_flow.py",
        "_async_validate_portal",
        dt_util=types.SimpleNamespace(async_get_time_zone=resolver),
        SolarEdgePortalConfig=adapter.SolarEdgePortalConfig,
        SolarEdgePortalClient=factory,
        async_create_clientsession=create_session,
    )
    operation = validate(
        sentinel.hass,
        username="user@example.test",
        password="synthetic-password",
        site_id="1234567",
        timezone_name="America/Los_Angeles" if valid_zone else "Not/A_Zone",
    )
    if valid_zone:
        asyncio.run(operation)
        config = factory.call_args.args[0]
        assert config.time_zone is time_zone
        assert factory.call_args.kwargs["session"] is session
        create_session.assert_called_once_with(sentinel.hass, auto_cleanup=False)
        client.live_power_flow.assert_awaited_once()
        session.detach.assert_called_once_with()
    else:
        with pytest.raises(ValueError, match="timezone is invalid"):
            asyncio.run(operation)
        factory.assert_not_called()
        create_session.assert_not_called()
    resolver.assert_awaited_once_with(
        "America/Los_Angeles" if valid_zone else "Not/A_Zone"
    )


@pytest.mark.parametrize("valid_zone", [True, False])
def test_setup_resolves_zone_before_first_coordinator_refresh(valid_zone) -> None:
    time_zone = ZoneInfo("America/Los_Angeles") if valid_zone else None
    resolver = AsyncMock(return_value=time_zone)
    factory = Mock()
    create_session = Mock(return_value=sentinel.portal_session)
    shared_session = Mock()
    coordinator = types.SimpleNamespace(
        async_config_entry_first_refresh=AsyncMock(),
        update_interval=timedelta(seconds=300),
    )
    coordinator_factory = Mock(return_value=coordinator)
    ev_coordinator = types.SimpleNamespace(async_refresh=AsyncMock())
    ev_factory = Mock(return_value=ev_coordinator)
    schedule_coordinator = types.SimpleNamespace(async_refresh=AsyncMock())
    schedule_factory = Mock(return_value=schedule_coordinator)
    battery_coordinator = types.SimpleNamespace(async_refresh=AsyncMock())
    battery_factory = Mock(return_value=battery_coordinator)
    hass = types.SimpleNamespace(
        config_entries=types.SimpleNamespace(async_forward_entry_setups=AsyncMock())
    )
    entry = types.SimpleNamespace(
        data={
            "mode": "portal",
            "username": "user@example.test",
            "password": "synthetic-password",
            "site_id": "1234567",
            "time_zone": "America/Los_Angeles" if valid_zone else "Not/A_Zone",
        },
        options={},
        async_on_unload=Mock(),
        add_update_listener=Mock(),
    )

    class ConfigEntryNotReady(Exception):
        pass

    setup = _async_entrypoint(
        "__init__.py",
        "async_setup_entry",
        dt_util=types.SimpleNamespace(async_get_time_zone=resolver),
        SolarEdgePortalSnapshotClient=factory,
        SolarEdgeBridgeCoordinator=coordinator_factory,
        SolarEdgeEVCoordinator=ev_factory,
        SolarEdgeEVScheduleCoordinator=schedule_factory,
        SolarEdgeBatteryConfigurationCoordinator=battery_factory,
        async_create_clientsession=create_session,
        async_get_clientsession=shared_session,
        ConfigEntryNotReady=ConfigEntryNotReady,
        timedelta=timedelta,
        _async_update_listener=sentinel.listener,
        PLATFORMS=[sentinel.sensor],
    )
    if valid_zone:
        assert asyncio.run(setup(hass, entry)) is True
        assert factory.call_args.kwargs["time_zone"] is time_zone
        assert factory.call_args.args[0] is sentinel.portal_session
        create_session.assert_called_once_with(hass)
        assert entry.runtime_data is coordinator
        assert coordinator.ev_coordinator is ev_coordinator
        assert coordinator.ev_schedule_coordinator is schedule_coordinator
        assert coordinator.battery_configuration_coordinator is battery_coordinator
        battery_coordinator.async_refresh.assert_awaited_once()
        schedule_coordinator.async_refresh.assert_awaited_once()
        ev_coordinator.async_refresh.assert_awaited_once()
        coordinator.async_config_entry_first_refresh.assert_awaited_once()
        hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
            entry, [sentinel.sensor]
        )
    else:
        with pytest.raises(ConfigEntryNotReady, match="configuration is invalid"):
            asyncio.run(setup(hass, entry))
        factory.assert_not_called()
        coordinator_factory.assert_not_called()
        ev_factory.assert_not_called()
        schedule_factory.assert_not_called()
        create_session.assert_not_called()
    resolver.assert_awaited_once_with(entry.data["time_zone"])
    shared_session.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [None, "authentication", "connection", "cancellation", "construction"],
)
def test_validation_isolates_cookies_and_always_releases_session(failure) -> None:
    async def exercise():
        connector = TCPConnector()
        shared = ClientSession(connector=connector, connector_owner=False)
        private = ClientSession(connector=connector, connector_owner=False)
        create_session = Mock(return_value=private)
        portal = _load("portal")
        error = {
            "authentication": portal.SolarEdgePortalAuthenticationError("rejected"),
            "connection": portal.SolarEdgePortalError("unavailable"),
            "cancellation": asyncio.CancelledError(),
            "construction": ValueError("invalid config"),
        }.get(failure)

        async def live_power_flow():
            private.cookie_jar.update_cookies(
                {"CSRF-TOKEN": "synthetic-cookie"},
                response_url=URL("https://monitoring.solaredge.com"),
            )
            assert len(private.cookie_jar) == 1
            assert not shared.cookie_jar
            if error is not None:
                raise error

        factory = Mock(
            return_value=types.SimpleNamespace(live_power_flow=live_power_flow),
            side_effect=error if failure == "construction" else None,
        )
        validate = _async_entrypoint(
            "config_flow.py",
            "_async_validate_portal",
            dt_util=types.SimpleNamespace(
                async_get_time_zone=AsyncMock(return_value=UTC)
            ),
            SolarEdgePortalConfig=adapter.SolarEdgePortalConfig,
            SolarEdgePortalClient=factory,
            async_create_clientsession=create_session,
        )
        try:
            operation = validate(
                sentinel.hass,
                username="user@example.test",
                password="synthetic-password",
                site_id="1234567",
                timezone_name="UTC",
            )
            if error is None:
                await operation
            else:
                with pytest.raises(type(error)):
                    await operation
            create_session.assert_called_once_with(sentinel.hass, auto_cleanup=False)
            assert factory.call_args.kwargs["session"] is private
            assert private.closed
            assert not connector.closed
            assert not shared.closed
            assert not shared.cookie_jar
        finally:
            await private.close()
            await shared.close()
            await connector.close()

    asyncio.run(exercise())
