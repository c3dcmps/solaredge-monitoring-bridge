"""HTTP client for the local snapshot bridge."""

from __future__ import annotations

import ipaddress
import json
import re
from typing import Any
from urllib.parse import urlparse

import aiohttp

from .const import (
    BRIDGE_SECRET_HEADER,
    DEFAULT_MAX_SNAPSHOT_AGE_SECONDS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    MAX_RESPONSE_BYTES,
)
from .model import InvalidSnapshot, SolarEdgeSnapshot, parse_snapshot

LOCAL_HOSTNAME_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)


class BridgeError(Exception):
    """Base error for bridge communication failures."""


class BridgeAuthenticationError(BridgeError):
    """Raised when the bridge rejects the shared secret."""


class BridgeConnectionError(BridgeError):
    """Raised when the bridge cannot be reached or returns an invalid response."""


class BridgeSnapshotTooLargeError(BridgeConnectionError):
    """Raised when a response exceeds the fixed public protocol limit."""


class BridgeStaleSnapshotError(BridgeConnectionError):
    """Raised when a connected bridge returns stale data."""


def is_local_endpoint(endpoint: str) -> bool:
    """Return whether an endpoint is syntactically local/private and HTTP(S)."""
    if not isinstance(endpoint, str) or len(endpoint) > 2048:
        return False
    try:
        parsed = urlparse(endpoint)
        hostname = parsed.hostname
        if parsed.scheme not in {"http", "https"} or not hostname:
            return False
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            return False
        if not parsed.path.startswith("/"):
            return False
        _ = parsed.port
    except ValueError:
        return False

    folded = hostname.casefold()
    if folded == "localhost":
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        # Single-label names support Docker/add-on DNS while refusing public FQDNs.
        return "." not in hostname and bool(LOCAL_HOSTNAME_PATTERN.fullmatch(hostname))
    if address.is_unspecified or address.is_multicast:
        return False
    if address.is_loopback or address.is_link_local:
        return True
    return any(
        address.version == network.version and address in network
        for network in PRIVATE_NETWORKS
    )


class SolarEdgeBridgeClient:
    """Fetch sanitized snapshots from an explicitly local bridge."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        endpoint: str,
        shared_secret: str,
        *,
        timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        max_snapshot_age_seconds: int = DEFAULT_MAX_SNAPSHOT_AGE_SECONDS,
    ) -> None:
        if not is_local_endpoint(endpoint):
            raise ValueError("bridge endpoint must be local or private")
        self._session = session
        self._endpoint = endpoint
        self._shared_secret = shared_secret
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._max_snapshot_age_seconds = max_snapshot_age_seconds

    async def _async_read_bounded_json(self, response: aiohttp.ClientResponse) -> Any:
        if (
            response.content_length is not None
            and response.content_length > MAX_RESPONSE_BYTES
        ):
            raise BridgeSnapshotTooLargeError("bridge response exceeds 64 KiB")

        body = bytearray()
        while True:
            remaining = MAX_RESPONSE_BYTES + 1 - len(body)
            chunk = await response.content.read(min(16384, remaining))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise BridgeSnapshotTooLargeError("bridge response exceeds 64 KiB")
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise BridgeConnectionError("bridge returned invalid JSON") from err

    async def async_get_snapshot(self) -> SolarEdgeSnapshot:
        """Retrieve, bound, validate, and freshness-check one snapshot."""
        try:
            async with self._session.get(
                self._endpoint,
                headers={BRIDGE_SECRET_HEADER: self._shared_secret},
                timeout=self._timeout,
                allow_redirects=False,
            ) as response:
                if response.status in (401, 403):
                    raise BridgeAuthenticationError("bridge authentication failed")
                if 300 <= response.status < 400:
                    raise BridgeConnectionError("bridge redirects are not allowed")
                if response.status != 200:
                    raise BridgeConnectionError(
                        f"bridge returned HTTP {response.status}"
                    )
                payload = await self._async_read_bounded_json(response)
        except BridgeError:
            raise
        except (TimeoutError, aiohttp.ClientError) as err:
            raise BridgeConnectionError("bridge request failed") from err

        try:
            snapshot = parse_snapshot(payload)
            snapshot.validate_freshness(self._max_snapshot_age_seconds)
        except InvalidSnapshot as err:
            message = str(err)
            if message in {"snapshot is stale", "observed_at is too far in the future"}:
                raise BridgeStaleSnapshotError(message) from err
            raise BridgeConnectionError("bridge returned an invalid snapshot") from err
        return snapshot
