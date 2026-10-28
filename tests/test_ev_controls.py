"""EV command routing, validation, privacy and ambiguous-result behavior."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC
from http.cookies import SimpleCookie
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientPayloadError
from test_portal import (
    SolarEdgePortalClient,
    SolarEdgePortalConfig,
    SolarEdgePortalError,
    _Response,
    _Session,
)
from test_portal_client import _load

model = _load("ev_charger")
portal_module = _load("portal")
KEY = hashlib.sha256(b"1234567:42").hexdigest()
DISCOVERY = {"devicesByType": {"EV_CHARGER": [{"reporterId": 42}]}}
PASSED = {"status": "PASSED", "httpStatus": 200, "errorMessages": []}


def test_control_session_diagnostics_report_flags_without_cookie_values() -> None:
    portal, session = _portal([], csrf=True)
    flags = portal.ev_control_diagnostics
    assert flags == {
        "authorized": True,
        "csrf_cookie_present": True,
        "csrf_header_ready": True,
        "legacy_session_present": False,
    }
    session.cookie_jar[0]["domain"] = "solaredge.com"
    assert portal.ev_control_diagnostics["csrf_cookie_present"] is True
    assert portal.ev_control_diagnostics["csrf_header_ready"] is False
    assert "synthetic" not in repr(portal.ev_control_diagnostics)


def test_command_error_details_allow_only_fixed_reasons_and_numeric_http_status() -> (
    None
):
    error_class = portal_module.SolarEdgeEVCommandError
    assert "rejected" in str(error_class("rejected"))
    assert "403" in str(error_class("http_error", status=403))
    with pytest.raises(ValueError):
        error_class("private-detail")
    with pytest.raises(ValueError):
        error_class("http_error", status="private-detail")


def _portal(responses, *, csrf=True, session_type=_Session):
    session = session_type(responses)
    if csrf:
        cookie = SimpleCookie("CSRF-TOKEN=synthetic-csrf")
        cookie["CSRF-TOKEN"]["domain"] = "monitoring.solaredge.com"
        session.cookie_jar = list(cookie.values())
    config = SolarEdgePortalConfig(
        "synthetic@example.test", "synthetic", "1234567", UTC
    )
    portal = SolarEdgePortalClient(config, session=session)
    portal._authorization_header = "Bearer synthetic"
    portal._ensure_login = AsyncMock()
    return portal, session


def test_reporter_resolution_is_site_scoped_and_keeps_snapshots_opaque() -> None:
    assert model.resolve_ev_reporter_id(DISCOVERY, site_id="1234567", key=KEY) == "42"
    with pytest.raises(ValueError, match="EV charger"):
        model.resolve_ev_reporter_id(DISCOVERY, site_id="7654321", key=KEY)
    assert "reporter" not in repr(model.parse_ev_chargers(DISCOVERY, site_id="1234567"))


@pytest.mark.parametrize(
    "devices",
    [[], [{"reporterId": "../42"}], [{"reporterId": 42}, {"reporterId": "42"}]],
)
def test_removed_invalid_or_duplicate_reporters_cannot_receive_commands(
    devices,
) -> None:
    payload = {"devicesByType": {"EV_CHARGER": devices}}
    with pytest.raises(ValueError, match="EV charger"):
        model.resolve_ev_reporter_id(payload, site_id="1234567", key=KEY)


@pytest.mark.parametrize(("charging", "level"), [(True, 100), (False, 0)])
def test_command_resolves_device_then_uses_only_fixed_put_and_exact_body(
    charging, level
) -> None:
    async def exercise():
        portal, session = _portal([_Response(200, DISCOVERY), _Response(200, PASSED)])
        await portal.set_ev_charging(KEY, charging=charging)
        assert [call[0] for call in session.calls] == ["GET", "PUT"]
        method, url, options = session.calls[1]
        assert url == (
            "https://monitoring.solaredge.com/services/m/api/homeautomation/v1.0/"
            "1234567/devices/42/activationState"
        )
        assert options["json"] == {"mode": "MANUAL", "level": level, "duration": None}
        assert options["allow_redirects"] is False
        assert options["headers"]["X-CSRF-TOKEN"] == "synthetic-csrf"
        assert options["headers"]["Authorization"] == "Bearer synthetic"
        assert options["data"] is None

    asyncio.run(exercise())


@pytest.mark.parametrize(("key", "charging"), [("../42", True), (KEY, 1), (KEY, "ON")])
def test_invalid_command_arguments_fail_before_network_access(key, charging) -> None:
    async def exercise():
        portal, session = _portal([])
        with pytest.raises(ValueError):
            await portal.set_ev_charging(key, charging=charging)
        assert session.calls == []

    asyncio.run(exercise())


def test_bearer_only_session_can_submit_without_a_legacy_csrf_cookie() -> None:
    async def exercise():
        portal, session = _portal(
            [
                _Response(200, DISCOVERY),
                _Response(200, PASSED),
            ],
            csrf=False,
        )
        await portal.set_ev_charging(KEY, charging=False)
        assert [call[0] for call in session.calls] == ["GET", "PUT"]
        headers = session.calls[-1][2]["headers"]
        assert headers["Authorization"] == "Bearer synthetic"
        assert "X-CSRF-TOKEN" not in headers

    asyncio.run(exercise())


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_write_http_failures_never_follow_redirects_or_retry(status) -> None:
    async def exercise():
        portal, session = _portal(
            [
                _Response(200, DISCOVERY),
                _Response(status),
                _Response(200, PASSED),
            ]
        )
        with pytest.raises(SolarEdgePortalError):
            await portal.set_ev_charging(KEY, charging=True)
        assert [call[0] for call in session.calls] == ["GET", "PUT"]

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "result",
    [
        None,
        [],
        {},
        {"status": "FAILED"},
        {"status": "PASSED", "httpStatus": 400},
        {"status": "PASSED", "errorMessages": ["private-detail"]},
        "not-json-private-detail",
    ],
)
def test_http_200_requires_application_confirmation_without_echoing_payload(
    result,
) -> None:
    async def exercise():
        portal, session = _portal([_Response(200, DISCOVERY), _Response(200, result)])
        with pytest.raises(SolarEdgePortalError) as captured:
            await portal.set_ev_charging(KEY, charging=False)
        assert "private-detail" not in str(captured.value)
        assert [call[0] for call in session.calls] == ["GET", "PUT"]

    asyncio.run(exercise())


def test_device_disappearing_before_press_prevents_put() -> None:
    async def exercise():
        portal, session = _portal(
            [_Response(200, {"devicesByType": {"EV_CHARGER": []}})]
        )
        with pytest.raises(ValueError, match="EV charger"):
            await portal.set_ev_charging(KEY, charging=True)
        assert [call[0] for call in session.calls] == ["GET"]

    asyncio.run(exercise())


class _TimeoutSession(_Session):
    async def request(self, method, url, **kwargs):
        if method == "PUT":
            self.calls.append((method, url, kwargs))
            raise TimeoutError
        return await super().request(method, url, **kwargs)


def test_ambiguous_write_timeout_is_not_retried() -> None:
    async def exercise():
        portal, session = _portal(
            [_Response(200, DISCOVERY)], session_type=_TimeoutSession
        )
        with pytest.raises(SolarEdgePortalError, match="timed out"):
            await portal.set_ev_charging(KEY, charging=True)
        assert [call[0] for call in session.calls] == ["GET", "PUT"]

    asyncio.run(exercise())


class _InterruptedBody:
    def __init__(self, error):
        self.error = error

    async def iter_chunked(self, _):
        yield b'{"status":'
        raise self.error


@pytest.mark.parametrize(
    "error", [TimeoutError(), ClientPayloadError("private-detail")]
)
def test_interrupted_put_body_is_sanitized_without_retry(error) -> None:
    async def exercise():
        response = _Response(200, PASSED)
        response.content = _InterruptedBody(error)
        portal, session = _portal([_Response(200, DISCOVERY), response])
        with pytest.raises(SolarEdgePortalError) as captured:
            await portal.set_ev_charging(KEY, charging=True)
        assert "private-detail" not in str(captured.value)
        assert [call[0] for call in session.calls] == ["GET", "PUT"]

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "error", [TimeoutError(), ClientPayloadError("private-detail")]
)
def test_interrupted_response_refreshes_actual_state_through_the_coordinator(
    error,
) -> None:
    from datetime import datetime, timedelta
    from types import SimpleNamespace

    from test_ev_entities import (
        EVChargerSnapshot,
        _coordinator_class,
        _HomeAssistantError,
    )

    async def exercise():
        response = _Response(200, PASSED)
        response.content = _InterruptedBody(error)
        actual = {
            "devicesByType": {
                "EV_CHARGER": [
                    {
                        "reporterId": 42,
                        "activationMode": "MANUAL",
                        "chargerStatus": "CHARGING",
                    }
                ]
            }
        }
        flow = {
            "evCharger": {"currentPower": 11},
            "lastUpdateTime": datetime.now(UTC).isoformat(),
        }
        portal, session = _portal(
            [
                _Response(200, DISCOVERY),
                response,
                _Response(200, actual),
                _Response(200, flow),
            ]
        )
        client = _load("portal_client").SolarEdgePortalSnapshotClient(
            session,
            username="synthetic@example.test",
            password="synthetic",
            site_id="1234567",
            time_zone=UTC,
            timeout_seconds=10,
            max_snapshot_age_seconds=900,
        )
        client._portal = portal
        coordinator = _coordinator_class()(
            None, SimpleNamespace(), client, update_interval=timedelta(seconds=60)
        )
        coordinator.data = {KEY: EVChargerSnapshot(KEY, "PLUGGED_IN", "AUTO", 0)}
        with pytest.raises(_HomeAssistantError, match="could not confirm"):
            await coordinator.async_set_charging(KEY, charging=True)
        assert coordinator.data[KEY].activation_mode == "MANUAL"
        assert coordinator.data[KEY].status == "CHARGING"
        assert [call[0] for call in session.calls].count("PUT") == 1

    asyncio.run(exercise())
