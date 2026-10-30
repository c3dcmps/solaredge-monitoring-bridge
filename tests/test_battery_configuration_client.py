"""Read-only battery transport and independent endpoint failures."""

import asyncio
from datetime import UTC
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_battery_configuration import _mode, _reserve
from test_portal import (
    SolarEdgePortalClient,
    SolarEdgePortalConfig,
    _Response,
    _Session,
)
from test_portal_client import _load


@pytest.mark.parametrize(
    "method,suffix,params",
    [
        ("battery_configuration", "batteryMode", None),
        ("battery_reserve", "getBatteries", [("triggerHF", "false")]),
    ],
)
def test_battery_reads_use_existing_fixed_origin_get_and_current_bearer(
    method, suffix, params
):
    async def exercise():
        session = _Session([_Response(200, _mode())])
        client = SolarEdgePortalClient(
            SolarEdgePortalConfig("u", "p", "123", UTC), session=session
        )

        async def login(*, force=False):
            client._authorization_header = "Bearer private-token"

        client._ensure_login = login
        assert await getattr(client, method)() == _mode()
        assert len(session.calls) == 1
        verb, url, options = session.calls[0]
        assert verb == "GET"
        assert url == (
            "https://monitoring.solaredge.com/services/api/homeautomation/v1.0/"
            f"storage/123/{suffix}"
        )
        assert options["params"] == params
        assert options["headers"] == {"Authorization": "Bearer private-token"}
        assert options["allow_redirects"] is False
        assert options["data"] is options["json"] is None

    asyncio.run(exercise())


@pytest.mark.parametrize("failed", ["mode", "reserve", "both", "neither"])
def test_adapter_preserves_successful_battery_endpoint_when_other_read_fails(failed):
    async def exercise():
        module = _load("portal_client")
        adapter = module.SolarEdgePortalSnapshotClient(
            None,
            username="u",
            password="p",
            site_id="123",
            time_zone=UTC,
            timeout_seconds=10,
            max_snapshot_age_seconds=900,
        )

        def result(kind, payload):
            return (
                AsyncMock(side_effect=module.SolarEdgePortalError("private-secret"))
                if failed in {kind, "both"}
                else AsyncMock(return_value=payload)
            )

        adapter._portal = SimpleNamespace(
            battery_configuration=result("mode", _mode()),
            battery_reserve=result("reserve", _reserve()),
        )
        snapshot = await adapter.async_get_battery_configuration()
        assert snapshot.mode == (None if failed in {"mode", "both"} else "MANUAL_TOU")
        assert snapshot.reserve_pct == (None if failed in {"reserve", "both"} else 15)
        assert "private-secret" not in repr(snapshot)

    asyncio.run(exercise())


def test_bad_nested_manual_action_is_unavailable_instead_of_crashing():
    from test_battery_configuration import _parse, _windows

    payload = _mode()
    _windows(payload)[0]["manualTouData"]["batteryMode"] = []
    assert _parse(payload).manual_count is None
