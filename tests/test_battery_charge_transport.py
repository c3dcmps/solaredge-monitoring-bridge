"""Fixed-origin battery writes must not redirect, replay or accept failed envelopes."""

import asyncio

import pytest
from aiohttp import ClientPayloadError
from test_battery_charge_schedule import plan
from test_portal import (
    SolarEdgePortalClient,
    SolarEdgePortalConfig,
    _Response,
    _Session,
)
from test_portal_client import _load


@pytest.mark.parametrize(
    "status,body,success",
    [
        (200, {"status": "PASSED", "httpStatus": 200, "errorMessages": []}, True),
        (200, {"status": "FAILED", "httpStatus": 500, "errorMessages": []}, False),
        (200, {"status": "PASSED", "httpStatus": 200}, False),
        (302, {}, False),
        (401, {}, False),
        (403, {}, False),
        (500, {}, False),
    ],
)
def test_write_uses_current_bearer_once_and_checks_provider_envelope(
    status, body, success
):
    async def exercise():
        from datetime import UTC

        session = _Session([_Response(status, body)])
        client = SolarEdgePortalClient(
            SolarEdgePortalConfig("u", "p", "123", UTC), session=session
        )

        async def login(*, force=False):
            client._authorization_header = "Bearer private-token"

        client._ensure_login = login
        _, expected = plan()
        if success:
            await client.set_battery_configuration(expected)
        else:
            with pytest.raises(_load("portal").SolarEdgePortalError):
                await client.set_battery_configuration(expected)
        assert len(session.calls) == 1
        verb, url, options = session.calls[0]
        assert verb == "PUT"
        assert (
            url
            == "https://monitoring.solaredge.com/services/api/homeautomation/v1.0/storage/123/batteryMode"
        )
        assert options["headers"] == {"Authorization": "Bearer private-token"}
        assert options["json"] == expected and options["allow_redirects"] is False

    asyncio.run(exercise())


def test_adapter_exposes_raw_read_and_write_without_reusing_sanitized_sensors():
    async def exercise():
        from datetime import UTC
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        adapter = _load("portal_client").SolarEdgePortalSnapshotClient(
            None,
            username="u",
            password="p",
            site_id="123",
            time_zone=UTC,
            timeout_seconds=10,
            max_snapshot_age_seconds=900,
        )
        before, expected = plan()
        adapter._portal = SimpleNamespace(
            battery_configuration=AsyncMock(return_value=before),
            set_battery_configuration=AsyncMock(),
        )
        assert await adapter.async_read_battery_program() == before
        await adapter.async_write_battery_program(expected)
        adapter._portal.set_battery_configuration.assert_awaited_once_with(expected)

    asyncio.run(exercise())


@pytest.mark.parametrize("error", [TimeoutError, ClientPayloadError])
def test_interrupted_write_response_is_never_retried(error):
    async def exercise():
        from datetime import UTC

        class BrokenContent:
            async def iter_chunked(self, size):
                yield b"{"
                raise error("private-token")

        response = _Response(200, {})
        response.content = BrokenContent()
        session = _Session([response])
        client = SolarEdgePortalClient(
            SolarEdgePortalConfig("u", "p", "123", UTC), session=session
        )

        async def login(*, force=False):
            client._authorization_header = "Bearer private-token"

        client._ensure_login = login
        with pytest.raises(_load("portal").SolarEdgePortalError) as captured:
            await client.set_battery_configuration(plan()[1])
        assert "private-token" not in str(captured.value)
        assert len(session.calls) == 1

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "field",
    [
        "batteryMode",
        "manualTouConfiguration",
        "touConfiguration",
        "smartPredictionEnabled",
    ],
)
def test_transport_refuses_missing_writable_fields_without_network(field):
    async def exercise():
        from datetime import UTC

        session = _Session([])
        client = SolarEdgePortalClient(
            SolarEdgePortalConfig("u", "p", "123", UTC), session=session
        )
        _, expected = plan()
        expected.pop(field)
        with pytest.raises(ValueError):
            await client.set_battery_configuration(expected)
        assert session.calls == []

    asyncio.run(exercise())
