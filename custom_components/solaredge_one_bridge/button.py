"""Manual SolarEdge EV charging commands through the authenticated portal."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.solaredge_one_bridge import SolarEdgeBridgeConfigEntry
from custom_components.solaredge_one_bridge.const import DOMAIN
from custom_components.solaredge_one_bridge.ev_coordinator import SolarEdgeEVCoordinator


@dataclass(frozen=True, kw_only=True)
class EVChargingButtonDescription(ButtonEntityDescription):
    """Describe a fixed manual charging action."""

    charging: bool


BUTTON_DESCRIPTIONS = (
    EVChargingButtonDescription(
        key="charge_now",
        translation_key="ev_charge_now",
        icon="mdi:ev-station",
        charging=True,
    ),
    EVChargingButtonDescription(
        key="stop_charging",
        translation_key="ev_stop_charging",
        icon="mdi:stop-circle-outline",
        charging=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarEdgeBridgeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add two buttons to known and subsequently discovered portal chargers."""
    coordinator = entry.runtime_data.ev_coordinator
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
                    SolarEdgeEVButton(coordinator, key, description)
                    for key in sorted(new_keys)
                    for description in BUTTON_DESCRIPTIONS
                ]
            )

    entry.async_on_unload(coordinator.async_add_listener(discover))
    discover()


class SolarEdgeEVButton(CoordinatorEntity[SolarEdgeEVCoordinator], ButtonEntity):
    """One specific command for a currently discovered SolarEdge charger."""

    _attr_has_entity_name = True
    entity_description: EVChargingButtonDescription

    def __init__(
        self,
        coordinator: SolarEdgeEVCoordinator,
        key: str,
        description: EVChargingButtonDescription,
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
        """Disable controls when current discovery is unavailable or the device left."""
        return super().available and self._key in self.coordinator.data

    async def async_press(self) -> None:
        """Submit this command and request an update of the observed state."""
        await self.coordinator.async_set_charging(
            self._key, charging=self.entity_description.charging
        )
