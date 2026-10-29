"""Five-minute EV schedule polling and read-only sensors."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from custom_components.solaredge_one_bridge.const import DOMAIN
from custom_components.solaredge_one_bridge.ev_schedule import EVScheduleSnapshot
from custom_components.solaredge_one_bridge.portal import SolarEdgePortalError
from custom_components.solaredge_one_bridge.portal_client import (
    SolarEdgePortalSnapshotClient,
)

_LOGGER = logging.getLogger(__name__)
SENSOR_DESCRIPTIONS = (
    SensorEntityDescription(
        key="schedule_count",
        translation_key="ev_charging_schedules",
        icon="mdi:calendar-clock",
    ),
    SensorEntityDescription(
        key="next_start",
        translation_key="ev_next_scheduled_charge",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
)


class SolarEdgeEVScheduleCoordinator(
    DataUpdateCoordinator[dict[str, EVScheduleSnapshot]]
):
    """Read schedule metadata independently of fast EV status and manual controls."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: SolarEdgePortalSnapshotClient,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN}_ev_schedules",
            config_entry=entry,
            update_interval=timedelta(minutes=5),
        )
        self._client = client
        self.data = {}

    async def _async_update_data(self) -> dict[str, EVScheduleSnapshot]:
        try:
            return await self._client.async_get_ev_schedules()
        except (SolarEdgePortalError, ValueError) as err:
            raise UpdateFailed("SolarEdge EV schedule data is unavailable") from err


async def async_setup_ev_schedule_sensors(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Discover read-only schedule sensors, including devices arriving later."""
    coordinator = entry.runtime_data.ev_schedule_coordinator
    if coordinator is None:
        return
    known: set[str] = set()

    @callback
    def discover() -> None:
        if not coordinator.last_update_success:
            return
        new_keys = set(coordinator.data) - known
        known.update(new_keys)
        if new_keys:
            async_add_entities(
                [
                    SolarEdgeEVScheduleSensor(coordinator, key, description)
                    for key in sorted(new_keys)
                    for description in SENSOR_DESCRIPTIONS
                ]
            )

    entry.async_on_unload(coordinator.async_add_listener(discover))
    discover()


class SolarEdgeEVScheduleSensor(
    CoordinatorEntity[SolarEdgeEVScheduleCoordinator], SensorEntity
):
    """Configured schedule count or next interval explicitly reported by SolarEdge."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolarEdgeEVScheduleCoordinator,
        key: str,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_ev_charger_{key}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"ev_charger_{key}")},
            manufacturer="SolarEdge",
            model="EV charger (Monitoring)",
            name=f"SolarEdge EV charger {key[:8]}",
        )

    @property
    def available(self) -> bool:
        """Keep no-next-window as unknown, distinct from unavailable schedule data."""
        snapshot = self.coordinator.data.get(self._key)
        return (
            super().available and snapshot is not None and snapshot.windows is not None
        )

    @property
    def native_value(self) -> int | datetime | None:
        """Return the configured count or the validated next UTC timestamp."""
        snapshot = self.coordinator.data.get(self._key)
        return getattr(snapshot, self.entity_description.key) if snapshot else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose only typed clocks, weekdays, activation and observation metadata."""
        snapshot = self.coordinator.data.get(self._key)
        if not self.coordinator.last_update_success or snapshot is None:
            return {}
        attributes: dict[str, Any] = {
            "time_zone": snapshot.time_zone,
            "last_checked": snapshot.checked_at.isoformat(),
        }
        if self.entity_description.key == "schedule_count":
            attributes["enabled_count"] = snapshot.enabled_count
            if snapshot.windows is not None:
                attributes["schedules"] = [
                    window.as_dict() for window in snapshot.windows
                ]
            return attributes
        attributes.update(
            {
                "reason": snapshot.next_reason,
                "source": "solaredge" if snapshot.next_reason == "reported" else None,
                "end": snapshot.next_end.isoformat() if snapshot.next_end else None,
            }
        )
        return attributes
