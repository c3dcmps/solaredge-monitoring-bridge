"""Read-only EV discovery, identity and privacy behavior."""

from __future__ import annotations

import asyncio
import importlib
import math
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from test_portal import (
    SolarEdgePortalClient,
    SolarEdgePortalConfig,
    SolarEdgePortalError,
    _Response,
    _Session,
)
from test_portal_client import adapter


def _parse(payload, **kwargs):
    module = importlib.import_module(
        "custom_components.solaredge_one_bridge.ev_charger"
    )
    return module.parse_ev_chargers(payload, site_id="1234567", **kwargs)


def _devices(*devices):
    return {"devicesByType": {"EV_CHARGER": list(devices)}}


def test_discovery_retains_only_safe_values_and_an_opaque_identity() -> None:
    payload = _devices(
        {
            "reporterId": 12345,
            "chargerStatus": "PLUGGED_IN",
            "activationMode": "AUTO",
            "serialNumber": "private-serial",
            "name": "private-name",
            "password": "private-password",
            "actionOperationDetails": [{"actionOp": "ON"}],
        }
    )
    result = _parse(payload, power_w=11000)
    assert len(result) == 1
    charger = next(iter(result.values()))
    assert charger.status == "PLUGGED_IN"
    assert charger.activation_mode == "AUTO"
    assert charger.power_w == 11000
    assert len(charger.key) == 64
    assert "12345" not in charger.key
    assert set(asdict(charger)) == {"key", "status", "activation_mode", "power_w"}
    assert "private" not in repr(result)
    assert next(iter(_parse(payload))) == charger.key
    module = importlib.import_module(
        "custom_components.solaredge_one_bridge.ev_charger"
    )
    assert (
        next(iter(module.parse_ev_chargers(payload, site_id="7654321"))) != charger.key
    )


@pytest.mark.parametrize("power", [True, -1, math.nan, math.inf, "11000", None])
def test_invalid_power_is_unavailable(power) -> None:
    charger = next(
        iter(_parse(_devices({"reporterId": "12345"}), power_w=power).values())
    )
    assert charger.power_w is None
    assert charger.status is None
    assert charger.activation_mode is None


def test_zero_power_is_a_real_measurement() -> None:
    charger = next(iter(_parse(_devices({"reporterId": 1}), power_w=0).values()))
    assert charger.power_w == 0


@pytest.mark.parametrize("value", [True, -1, 0, 1.5, "../1", "", None, "1" * 33])
def test_invalid_identity_cannot_create_a_device(value) -> None:
    assert _parse(_devices({"reporterId": value})) == {}


def test_multiple_or_duplicate_devices_cannot_receive_aggregate_power() -> None:
    result = _parse(_devices({"reporterId": 1}, {"reporterId": 2}), power_w=11000)
    assert len(result) == 2
    assert all(charger.power_w is None for charger in result.values())
    assert _parse(_devices({"reporterId": 1}, {"reporterId": "1"})) == {}
    mixed = _parse(_devices({"reporterId": 1}, {"reporterId": None}), power_w=11000)
    assert len(mixed) == 1
    assert next(iter(mixed.values())).power_w is None


def test_unrecognized_status_is_kept_but_arbitrary_text_is_not() -> None:
    result = _parse(_devices({"reporterId": 1, "chargerStatus": "NEW_STATUS"}))
    assert next(iter(result.values())).status == "NEW_STATUS"
    for value in ["private@example.test", "A" * 65, 123, {}, ""]:
        result = _parse(_devices({"reporterId": 1, "chargerStatus": value}))
        assert next(iter(result.values())).status is None


@pytest.mark.parametrize(
    "payload",
    [None, [], {}, {"devicesByType": []}, {"devicesByType": {"EV_CHARGER": {}}}],
)
def test_malformed_envelope_is_an_error_not_an_empty_discovery(payload) -> None:
    with pytest.raises(ValueError, match="EV charger"):
        _parse(payload)


def test_successful_empty_list_means_no_ev_charger() -> None:
    assert _parse(_devices()) == {}
    assert _parse({"devicesByType": {"OTHER_DEVICE": []}}) == {}


def test_ev_discovery_uses_only_the_fixed_https_get_endpoint() -> None:
    async def exercise():
        session = _Session([_Response(200, _devices({"reporterId": 1}))])
        config = SolarEdgePortalConfig(
            "synthetic@example.test", "synthetic", "1234567", UTC
        )
        portal = SolarEdgePortalClient(config, session=session)
        portal._authorization_header = "Bearer synthetic"
        portal._ensure_login = AsyncMock()
        assert await portal.ev_chargers() == _devices({"reporterId": 1})
        method, url, options = session.calls[0]
        assert method == "GET"
        assert url == (
            "https://monitoring.solaredge.com/services/api/homeautomation/v1.0/"
            "sites/1234567/devices"
        )
        assert options["allow_redirects"] is False
        assert options["data"] is None and options["json"] is None

    asyncio.run(exercise())


@pytest.mark.parametrize("status", [302, 401, 403, 500])
def test_ev_endpoint_rejects_http_failures_and_redirects(status) -> None:
    async def exercise():
        session = _Session([_Response(status), _Response(status)])
        config = SolarEdgePortalConfig(
            "synthetic@example.test", "synthetic", "1234567", UTC
        )
        portal = SolarEdgePortalClient(config, session=session)
        portal._authorization_header = "Bearer synthetic"
        portal._ensure_login = AsyncMock()
        with pytest.raises(SolarEdgePortalError):
            await portal.ev_chargers()
        assert all(method == "GET" for method, _, _ in session.calls)

    asyncio.run(exercise())


def _client():
    return adapter.SolarEdgePortalSnapshotClient(
        _Session([]),
        username="synthetic@example.test",
        password="synthetic",
        site_id="1234567",
        time_zone=UTC,
        timeout_seconds=10,
        max_snapshot_age_seconds=900,
    )


def test_adapter_exposes_normalized_power_and_rejects_stale_flow() -> None:
    async def exercise():
        client = _client()
        client._portal.ev_chargers = AsyncMock(return_value=_devices({"reporterId": 1}))
        client._portal.live_power_flow = AsyncMock(
            return_value={
                "components": {"ev_charger": {"current_power_w": 11000}},
                "last_update_time": datetime.now(UTC).isoformat(),
            }
        )
        result = await client.async_get_ev_chargers()
        assert next(iter(result.values())).power_w == 11000
        client._portal.live_power_flow.return_value["last_update_time"] = (
            datetime.now(UTC) - timedelta(minutes=20)
        ).isoformat()
        result = await client.async_get_ev_chargers()
        assert next(iter(result.values())).power_w is None
        assert len(result) == 1

    asyncio.run(exercise())


def test_power_flow_failure_preserves_discovered_status_and_mode(caplog) -> None:
    async def exercise():
        client = _client()
        client._portal.ev_chargers = AsyncMock(
            return_value=_devices(
                {
                    "reporterId": 1,
                    "chargerStatus": "PLUGGED_IN",
                    "activationMode": "AUTO",
                }
            )
        )
        client._portal.live_power_flow = AsyncMock(
            side_effect=SolarEdgePortalError("private detail")
        )
        with caplog.at_level("DEBUG"):
            result = await client.async_get_ev_chargers()
        charger = next(iter(result.values()))
        assert charger.status == "PLUGGED_IN"
        assert charger.activation_mode == "AUTO"
        assert charger.power_w is None
        assert "power flow is unavailable" in caplog.text
        assert "private detail" not in caplog.text

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "timestamp",
    [
        None,
        "",
        "not-a-date",
        1234,
        (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
    ],
)
def test_ev_power_without_a_valid_recent_timestamp_is_unavailable(timestamp) -> None:
    async def exercise():
        client = _client()
        client._portal.ev_chargers = AsyncMock(
            return_value=_devices(
                {
                    "reporterId": 1,
                    "chargerStatus": "PLUGGED_IN",
                }
            )
        )
        client._portal.live_power_flow = AsyncMock(
            return_value={
                "components": {"ev_charger": {"current_power_w": 11000}},
                "last_update_time": timestamp,
            }
        )
        result = await client.async_get_ev_chargers()
        charger = next(iter(result.values()))
        assert charger.power_w is None
        assert charger.status == "PLUGGED_IN"

    asyncio.run(exercise())
