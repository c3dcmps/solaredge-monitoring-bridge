"""Adapter from the unofficial SolarEdge portal to snapshot protocol v1."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from datetime import UTC, datetime, tzinfo
from typing import Any

import aiohttp

from .model import SolarEdgeSnapshot, parse_snapshot
from .portal import SolarEdgePortalClient, SolarEdgePortalConfig

LIFETIME_CACHE_SECONDS = 6 * 60 * 60


class SolarEdgePortalSnapshotClient:
    """Expose only allowlisted aggregate portal values as a validated snapshot."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        username: str,
        password: str,
        site_id: str,
        time_zone: tzinfo,
        timeout_seconds: int,
        max_snapshot_age_seconds: int,
    ) -> None:
        config = SolarEdgePortalConfig(
            username=username,
            password=password,
            site_id=site_id,
            time_zone=time_zone,
            timeout_seconds=float(timeout_seconds),
        )
        self._portal = SolarEdgePortalClient(config, session=session)
        self._time_zone = time_zone
        self._max_snapshot_age_seconds = max_snapshot_age_seconds
        self._lifetime_cache: tuple[float, dict[str, Any]] | None = None
        self._lifetime_lock = asyncio.Lock()

    async def async_get_snapshot(self) -> SolarEdgeSnapshot:
        """Fetch live flow plus bounded-cache lifetime counters."""
        flow, lifetime = await asyncio.gather(
            self._portal.live_power_flow(), self._async_lifetime_energy()
        )
        payload = _build_snapshot_payload(flow, lifetime, self._time_zone)
        snapshot = parse_snapshot(payload)
        snapshot.validate_freshness(self._max_snapshot_age_seconds)
        return snapshot

    async def _async_lifetime_energy(self) -> dict[str, Any]:
        now = time.monotonic()
        cached = self._lifetime_cache
        if cached is not None and now - cached[0] < LIFETIME_CACHE_SECONDS:
            return cached[1]
        async with self._lifetime_lock:
            now = time.monotonic()
            cached = self._lifetime_cache
            if cached is not None and now - cached[0] < LIFETIME_CACHE_SECONDS:
                return cached[1]
            result = await self._portal.lifetime_energy_summary()
            self._lifetime_cache = (now, result)
            return result


def _component_number(component: Any, *keys: str) -> float | None:
    if not isinstance(component, Mapping):
        return None
    for key in keys:
        value = component.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0.0, float(value))
    return None


def _component_text(component: Any, *keys: str) -> str:
    if not isinstance(component, Mapping):
        return ""
    for key in keys:
        value = component.get(key)
        if isinstance(value, str):
            return value.casefold()
    return ""


def _provider_observed_at(value: Any, time_zone: tzinfo) -> str:
    """Normalize provider local timestamps to timezone-aware UTC timestamps."""
    if isinstance(value, str) and value.strip():
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            pass
        else:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=time_zone)
            return parsed.astimezone(UTC).isoformat()
    return datetime.now(UTC).isoformat()


def _build_snapshot_payload(
    flow: Mapping[str, Any],
    lifetime: Mapping[str, Any],
    time_zone: tzinfo,
) -> dict[str, Any]:
    """Map sanitized portal summaries into the provider-neutral contract."""
    components = flow.get("components", {})
    if not isinstance(components, Mapping):
        components = {}

    solar = components.get("solar_production", {})
    consumption = components.get("consumption", {})
    grid = components.get("grid", {})
    storage_candidates = [
        item
        for item in (components.get("dc_storage"), components.get("ac_storage"))
        if isinstance(item, Mapping)
    ]
    storage = next(
        (
            item
            for item in storage_candidates
            if _component_number(item, "chargeLevel", "stateOfCharge") is not None
        ),
        storage_candidates[0] if storage_candidates else {},
    )

    grid_power = _component_number(grid, "current_power_w", "power_w")
    grid_status = _component_text(grid, "status", "direction")
    grid_import = (
        grid_power
        if grid_power is not None
        and ("import" in grid_status or "from_grid" in grid_status)
        else 0.0
        if grid_power is not None
        else None
    )
    grid_export = (
        grid_power
        if grid_power is not None
        and ("export" in grid_status or "to_grid" in grid_status)
        else 0.0
        if grid_power is not None
        else None
    )

    storage_power = _component_number(storage, "current_power_w", "power_w")
    storage_status = _component_text(storage, "status", "direction")
    battery_charge = (
        storage_power
        if storage_power is not None
        and "charg" in storage_status
        and "discharg" not in storage_status
        else 0.0
        if storage_power is not None
        else None
    )
    battery_discharge = (
        storage_power
        if storage_power is not None and "discharg" in storage_status
        else 0.0
        if storage_power is not None
        else None
    )

    totals = lifetime.get("totals_kwh", {})
    production_distribution = lifetime.get("production_distribution", {})
    consumption_distribution = lifetime.get("consumption_distribution", {})
    if not isinstance(totals, Mapping):
        totals = {}
    if not isinstance(production_distribution, Mapping):
        production_distribution = {}
    if not isinstance(consumption_distribution, Mapping):
        consumption_distribution = {}

    operating_plan = flow.get("storage_operating_plan", {})
    if not isinstance(operating_plan, Mapping):
        operating_plan = {}
    site: dict[str, Any] = {
        "production_power_w": _component_number(solar, "current_power_w", "power_w"),
        "consumption_power_w": _component_number(
            consumption, "current_power_w", "power_w"
        ),
        "grid_import_power_w": grid_import,
        "grid_export_power_w": grid_export,
        "battery_charge_power_w": battery_charge,
        "battery_discharge_power_w": battery_discharge,
        "battery_state_of_energy_pct": _component_number(
            storage, "chargeLevel", "stateOfCharge"
        ),
        "production_energy_kwh": _component_number(totals, "production"),
        "consumption_energy_kwh": _component_number(totals, "consumption"),
        "grid_import_energy_kwh": _component_number(totals, "import"),
        "grid_export_energy_kwh": _component_number(totals, "export"),
        "battery_charge_energy_kwh": _component_number(
            production_distribution, "production_to_battery_kwh", "storage_kwh"
        ),
        "battery_discharge_energy_kwh": _component_number(
            consumption_distribution, "consumption_from_battery_kwh", "storage_kwh"
        ),
        "storage_operating_plan": operating_plan.get("plan"),
        "storage_operating_plan_active": operating_plan.get("is_active"),
        "storage_operating_plan_block_count": operating_plan.get("block_count"),
    }
    return {
        "schema_version": 1,
        "connected": True,
        "provider": "solaredge_monitoring_portal",
        "observed_at": _provider_observed_at(
            flow.get("last_update_time"), time_zone
        ),
        "site": {key: value for key, value in site.items() if value is not None},
        "completeness": {key: value is not None for key, value in site.items()},
    }
