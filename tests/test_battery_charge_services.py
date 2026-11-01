"""HA action routing and lifecycle with framework boundaries substituted."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from test_battery_charge import Boundary
from test_battery_charge_schedule import END, START, ZONE
from test_ev_entities import _HomeAssistantError, _load_boundary
from test_portal_client import _load


def boundary():
    services = SimpleNamespace(
        async_register=Mock(), has_service=Mock(return_value=False)
    )
    notices = SimpleNamespace(async_create=Mock(), async_dismiss=Mock())
    entry = SimpleNamespace(
        entry_id="entry",
        data={"site_id": "123", "time_zone": str(ZONE)},
        runtime_data=SimpleNamespace(battery_charge=None),
    )
    hass = SimpleNamespace(
        services=services,
        config_entries=SimpleNamespace(async_entries=lambda domain: [entry]),
    )
    entry.state = "loaded"
    entry.async_on_unload = Mock()
    store = SimpleNamespace(
        async_load=AsyncMock(return_value=None), async_save=AsyncMock()
    )
    tracker = Mock(return_value=Mock())
    namespace = _load_boundary(
        "battery_charge_services.py",
        DOMAIN="solaredge_one_bridge",
        TemporaryBatteryCharge=_load("battery_charge").TemporaryBatteryCharge,
        BatteryChargeError=_load("battery_charge").BatteryChargeError,
        HomeAssistantError=_HomeAssistantError,
        ConfigEntryState=SimpleNamespace(LOADED="loaded"),
        Store=lambda *args, **kwargs: store,
        persistent_notification=notices,
        async_track_time_interval=tracker,
        timedelta=timedelta,
        dt_util=SimpleNamespace(parse_datetime=datetime.fromisoformat),
        hashlib=__import__("hashlib"),
        _LOGGER=Mock(),
        ISSUE_MESSAGES={
            "conflict": "Changed app settings",
            "invalid_record": "Invalid stored operation",
            "cleanup_pending": "Cleanup pending",
            "write_uncertain": "Uncertain write",
        },
        CONF_SITE_ID="site_id",
        asyncio=asyncio,
        SCHEDULE_SCHEMA="schedule-schema",
        CANCEL_SCHEMA="cancel-schema",
    )
    return namespace, hass, entry, store, tracker, notices


def test_registration_routes_actions_and_rejects_missing_runtime():
    async def exercise():
        module, hass, entry, _, _, _ = boundary()
        module["async_register_battery_charge_services"](hass)
        calls = hass.services.async_register.call_args_list
        handlers = {call.args[1]: call.args[2] for call in calls}
        assert set(handlers) == {"schedule_battery_charge", "cancel_battery_charge"}
        with pytest.raises(_HomeAssistantError, match="portal"):
            await handlers["cancel_battery_charge"](SimpleNamespace(data={}))
        runtime = SimpleNamespace(schedule=AsyncMock(), cancel=AsyncMock())
        entry.runtime_data.battery_charge = runtime
        data = {"start": START.isoformat(), "end": END.isoformat()}
        await handlers["schedule_battery_charge"](SimpleNamespace(data=data))
        runtime.schedule.assert_awaited_once_with(start=START, end=END)
        await handlers["cancel_battery_charge"](SimpleNamespace(data={}))
        runtime.cancel.assert_awaited_once()
        hass.services.has_service.return_value = True
        module["async_register_battery_charge_services"](hass)
        assert hass.services.async_register.call_count == 2

    asyncio.run(exercise())


def test_runtime_loads_store_registers_timer_and_preserves_pending_on_unload():
    async def exercise():
        module, hass, entry, store, tracker, notices = boundary()
        b = Boundary()
        coordinator = SimpleNamespace(async_request_refresh=AsyncMock())
        runtime = module["BatteryChargeRuntime"](
            hass,
            entry,
            SimpleNamespace(
                async_read_battery_program=b.read, async_write_battery_program=b.write
            ),
            time_zone=ZONE,
            coordinator=coordinator,
        )
        await runtime.async_start()
        assert tracker.call_args.args[2] == timedelta(minutes=1)
        runtime.manager._now = lambda: b.clock

        # Represent durable disk at the HA storage boundary.
        async def save(record):
            await b.save(record["operation"])
            store.async_load.return_value = record

        store.async_save.side_effect = save
        await runtime.schedule(start=START, end=END)
        assert (
            runtime.manager.pending
            and coordinator.async_request_refresh.await_count == 1
        )
        await runtime.async_stop()
        assert runtime.manager.pending
        tracker.return_value.assert_called_once()
        notices.async_create.assert_called()

    asyncio.run(exercise())


def test_storage_from_another_site_blocks_commands_and_warns_without_writes():
    async def exercise():
        module, hass, entry, store, _, notices = boundary()
        store.async_load.return_value = {
            "site_fingerprint": "another-site",
            "operation": {},
        }
        b = Boundary()
        runtime = module["BatteryChargeRuntime"](
            hass,
            entry,
            SimpleNamespace(
                async_read_battery_program=b.read, async_write_battery_program=b.write
            ),
            time_zone=ZONE,
            coordinator=SimpleNamespace(async_request_refresh=AsyncMock()),
        )
        await runtime.async_start()
        assert runtime.manager.issue == "invalid_record"
        with pytest.raises(_HomeAssistantError):
            await runtime.schedule(start=START, end=END)
        assert b.writes == []
        notices.async_create.assert_called()

    asyncio.run(exercise())


def test_silent_storage_write_failure_prevents_remote_command():
    async def exercise():
        module, hass, entry, store, _, _ = boundary()
        store.async_save.side_effect = None
        b = Boundary()
        runtime = module["BatteryChargeRuntime"](
            hass,
            entry,
            SimpleNamespace(
                async_read_battery_program=b.read, async_write_battery_program=b.write
            ),
            time_zone=ZONE,
            coordinator=SimpleNamespace(async_request_refresh=AsyncMock()),
        )
        await runtime.async_start()
        runtime.manager._now = lambda: b.clock
        with pytest.raises(_HomeAssistantError, match="persist"):
            await runtime.schedule(start=START, end=END)
        assert b.writes == []

    asyncio.run(exercise())
