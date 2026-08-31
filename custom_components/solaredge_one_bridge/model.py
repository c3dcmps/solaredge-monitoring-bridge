"""Validation and sanitization for local bridge snapshots.

This module has no Home Assistant dependencies. It is the integration's strict trust
boundary: only documented, whole-site aggregate values cross into Home Assistant.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from .const import MAX_FUTURE_SKEW_SECONDS, SNAPSHOT_SCHEMA_VERSION

POWER_KEYS: Final = frozenset(
    {
        "production_power_w",
        "consumption_power_w",
        "grid_import_power_w",
        "grid_export_power_w",
        "battery_charge_power_w",
        "battery_discharge_power_w",
    }
)
ENERGY_KEYS: Final = frozenset(
    {
        "production_energy_kwh",
        "consumption_energy_kwh",
        "grid_import_energy_kwh",
        "grid_export_energy_kwh",
        "battery_charge_energy_kwh",
        "battery_discharge_energy_kwh",
    }
)
PERCENTAGE_KEYS: Final = frozenset({"battery_state_of_energy_pct"})
METRIC_KEYS: Final = POWER_KEYS | ENERGY_KEYS | PERCENTAGE_KEYS
STORAGE_STATUS_KEYS: Final = frozenset(
    {
        "storage_operating_plan",
        "storage_operating_plan_active",
        "storage_operating_plan_block_count",
    }
)
SNAPSHOT_KEYS: Final = METRIC_KEYS | STORAGE_STATUS_KEYS
PROVIDER_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ /-]{0,63}")
OPERATING_PLAN_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ /-]{0,63}")
MAX_OPERATING_PLAN_BLOCKS: Final = 1000


class InvalidSnapshot(ValueError):
    """Raised when a bridge response violates the public snapshot contract."""


@dataclass(frozen=True, slots=True)
class SolarEdgeSnapshot:
    """Sanitized aggregate data safe to expose to Home Assistant."""

    schema_version: int
    connected: bool
    observed_at: datetime | None
    provider: str | None
    site: Mapping[str, float]
    completeness: Mapping[str, bool]
    storage_operating_plan: str | None
    storage_operating_plan_active: bool | None
    storage_operating_plan_block_count: int | None

    def value(self, key: str) -> float | None:
        """Return a metric only when present and not explicitly incomplete."""
        if self.completeness.get(key) is False:
            return None
        return self.site.get(key)

    @property
    def observed_at_iso(self) -> str | None:
        """Return a normalized UTC timestamp suitable for diagnostics."""
        if self.observed_at is None:
            return None
        return self.observed_at.astimezone(UTC).isoformat()

    def age_seconds(self, *, now: datetime | None = None) -> float | None:
        """Return signed snapshot age in seconds."""
        if self.observed_at is None:
            return None
        current = now or datetime.now(UTC)
        return (current - self.observed_at).total_seconds()

    def validate_freshness(
        self, max_age_seconds: int, *, now: datetime | None = None
    ) -> None:
        """Reject connected snapshots that are stale or implausibly future-dated."""
        if not self.connected:
            return
        age = self.age_seconds(now=now)
        if age is None:
            raise InvalidSnapshot("connected snapshots require observed_at")
        if age < -MAX_FUTURE_SKEW_SECONDS:
            raise InvalidSnapshot("observed_at is too far in the future")
        if age > max_age_seconds:
            raise InvalidSnapshot("snapshot is stale")


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise InvalidSnapshot("observed_at must be a non-empty ISO 8601 string or null")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as err:
        raise InvalidSnapshot("observed_at must be an ISO 8601 timestamp") from err
    if parsed.tzinfo is None:
        raise InvalidSnapshot("observed_at must include a timezone")
    return parsed


def _parse_metric(key: str, value: Any) -> float | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        return None
    if key in PERCENTAGE_KEYS and numeric > 100:
        return None
    return numeric


def _parse_bounded_label(value: Any, pattern: re.Pattern[str]) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if pattern.fullmatch(normalized) else None


def _parse_optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _parse_block_count(value: Any) -> int | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= MAX_OPERATING_PLAN_BLOCKS
    ):
        return None
    return value


def parse_snapshot(payload: Any) -> SolarEdgeSnapshot:
    """Validate an envelope and retain only allowlisted aggregate fields.

    Unknown fields are ignored. Site identifiers, addresses, serial numbers,
    credentials, and arbitrary provider objects therefore cannot become states or
    attributes accidentally.
    """
    if not isinstance(payload, Mapping):
        raise InvalidSnapshot("snapshot must be an object")
    if payload.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise InvalidSnapshot("unsupported schema_version")

    connected = payload.get("connected")
    if not isinstance(connected, bool):
        raise InvalidSnapshot("connected must be a boolean")

    raw_site = payload.get("site")
    if not isinstance(raw_site, Mapping):
        raise InvalidSnapshot("site must be an object")

    observed_at = _parse_timestamp(payload.get("observed_at"))
    if connected and observed_at is None:
        raise InvalidSnapshot("connected snapshots require observed_at")

    site: dict[str, float] = {}
    for key in METRIC_KEYS:
        parsed = _parse_metric(key, raw_site.get(key))
        if parsed is not None:
            site[key] = parsed

    raw_completeness = payload.get("completeness", {})
    if not isinstance(raw_completeness, Mapping):
        raise InvalidSnapshot("completeness must be an object")
    completeness = {
        key: value
        for key, value in raw_completeness.items()
        if key in SNAPSHOT_KEYS and isinstance(value, bool)
    }

    return SolarEdgeSnapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        connected=connected,
        observed_at=observed_at,
        provider=_parse_bounded_label(payload.get("provider"), PROVIDER_PATTERN),
        site=site,
        completeness=completeness,
        storage_operating_plan=_parse_bounded_label(
            raw_site.get("storage_operating_plan"), OPERATING_PLAN_PATTERN
        ),
        storage_operating_plan_active=_parse_optional_bool(
            raw_site.get("storage_operating_plan_active")
        ),
        storage_operating_plan_block_count=_parse_block_count(
            raw_site.get("storage_operating_plan_block_count")
        ),
    )
