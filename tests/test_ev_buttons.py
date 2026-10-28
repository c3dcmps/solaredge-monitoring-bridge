"""HA EV buttons and serialized commands, with framework/network boundaries doubled."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_ev_entities import (
    EVChargerSnapshot,
    _Coordinator,
    _coordinator_class,
    _CoordinatorEntity,
    _device_info,
    _HomeAssistantError,
    _load_boundary,
)
from test_portal import SolarEdgePortalError, portal_module

KEY = "a" * 64


def test_safe_control_error_reports_actionable_reason() -> None:
    async def exercise():
        client = SimpleNamespace(
            async_set_ev_charging=AsyncMock(
                side_effect=portal_module.SolarEdgeEVCommandError("missing_csrf")
            ),
            async_get_ev_chargers=AsyncMock(
                return_value={
                    KEY: EVChargerSnapshot(KEY, "PLUGGED_IN", "AUTO", 0),
                }
            ),
        )
        coordinator = _coordinator(client)
        with pytest.raises(_HomeAssistantError, match="CSRF"):
            await coordinator.async_set_charging(KEY, charging=False)

    asyncio.run(exercise())


def _coordinator(client):
    coordinator = _coordinator_class()(
        None, SimpleNamespace(), client, update_interval=timedelta(seconds=60)
    )
    coordinator.data = {KEY: EVChargerSnapshot(KEY, "PLUGGED_IN", "AUTO", 0)}
    return coordinator


def test_start_and_stop_are_serialized_and_observed_values_are_refreshed() -> None:
    async def exercise():
        started = asyncio.Event()
        release = asyncio.Event()
        calls = []
        mode = "AUTO"

        async def request(key, *, charging):
            nonlocal mode
            calls.append(charging)
            if charging:
                started.set()
                await release.wait()
            mode = "MANUAL" if charging else "AUTO"

        async def read():
            return {KEY: EVChargerSnapshot(KEY, "PLUGGED_IN", mode, 0)}

        client = SimpleNamespace(
            async_set_ev_charging=request, async_get_ev_chargers=read
        )
        coordinator = _coordinator(client)
        start = asyncio.create_task(coordinator.async_set_charging(KEY, charging=True))
        await started.wait()
        stop = asyncio.create_task(coordinator.async_set_charging(KEY, charging=False))
        await asyncio.sleep(0)
        assert calls == [True]
        assert coordinator.data[KEY].activation_mode == "AUTO"
        release.set()
        await asyncio.gather(start, stop)
        assert calls == [True, False]
        assert coordinator.data[KEY].activation_mode == "AUTO"

    asyncio.run(exercise())


@pytest.mark.parametrize("unavailable", ["unknown", "failed"])
def test_unavailable_device_does_not_receive_a_command(unavailable) -> None:
    async def exercise():
        request = AsyncMock()
        coordinator = _coordinator(SimpleNamespace(async_set_ev_charging=request))
        if unavailable == "unknown":
            coordinator.data = {}
        else:
            coordinator.last_update_success = False
        with pytest.raises(_HomeAssistantError, match="unavailable"):
            await coordinator.async_set_charging(KEY, charging=True)
        request.assert_not_awaited()

    asyncio.run(exercise())


def test_failed_command_refreshes_state_without_claiming_success_or_retrying() -> None:
    async def exercise():
        request = AsyncMock(side_effect=SolarEdgePortalError("private-detail"))
        client = SimpleNamespace(
            async_set_ev_charging=request,
            async_get_ev_chargers=AsyncMock(
                return_value={
                    KEY: EVChargerSnapshot(KEY, "CHARGING", "MANUAL", None),
                }
            ),
        )
        coordinator = _coordinator(client)
        with pytest.raises(_HomeAssistantError, match="could not confirm") as captured:
            await coordinator.async_set_charging(KEY, charging=True)
        assert "private-detail" not in str(captured.value)
        assert request.await_count == 1
        assert coordinator.data[KEY].activation_mode == "MANUAL"

    asyncio.run(exercise())


def _button_module():
    return _load_boundary(
        "button.py",
        CoordinatorEntity=_CoordinatorEntity,
        ButtonEntity=type("Button", (), {}),
        ButtonEntityDescription=type("Description", (), {}),
        dataclass=lambda **_: lambda cls: cls,
        DeviceInfo=_device_info,
        DOMAIN="solaredge_one_bridge",
        callback=lambda function: function,
        SolarEdgeEVCoordinator=_coordinator_class(),
        BUTTON_DESCRIPTIONS=[
            SimpleNamespace(key="charge_now", charging=True),
            SimpleNamespace(key="stop_charging", charging=False),
        ],
    )


def test_buttons_target_same_device_and_send_the_selected_action() -> None:
    async def exercise():
        module = _button_module()
        coordinator = _Coordinator()
        coordinator.data = {KEY: EVChargerSnapshot(KEY, "PLUGGED_IN", "AUTO", 0)}
        coordinator.async_set_charging = AsyncMock()
        buttons = [
            module["SolarEdgeEVButton"](coordinator, KEY, description)
            for description in module["BUTTON_DESCRIPTIONS"]
        ]
        assert all(button.available for button in buttons)
        assert buttons[0]._attr_device_info["identifiers"] == {
            ("solaredge_one_bridge", "ev_charger_" + KEY)
        }
        assert len({button._attr_unique_id for button in buttons}) == 2
        await buttons[0].async_press()
        await buttons[1].async_press()
        assert [
            call.kwargs["charging"]
            for call in coordinator.async_set_charging.await_args_list
        ] == [True, False]
        coordinator.last_update_success = False
        assert not any(button.available for button in buttons)
        coordinator.last_update_success = True
        coordinator.data = {}
        assert not any(button.available for button in buttons)

    asyncio.run(exercise())


def test_button_discovery_handles_late_devices_and_cleans_up_listener() -> None:
    async def exercise():
        module = _button_module()
        coordinator = _Coordinator()
        callbacks = []
        entry = SimpleNamespace(
            runtime_data=SimpleNamespace(ev_coordinator=coordinator),
            async_on_unload=callbacks.append,
        )
        added = []
        await module["async_setup_entry"](None, entry, added.extend)
        assert added == []
        coordinator.data = {KEY: EVChargerSnapshot(KEY, "PLUGGED_IN", "AUTO", 0)}
        for listener in coordinator.listeners:
            listener()
            listener()
        assert len(added) == 2
        for remove in callbacks:
            remove()
        assert coordinator.listeners == []

    asyncio.run(exercise())


def test_local_snapshot_mode_does_not_create_buttons() -> None:
    async def exercise():
        module = _button_module()
        entry = SimpleNamespace(runtime_data=SimpleNamespace(ev_coordinator=None))
        await module["async_setup_entry"](
            None, entry, lambda _: pytest.fail("button created")
        )

    asyncio.run(exercise())
