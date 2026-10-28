"""Discover read-only EV sensors without exposing provider identifiers."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.solaredge_one_bridge import SolarEdgeBridgeConfigEntry
from custom_components.solaredge_one_bridge.const import DOMAIN
from custom_components.solaredge_one_bridge.ev_coordinator import SolarEdgeEVCoordinator

SENSOR_DESCRIPTIONS = (
    SensorEntityDescription(key="status", translation_key="ev_charger_status"),
    SensorEntityDescription(key="activation_mode", translation_key="ev_charger_mode"),
    SensorEntityDescription(
        key="power_w",
        translation_key="ev_charger_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
)


async def async_setup_ev_sensors(
    hass: HomeAssistant,
    entry: SolarEdgeBridgeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Discover EV devices now and after later successful polls."""
    site = entry.runtime_data
    client = site.portal_client
    if client is None:
        return
    coordinator = SolarEdgeEVCoordinator(
        hass, entry, client, update_interval=site.update_interval
    )
    site.ev_coordinator = coordinator
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
                    SolarEdgeEVSensor(coordinator, key, description)
                    for key in sorted(new_keys)
                    for description in SENSOR_DESCRIPTIONS
                ]
            )

    await coordinator.async_refresh()
    entry.async_on_unload(coordinator.async_add_listener(discover))
    discover()


class SolarEdgeEVSensor(CoordinatorEntity[SolarEdgeEVCoordinator], SensorEntity):
    """One allowlisted status, mode or power field of one EV charger."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolarEdgeEVCoordinator,
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
            via_device=(DOMAIN, "bridge"),
        )

    @property
    def available(self) -> bool:
        """Make missing devices, missing fields and failed polls unavailable."""
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> str | float | None:
        """Return the current value, never an inferred charging permission."""
        charger = self.coordinator.data.get(self._key)
        return getattr(charger, self.entity_description.key) if charger else None
