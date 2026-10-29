"""Tests for the dependency-free snapshot trust boundary."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "solaredge_one_bridge"


def _load_module(name: str):
    package_name = "custom_components.solaredge_one_bridge"
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(COMPONENT)]
        sys.modules[package_name] = package
    qualified = f"{package_name}.{name}"
    spec = importlib.util.spec_from_file_location(qualified, COMPONENT / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module


const = _load_module("const")
model = _load_module("model")
client_module = _load_module("client")


def _connected_snapshot(**overrides):
    payload = {
        "schema_version": 1,
        "connected": True,
        "observed_at": datetime.now(UTC).isoformat(),
        "provider": "documented_api",
        "site": {},
        "completeness": {},
    }
    payload.update(overrides)
    return payload


def test_parser_allowlists_metrics_and_drops_sensitive_fields() -> None:
    snapshot = model.parse_snapshot(
        _connected_snapshot(
            site={
                "production_power_w": 8123,
                "grid_import_energy_kwh": 42.5,
                "battery_state_of_energy_pct": 61,
                "storage_operating_plan": "Time of Use",
                "storage_operating_plan_active": True,
                "storage_operating_plan_block_count": 4,
                "storage_operating_plan_policy": {"private": "sentinel"},
                "site_id": "sentinel",
                "address": "sentinel",
                "serial": "sentinel",
                "api_key": "sentinel",
            },
            completeness={
                "production_power_w": True,
                "storage_operating_plan": True,
                "site_id": True,
            },
            credentials={"secret": "sentinel"},
        )
    )

    assert snapshot.site == {
        "production_power_w": 8123.0,
        "grid_import_energy_kwh": 42.5,
        "battery_state_of_energy_pct": 61.0,
    }
    assert snapshot.completeness == {
        "production_power_w": True,
        "storage_operating_plan": True,
    }
    assert snapshot.storage_operating_plan == "Time of Use"
    assert snapshot.storage_operating_plan_active is True
    assert snapshot.storage_operating_plan_block_count == 4
    assert "sentinel" not in repr(snapshot)


def test_parser_rejects_invalid_envelopes_and_schema_versions() -> None:
    invalid = (
        None,
        {},
        {"schema_version": 2, "connected": False, "site": {}},
        {"schema_version": 1, "connected": "yes", "site": {}},
        {"schema_version": 1, "connected": True, "site": {}},
        {
            "schema_version": 1,
            "connected": True,
            "observed_at": "2026-01-01T00:00:00",
            "site": {},
        },
        {"schema_version": 1, "connected": False, "site": []},
        {
            "schema_version": 1,
            "connected": False,
            "site": {},
            "completeness": [],
        },
    )
    for payload in invalid:
        try:
            model.parse_snapshot(payload)
        except model.InvalidSnapshot:
            pass
        else:
            raise AssertionError(f"invalid payload accepted: {payload!r}")


def test_invalid_metrics_are_unavailable_not_coerced() -> None:
    snapshot = model.parse_snapshot(
        _connected_snapshot(
            site={
                "production_power_w": float("nan"),
                "consumption_power_w": -1,
                "grid_import_power_w": True,
                "grid_export_power_w": 100,
                "battery_state_of_energy_pct": 101,
            },
            completeness={"grid_export_power_w": False},
        )
    )
    assert snapshot.site == {"grid_export_power_w": 100.0}
    assert snapshot.value("grid_export_power_w") is None


def test_freshness_rejects_old_and_future_connected_snapshots() -> None:
    now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    old = model.parse_snapshot(
        _connected_snapshot(observed_at=(now - timedelta(minutes=16)).isoformat())
    )
    future = model.parse_snapshot(
        _connected_snapshot(observed_at=(now + timedelta(minutes=6)).isoformat())
    )
    for snapshot in (old, future):
        try:
            snapshot.validate_freshness(900, now=now)
        except model.InvalidSnapshot:
            pass
        else:
            raise AssertionError("invalid snapshot freshness accepted")


def test_disconnected_snapshot_may_omit_observation_time() -> None:
    snapshot = model.parse_snapshot(
        {"schema_version": 1, "connected": False, "observed_at": None, "site": {}}
    )
    snapshot.validate_freshness(900)
    assert snapshot.observed_at is None


class _FakeContent:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self._offset = 0

    async def read(self, count: int) -> bytes:
        chunk = self._body[self._offset : self._offset + count]
        self._offset += len(chunk)
        return chunk


_UNSET = object()


class _FakeResponse:
    def __init__(
        self,
        status: int,
        payload=None,
        *,
        raw_body: bytes | None = None,
        content_length: int | None | object = _UNSET,
    ) -> None:
        body = raw_body if raw_body is not None else json.dumps(payload).encode()
        self.status = status
        self.content = _FakeContent(body)
        self.content_length = len(body) if content_length is _UNSET else content_length

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls = []

    def get(self, endpoint, **kwargs):
        self.calls.append((endpoint, kwargs))
        return self.response


def _client_for(response: _FakeResponse, secret: str = "a" * 32):
    session = _FakeSession(response)
    client = client_module.SolarEdgeBridgeClient(
        session, "http://127.0.0.1:8765/v1/snapshot", secret
    )
    return client, session


def test_client_sends_only_secret_header_and_refuses_redirects() -> None:
    client, session = _client_for(_FakeResponse(200, _connected_snapshot()))
    result = asyncio.run(client.async_get_snapshot())
    assert result.connected is True
    _, kwargs = session.calls[0]
    assert kwargs["headers"] == {const.BRIDGE_SECRET_HEADER: "a" * 32}
    assert kwargs["allow_redirects"] is False

    redirect_client, _ = _client_for(_FakeResponse(302, {}))
    try:
        asyncio.run(redirect_client.async_get_snapshot())
    except client_module.BridgeConnectionError:
        pass
    else:
        raise AssertionError("redirect was accepted")


def test_client_classifies_authentication_failures() -> None:
    for status in (401, 403):
        client, _ = _client_for(_FakeResponse(status, {}))
        try:
            asyncio.run(client.async_get_snapshot())
        except client_module.BridgeAuthenticationError:
            pass
        else:
            raise AssertionError(f"HTTP {status} was not classified as auth failure")


def test_client_bounds_response_before_json_parsing() -> None:
    oversized = const.MAX_RESPONSE_BYTES + 1
    client, _ = _client_for(
        _FakeResponse(200, raw_body=b"{}", content_length=oversized)
    )
    try:
        asyncio.run(client.async_get_snapshot())
    except client_module.BridgeSnapshotTooLargeError:
        pass
    else:
        raise AssertionError("oversized Content-Length was accepted")

    client, _ = _client_for(
        _FakeResponse(200, raw_body=b"x" * oversized, content_length=None)
    )
    try:
        asyncio.run(client.async_get_snapshot())
    except client_module.BridgeSnapshotTooLargeError:
        pass
    else:
        raise AssertionError("oversized streamed response was accepted")


def test_client_classifies_stale_connected_snapshot() -> None:
    payload = _connected_snapshot(
        observed_at=(datetime.now(UTC) - timedelta(minutes=16)).isoformat()
    )
    client, _ = _client_for(_FakeResponse(200, payload))
    try:
        asyncio.run(client.async_get_snapshot())
    except client_module.BridgeStaleSnapshotError:
        pass
    else:
        raise AssertionError("stale connected snapshot was accepted")


def test_endpoint_validation_is_local_and_credential_safe() -> None:
    accepted = (
        "http://127.0.0.1:8765/v1/snapshot",
        "http://localhost:8765/v1/snapshot",
        "http://bridge-addon:8765/v1/snapshot",
        "https://192.168.10.20/v1/snapshot",
        "http://[fd00::20]:8765/v1/snapshot",
    )
    rejected = (
        "https://api.example.com/v1/snapshot",
        "https://8.8.8.8/v1/snapshot",
        "https://192.0.2.44/v1/snapshot",
        "http://0.0.0.0/v1/snapshot",
        "http://user:password@127.0.0.1/v1/snapshot",
        "http://127.0.0.1/v1/snapshot?token=value",
        "http://127.0.0.1/v1/snapshot#fragment",
        "file:///config/secrets.yaml",
        "http://bad_host/v1/snapshot",
    )
    assert all(client_module.is_local_endpoint(value) for value in accepted)
    assert not any(client_module.is_local_endpoint(value) for value in rejected)


def test_contract_contains_lifetime_daily_power_and_plan_fields() -> None:
    assert len(model.POWER_KEYS) == 6
    assert len(model.ENERGY_KEYS) == 6
    assert len(model.PERCENTAGE_KEYS) == 1
    assert len(model.DAILY_ENERGY_KEYS) == 6
    assert len(model.METRIC_KEYS) == 19
    assert len(model.STORAGE_STATUS_KEYS) == 3
