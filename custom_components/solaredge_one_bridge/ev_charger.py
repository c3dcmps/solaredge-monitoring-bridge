"""Allowlisted EV charger data, independent of the aggregate snapshot protocol."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

_REPORTER_ID = re.compile(r"[1-9][0-9]{0,31}")
_STATUS = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


@dataclass(frozen=True, slots=True)
class EVChargerSnapshot:
    """Safe EV values with an opaque identity, never provider identifiers."""

    key: str
    status: str | None
    activation_mode: str | None
    power_w: float | None


def _status(value: object) -> str | None:
    return value if isinstance(value, str) and _STATUS.fullmatch(value) else None


def _power(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _identity(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    candidate = str(value)
    return candidate if _REPORTER_ID.fullmatch(candidate) else None


def parse_ev_chargers(
    payload: object, *, site_id: str, power_w: object = None
) -> dict[str, EVChargerSnapshot]:
    """Validate discovery and retain safe values for uniquely identified chargers.

    Args:
        payload: Device-list response from the fixed SolarEdge endpoint.
        site_id: Configured site, used only to scope the hashed device identity.
        power_w: Whole-site EV power in watts, attributable only to a single device.

    Returns:
        Snapshots keyed by a SHA-256 identity scoped to the site and reporter ID.

    Raises:
        ValueError: If the response envelope is not a device list.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("SolarEdge EV charger response is not a device list")
    grouped = payload.get("devicesByType")
    if not isinstance(grouped, Mapping):
        raise ValueError("SolarEdge EV charger device groups are invalid")
    devices = grouped.get("EV_CHARGER", [])
    if not isinstance(devices, list):
        raise ValueError("SolarEdge EV charger list is invalid")
    identified = [
        (_identity(device.get("reporterId")), device)
        for device in devices
        if isinstance(device, Mapping)
    ]
    counts = Counter(identity for identity, _ in identified)
    power = _power(power_w) if len(devices) == 1 else None
    result = {}
    for identity, device in identified:
        if identity is None or counts[identity] != 1:
            continue
        key = hashlib.sha256(f"{site_id}:{identity}".encode()).hexdigest()
        result[key] = EVChargerSnapshot(
            key=key,
            status=_status(device.get("chargerStatus")),
            activation_mode=_status(device.get("activationMode")),
            power_w=power,
        )
    return result


def resolve_ev_reporter_id(payload: object, *, site_id: str, key: str) -> str:
    """Resolve an opaque key against a fresh, unambiguous device discovery.

    Args:
        payload: Fresh SolarEdge device-list response.
        site_id: Configured site used to scope the opaque key.
        key: Opaque charger key already exposed by the integration.

    Returns:
        Validated reporter ID for this operation only.

    Raises:
        ValueError: If discovery cannot resolve this key uniquely within the site.
    """
    if not isinstance(payload, Mapping) or key not in parse_ev_chargers(
        payload, site_id=site_id
    ):
        raise ValueError(
            "SolarEdge EV charger is no longer available; refresh device discovery"
        )
    for device in payload["devicesByType"]["EV_CHARGER"]:
        if not isinstance(device, Mapping):
            continue
        identity = _identity(device.get("reporterId"))
        if (
            identity is not None
            and hashlib.sha256(f"{site_id}:{identity}".encode()).hexdigest() == key
        ):
            return identity
    raise ValueError("SolarEdge EV charger identity could not be resolved")
