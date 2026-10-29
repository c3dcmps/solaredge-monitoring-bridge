"""Sensor platform for SolarEdge Monitoring Bridge."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.solaredge_one_bridge.const import (
    CONF_MODE,
    CONF_TIME_ZONE,
    MODE_PORTAL,
)
from custom_components.solaredge_one_bridge.ev_schedule_sensor import (
    async_setup_ev_schedule_sensors,
)
from custom_components.solaredge_one_bridge.ev_sensor import async_setup_ev_sensors
from custom_components.solaredge_one_bridge.model import DAILY_ENERGY_KEYS

from . import SolarEdgeBridgeConfigEntry
from .const import DOMAIN
from .coordinator import SolarEdgeBridgeCoordinator

DEVICE_INFO = DeviceInfo(
    identifiers={(DOMAIN, "bridge")},
    manufacturer="SolarEdge",
    model="Monitoring data source",
    name="SolarEdge Monitoring",
)


@dataclass(frozen=True, kw_only=True)
class SolarEdgeBridgeSensorDescription(SensorEntityDescription):
    """Describe one sanitized bridge sensor."""


SENSOR_DESCRIPTIONS: tuple[SolarEdgeBridgeSensorDescription, ...] = (
    *(
        SolarEdgeBridgeSensorDescription(
            key=key,
            translation_key=key,
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.WATT,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=0,
        )
        for key in (
            "production_power_w",
            "consumption_power_w",
            "grid_import_power_w",
            "grid_export_power_w",
            "battery_charge_power_w",
            "battery_discharge_power_w",
        )
    ),
    SolarEdgeBridgeSensorDescription(
        key="battery_state_of_energy_pct",
        translation_key="battery_state_of_energy_pct",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    *(
        SolarEdgeBridgeSensorDescription(
            key=key,
            translation_key=key,
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            state_class=SensorStateClass.TOTAL_INCREASING,
            suggested_display_precision=3,
        )
        for key in (
            "production_energy_kwh",
            "consumption_energy_kwh",
            "grid_import_energy_kwh",
            "grid_export_energy_kwh",
            "battery_charge_energy_kwh",
            "battery_discharge_energy_kwh",
        )
    ),
)

DAILY_SENSOR_DESCRIPTIONS = tuple(
    SolarEdgeBridgeSensorDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=3,
    )
    for key in sorted(DAILY_ENERGY_KEYS)
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarEdgeBridgeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up bridge sensors."""
    async_add_entities(
        [
            *(
                SolarEdgeBridgeSensor(entry.runtime_data, description)
                for description in SENSOR_DESCRIPTIONS
            ),
            SolarEdgeStorageOperatingPlanSensor(entry.runtime_data),
        ]
    )
    if entry.data.get(CONF_MODE) == MODE_PORTAL:
        time_zone = ZoneInfo(entry.data[CONF_TIME_ZONE])
        async_add_entities(
            [
                SolarEdgeBridgeSensor(
                    entry.runtime_data, description, time_zone=time_zone
                )
                for description in DAILY_SENSOR_DESCRIPTIONS
            ]
        )
    await async_setup_ev_sensors(hass, entry, async_add_entities)
    await async_setup_ev_schedule_sensors(hass, entry, async_add_entities)


def _diagnostic_attributes(
    coordinator: SolarEdgeBridgeCoordinator,
) -> dict[str, str | int]:
    """Return concise, non-identifying protocol diagnostics."""
    attributes: dict[str, str | int] = {
        "schema_version": coordinator.data.schema_version
    }
    if coordinator.data.observed_at_iso is not None:
        attributes["observed_at"] = coordinator.data.observed_at_iso
    if coordinator.data.provider is not None:
        attributes["provider"] = coordinator.data.provider
    return attributes


class SolarEdgeBridgeSensor(
    CoordinatorEntity[SolarEdgeBridgeCoordinator], SensorEntity
):
    """One allowlisted aggregate SolarEdge metric."""

    _attr_has_entity_name = True
    _attr_device_info = DEVICE_INFO

    def __init__(
        self,
        coordinator: SolarEdgeBridgeCoordinator,
        description: SolarEdgeBridgeSensorDescription,
        *,
        time_zone: tzinfo | None = None,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{description.key}"
        self._time_zone = time_zone

    @property
    def _daily_date_matches(self) -> bool:
        if self.entity_description.key not in DAILY_ENERGY_KEYS:
            return True
        return (
            self._time_zone is not None
            and self.coordinator.data.daily_energy_date
            == datetime.now(self._time_zone).date()
        )

    @property
    def last_reset(self) -> datetime | None:
        """Identify each daily cycle by its local midnight, expressed in UTC."""
        selected = self.coordinator.data.daily_energy_date
        if (
            self.entity_description.key not in DAILY_ENERGY_KEYS
            or selected is None
            or self._time_zone is None
        ):
            return None
        return datetime.combine(
            selected, datetime.min.time(), self._time_zone
        ).astimezone(UTC)

    @property
    def available(self) -> bool:
        """Mark absent, incomplete, disconnected, and failed values unavailable."""
        return (
            super().available
            and self.coordinator.data.connected
            and self.coordinator.data.value(self.entity_description.key) is not None
            and self._daily_date_matches
        )

    @property
    def native_value(self) -> float | None:
        """Return the sanitized metric value."""
        if not self.coordinator.data.connected or not self._daily_date_matches:
            return None
        return self.coordinator.data.value(self.entity_description.key)

    @property
    def extra_state_attributes(self) -> dict[str, str | int | bool]:
        """Expose only bounded protocol diagnostics."""
        attributes: dict[str, str | int | bool] = _diagnostic_attributes(
            self.coordinator
        )
        if (
            self.entity_description.key in DAILY_ENERGY_KEYS
            and self.coordinator.data.daily_energy_date is not None
        ):
            attributes["date"] = self.coordinator.data.daily_energy_date.isoformat()
        completeness = self.coordinator.data.completeness.get(
            self.entity_description.key
        )
        if completeness is not None:
            attributes["complete"] = completeness
        return attributes


class SolarEdgeStorageOperatingPlanSensor(
    CoordinatorEntity[SolarEdgeBridgeCoordinator], SensorEntity
):
    """Provider-reported storage operating-plan label."""

    _attr_has_entity_name = True
    _attr_translation_key = "storage_operating_plan"
    _attr_unique_id = f"{DOMAIN}_storage_operating_plan"
    _attr_device_info = DEVICE_INFO

    @property
    def available(self) -> bool:
        """Return whether a current, complete plan label is available."""
        return (
            super().available
            and self.coordinator.data.connected
            and self.coordinator.data.storage_operating_plan is not None
            and self.coordinator.data.completeness.get("storage_operating_plan")
            is not False
        )

    @property
    def native_value(self) -> str | None:
        """Return the sanitized plan label."""
        if not self.coordinator.data.connected:
            return None
        return self.coordinator.data.storage_operating_plan

    @property
    def extra_state_attributes(self) -> dict[str, str | int | bool]:
        """Expose bounded plan status, never a raw policy payload."""
        attributes: dict[str, str | int | bool] = _diagnostic_attributes(
            self.coordinator
        )
        if self.coordinator.data.storage_operating_plan_active is not None:
            attributes["active"] = self.coordinator.data.storage_operating_plan_active
        if self.coordinator.data.storage_operating_plan_block_count is not None:
            attributes["block_count"] = (
                self.coordinator.data.storage_operating_plan_block_count
            )
        completeness = self.coordinator.data.completeness.get("storage_operating_plan")
        if completeness is not None:
            attributes["complete"] = completeness
        return attributes
