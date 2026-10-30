"""Adapter from the unofficial SolarEdge portal to snapshot protocol v1."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from datetime import UTC, date, datetime, tzinfo
from typing import Any

import aiohttp

from custom_components.solaredge_one_bridge.battery_configuration import (
    BatteryConfigurationSnapshot,
    parse_battery_configuration,
)
from custom_components.solaredge_one_bridge.const import MAX_FUTURE_SKEW_SECONDS
from custom_components.solaredge_one_bridge.ev_charger import (
    EVChargerSnapshot,
    parse_ev_chargers,
    parse_ev_schedules,
)
from custom_components.solaredge_one_bridge.ev_schedule import EVScheduleSnapshot
from custom_components.solaredge_one_bridge.model import (
    DAILY_ENERGY_KEYS,
    SolarEdgeSnapshot,
    parse_snapshot,
)
from custom_components.solaredge_one_bridge.portal import (
    SolarEdgePortalAuthenticationError,
    SolarEdgePortalClient,
    SolarEdgePortalConfig,
    SolarEdgePortalError,
)

LIFETIME_CACHE_SECONDS = 6 * 60 * 60
DAILY_CACHE_SECONDS = 5 * 60
_LOGGER = logging.getLogger(__name__)


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
        self._daily_cache: tuple[date, float, dict[str, Any]] | None = None
        self._daily_lock = asyncio.Lock()

    async def async_get_snapshot(self) -> SolarEdgeSnapshot:
        """Fetch live flow and separately cached lifetime and daily counters."""
        flow, lifetime, daily = await asyncio.gather(
            self._portal.live_power_flow(),
            self._async_lifetime_energy(),
            self._async_daily_energy(),
        )
        payload = _build_snapshot_payload(flow, lifetime, self._time_zone)
        payload["daily_energy_date"] = daily.get("start_date")
        daily_values = _daily_energy_values(daily)
        payload["site"].update(daily_values)
        payload["completeness"].update(
            {key: key in daily_values for key in DAILY_ENERGY_KEYS}
        )
        snapshot = parse_snapshot(payload)
        snapshot.validate_freshness(self._max_snapshot_age_seconds)
        return snapshot

    async def _async_daily_energy(self) -> dict[str, Any]:
        async with self._daily_lock:
            selected = datetime.now(self._time_zone).date()
            now = time.monotonic()
            cached = self._daily_cache
            if (
                cached
                and cached[0] == selected
                and now - cached[1] < DAILY_CACHE_SECONDS
            ):
                return cached[2]
            try:
                result = await self._portal.current_day_energy_summary(
                    local_date=selected
                )
            except SolarEdgePortalAuthenticationError:
                raise
            except SolarEdgePortalError:
                self._daily_cache = None
                _LOGGER.warning(
                    "SolarEdge daily energy is unavailable; retrying on the next poll"
                )
                return {}
            expected = selected.isoformat()
            if (
                datetime.now(self._time_zone).date() != selected
                or result.get("start_date") != expected
                or result.get("end_date") != expected
            ):
                self._daily_cache = None
                return {}
            self._daily_cache = (selected, now, result)
            return result

    async def async_get_ev_chargers(self) -> dict[str, EVChargerSnapshot]:
        """Read EV status and associate fresh aggregate power only when unambiguous."""
        devices, power = await asyncio.gather(
            self._portal.ev_chargers(), self._async_ev_power()
        )
        return parse_ev_chargers(
            devices, site_id=self._portal.config.site_id, power_w=power
        )

    async def async_set_ev_charging(self, key: str, *, charging: bool) -> None:
        """Submit a manual request through the existing authenticated portal."""
        await self._portal.set_ev_charging(key, charging=charging)

    async def async_get_ev_schedules(self) -> dict[str, EVScheduleSnapshot]:
        """Read schedule metadata using only the existing authenticated device GET."""
        payload = await self._portal.ev_chargers()
        return parse_ev_schedules(
            payload, site_id=self._portal.config.site_id, time_zone=self._time_zone
        )

    async def async_get_battery_configuration(self) -> BatteryConfigurationSnapshot:
        """Read both settings endpoints, preserving independently successful data."""
        results = await asyncio.gather(
            self._portal.battery_configuration(),
            self._portal.battery_reserve(),
            return_exceptions=True,
        )
        payloads = []
        for name, result in zip(("mode", "reserve"), results, strict=True):
            if isinstance(result, SolarEdgePortalError):
                _LOGGER.debug("SolarEdge battery %s settings read is unavailable", name)
                payloads.append(None)
            elif isinstance(result, BaseException):
                raise result
            else:
                payloads.append(result)
        return parse_battery_configuration(
            payloads[0], payloads[1], time_zone=self._time_zone
        )

    @property
    def ev_control_diagnostics(self) -> dict[str, bool]:
        """Return only session-readiness flags for EV diagnostics."""
        return self._portal.ev_control_diagnostics

    async def _async_ev_power(self) -> object:
        try:
            flow = await self._portal.live_power_flow()
        except SolarEdgePortalError:
            _LOGGER.debug("SolarEdge EV power flow is unavailable")
            return None
        timestamp = flow.get("last_update_time")
        if not isinstance(timestamp, str):
            return None
        try:
            observed_at = datetime.fromisoformat(
                timestamp.strip().replace("Z", "+00:00")
            )
        except ValueError:
            return None
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=self._time_zone)
        age = (datetime.now(UTC) - observed_at).total_seconds()
        if not -MAX_FUTURE_SKEW_SECONDS <= age <= self._max_snapshot_age_seconds:
            return None
        components = flow.get("components", {})
        component = (
            components.get("ev_charger") if isinstance(components, Mapping) else None
        )
        if not isinstance(component, Mapping):
            return None
        return component.get("current_power_w", component.get("power_w"))

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


def _daily_energy_values(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Select daily energy fields; numeric validation stays in the snapshot parser."""
    totals = summary.get("totals_kwh", {})
    production = summary.get("production_distribution", {})
    consumption = summary.get("consumption_distribution", {})
    if not isinstance(totals, Mapping):
        totals = {}
    if not isinstance(production, Mapping):
        production = {}
    if not isinstance(consumption, Mapping):
        consumption = {}
    values = {
        "production_energy_today_kwh": totals.get("production"),
        "consumption_energy_today_kwh": totals.get("consumption"),
        "grid_import_energy_today_kwh": totals.get("import"),
        "grid_to_home_energy_today_kwh": consumption.get(
            "consumption_from_grid_kwh", consumption.get("grid_kwh")
        ),
        "grid_export_energy_today_kwh": totals.get("export"),
        "battery_charge_energy_today_kwh": production.get(
            "production_to_battery_kwh", production.get("storage_kwh")
        ),
        "battery_discharge_energy_today_kwh": consumption.get(
            "consumption_from_battery_kwh", consumption.get("storage_kwh")
        ),
    }
    return {key: value for key, value in values.items() if value is not None}


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
        "observed_at": _provider_observed_at(flow.get("last_update_time"), time_zone),
        "site": {key: value for key, value in site.items() if value is not None},
        "completeness": {key: value is not None for key, value in site.items()},
    }
