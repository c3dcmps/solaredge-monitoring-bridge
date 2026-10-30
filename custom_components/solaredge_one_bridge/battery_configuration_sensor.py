"""Read-only battery configuration sensors with independent five-minute polling."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from custom_components.solaredge_one_bridge.battery_configuration import (
    BatteryConfigurationSnapshot,
)
from custom_components.solaredge_one_bridge.const import DOMAIN
from custom_components.solaredge_one_bridge.portal import SolarEdgePortalError
from custom_components.solaredge_one_bridge.portal_client import (
    SolarEdgePortalSnapshotClient,
)

_LOGGER = logging.getLogger(__name__)
DEVICE_INFO = DeviceInfo(identifiers={(DOMAIN, "bridge")})
SENSOR_DESCRIPTIONS = (
    SensorEntityDescription(
        key="mode",
        translation_key="battery_operating_mode",
        icon="mdi:battery-cog",
    ),
    SensorEntityDescription(
        key="reserve_pct",
        translation_key="battery_backup_reserve",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:battery-lock",
    ),
    SensorEntityDescription(
        key="manual_count",
        translation_key="battery_manual_schedules",
        icon="mdi:calendar-clock",
    ),
    SensorEntityDescription(
        key="tariff_count",
        translation_key="battery_tariff_schedules",
        icon="mdi:calendar-clock",
    ),
)


class SolarEdgeBatteryConfigurationCoordinator(
    DataUpdateCoordinator[BatteryConfigurationSnapshot | None]
):
    """Read settings independently of fast power telemetry and EV controls."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: SolarEdgePortalSnapshotClient,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN}_battery_configuration",
            config_entry=entry,
            update_interval=timedelta(minutes=5),
        )
        self._client = client
        self.data = None

    async def _async_update_data(self) -> BatteryConfigurationSnapshot:
        try:
            snapshot = await self._client.async_get_battery_configuration()
        except (SolarEdgePortalError, ValueError) as err:
            raise UpdateFailed(
                "SolarEdge battery configuration is unavailable"
            ) from err
        if all(
            getattr(snapshot, key) is None
            for key in ("mode", "reserve_pct", "manual_count", "tariff_count")
        ):
            raise UpdateFailed("SolarEdge battery configuration is unavailable")
        return snapshot


async def async_setup_battery_configuration_sensors(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create four read-only sensors only for the portal connection mode."""
    coordinator = entry.runtime_data.battery_configuration_coordinator
    if coordinator is not None:
        async_add_entities(
            [
                SolarEdgeBatteryConfigurationSensor(
                    coordinator, entry.entry_id, description
                )
                for description in SENSOR_DESCRIPTIONS
            ]
        )


class SolarEdgeBatteryConfigurationSensor(
    CoordinatorEntity[SolarEdgeBatteryConfigurationCoordinator], SensorEntity
):
    """A configured setpoint or program, never an inferred physical state."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolarEdgeBatteryConfigurationCoordinator,
        entry_id: str,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_battery_{description.key}"
        self._attr_device_info = DEVICE_INFO

    @property
    def available(self) -> bool:
        """Scope availability to the endpoint and specific validated field."""
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> str | float | int | None:
        """Return no stale setting when a read or field validation has failed."""
        snapshot = self.coordinator.data
        if not self.coordinator.last_update_success or snapshot is None:
            return None
        return getattr(snapshot, self.entity_description.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose typed calendars, retrieval time and explicit backup metadata."""
        snapshot = self.coordinator.data
        if not self.available or snapshot is None:
            return {}
        attributes: dict[str, Any] = {
            "last_checked": snapshot.checked_at.isoformat(),
            "time_zone": snapshot.time_zone,
        }
        key = self.entity_description.key
        if key == "reserve_pct":
            attributes.update(self._reserve_attributes(snapshot))
        else:
            attributes["configuration_updated_at"] = (
                snapshot.updated_at.isoformat() if snapshot.updated_at else None
            )
        if key in {"manual_count", "tariff_count"}:
            attributes.update(self._program_attributes(snapshot))
        return attributes

    def _reserve_attributes(
        self, snapshot: BatteryConfigurationSnapshot
    ) -> dict[str, Any]:
        reserve = snapshot.reserve
        if reserve is None:
            return {}
        return {
            "minimum_reserve_pct": reserve.minimum_pct,
            "view_capable": reserve.view_capable,
            "edit_capable": reserve.edit_capable,
            "weather_guard_configuration": reserve.weather_guard_configuration,
            "weather_guard_status": reserve.weather_guard_status,
        }

    def _program_attributes(
        self, snapshot: BatteryConfigurationSnapshot
    ) -> dict[str, Any]:
        manual = self.entity_description.key == "manual_count"
        windows = snapshot.manual_windows if manual else snapshot.tariff_windows
        selected = None
        if snapshot.mode in {"MANUAL_TOU", "MSC"}:
            selected = manual and snapshot.mode == "MANUAL_TOU"
        return {
            "program_type": "manual" if manual else "tariff",
            "selected_mode": snapshot.mode,
            "selected": selected,
            "schedules": [window.as_dict() for window in windows or ()],
        }
