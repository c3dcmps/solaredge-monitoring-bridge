"""HA actions, durable storage and recovery for temporary battery charging."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, tzinfo
from typing import Any

import voluptuous as vol
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from custom_components.solaredge_one_bridge.battery_charge import (
    BatteryChargeError,
    TemporaryBatteryCharge,
)
from custom_components.solaredge_one_bridge.battery_configuration_sensor import (
    SolarEdgeBatteryConfigurationCoordinator,
)
from custom_components.solaredge_one_bridge.const import CONF_SITE_ID, DOMAIN
from custom_components.solaredge_one_bridge.portal_client import (
    SolarEdgePortalSnapshotClient,
)

SCHEDULE_SCHEMA = vol.Schema({vol.Required("start"): str, vol.Required("end"): str})
CANCEL_SCHEMA = vol.Schema({})
ISSUE_MESSAGES = {
    "write_uncertain": "The temporary battery schedule could not be "
    "confirmed. Do not repeat "
    "the request. Check mySolarEdge or cancel the temporary charge.",
    "cleanup_pending": "The temporary battery window could not be removed. "
    "Cleanup will retry "
    "every minute while the integration runs. It can repeat daily until removed.",
    "conflict": "Battery settings changed in mySolarEdge. Automatic removal "
    "was refused to "
    "preserve your changes. Inspect and remove the temporary window in the app. "
    "Then retry cancellation to confirm removal without changing your other settings.",
    "invalid_record": "The saved temporary battery operation cannot be "
    "validated for this site "
    "and timezone. No command was sent. Inspect mySolarEdge and the "
    "integration configuration.",
}


def _runtime(hass: HomeAssistant) -> BatteryChargeRuntime:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state == ConfigEntryState.LOADED:
            manager = entry.runtime_data.battery_charge
            if manager is not None:
                return manager
    raise HomeAssistantError("A loaded SolarEdge portal integration is required")


def async_register_battery_charge_services(hass: HomeAssistant) -> None:
    """Register actions once; resolve only a loaded portal entry at call time."""
    if hass.services.has_service(DOMAIN, "schedule_battery_charge"):
        return

    async def schedule(call: ServiceCall) -> None:
        start = dt_util.parse_datetime(call.data["start"])
        end = dt_util.parse_datetime(call.data["end"])
        if start is None or end is None:
            raise HomeAssistantError(
                "Use ISO start/end datetimes with timezone offsets"
            )
        await _runtime(hass).schedule(start=start, end=end)

    async def cancel(call: ServiceCall) -> None:
        await _runtime(hass).cancel()

    hass.services.async_register(
        DOMAIN, "schedule_battery_charge", schedule, schema=SCHEDULE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "cancel_battery_charge", cancel, schema=CANCEL_SCHEMA
    )


class BatteryChargeRuntime:
    """Bind one manager to HA persistence, notification and unload lifecycle."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: SolarEdgePortalSnapshotClient,
        *,
        time_zone: tzinfo,
        coordinator: SolarEdgeBatteryConfigurationCoordinator,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._coordinator = coordinator
        self._store = self._new_store()
        self._site = hashlib.sha256(str(entry.data[CONF_SITE_ID]).encode()).hexdigest()
        self.manager = TemporaryBatteryCharge(
            client, save=self._save, time_zone=time_zone
        )
        self._remove_timer = lambda: None
        self._last_issue: str | None = ""
        self._stopped = False
        self._lock = asyncio.Lock()

    def _new_store(self) -> Store[dict[str, Any]]:
        return Store(
            self._hass,
            1,
            f"{DOMAIN}.{self._entry.entry_id}.temporary_battery_charge",
            private=True,
            atomic_writes=True,
        )

    async def _save(self, operation: dict[str, Any] | None) -> None:
        envelope = {"site_fingerprint": self._site, "operation": operation}
        try:
            await self._store.async_save(envelope)
            # Store can log write failures; confirm disk state independently.
            if await self._new_store().async_load() != envelope:
                raise OSError("Temporary battery operation was not persisted")
        except HomeAssistantError:
            raise OSError("Temporary battery operation persistence failed") from None

    async def async_start(self) -> None:
        """Restore ownership and retry overdue removal before starting the timer."""
        try:
            saved = await self._store.async_load()
        except (HomeAssistantError, OSError, ValueError):
            saved = {}
        if saved is not None:
            valid = (
                isinstance(saved, dict)
                and saved.get("site_fingerprint") == self._site
                and set(saved) == {"site_fingerprint", "operation"}
            )
            self.manager.restore(saved["operation"] if valid else {})
        await self.manager.recover()
        self._notify()
        self._remove_timer = async_track_time_interval(
            self._hass, self._tick, timedelta(minutes=1)
        )
        self._entry.async_on_unload(self._remove_timer)

    async def _tick(self, now: datetime) -> None:
        async with self._lock:
            if self._stopped:
                return
            had_pending = self.manager.pending is not None
            await self.manager.recover()
            self._notify()
            if had_pending and self.manager.pending is None:
                await self._coordinator.async_request_refresh()

    async def schedule(self, *, start: datetime, end: datetime) -> None:
        """Schedule an interval and refresh the settings."""
        await self._execute("schedule", start=start, end=end)

    async def cancel(self) -> None:
        """Remove the owned calendar addition, independently of the native program."""
        await self._execute("cancel")

    async def _execute(self, action: str, **kwargs: Any) -> None:
        async with self._lock:
            if self._stopped:
                raise HomeAssistantError(
                    "SolarEdge integration is unloading; try after reload"
                )
            try:
                if action == "schedule":
                    await self.manager.schedule(**kwargs)
                else:
                    await self.manager.cancel()
            except BatteryChargeError as err:
                raise HomeAssistantError(str(err)) from None
            finally:
                self._notify()
                await self._coordinator.async_request_refresh()

    def _notify(self) -> None:
        issue = self.manager.issue
        if issue == self._last_issue:
            return
        notification_id = f"{DOMAIN}_{self._entry.entry_id}_temporary_charge"
        if issue:
            persistent_notification.async_create(
                self._hass,
                ISSUE_MESSAGES[issue],
                title="SolarEdge temporary battery charge",
                notification_id=notification_id,
            )
        else:
            persistent_notification.async_dismiss(self._hass, notification_id)
        self._last_issue = issue

    async def async_stop(self) -> None:
        """Drain active operations on unload and retain durable intent."""
        self._stopped = True
        self._remove_timer()
        async with self._lock:
            if self.manager.pending is not None:
                persistent_notification.async_create(
                    self._hass,
                    "SolarEdge integration unloaded with a temporary battery window. "
                    "It can repeat daily; reload the integration or remove "
                    "it in mySolarEdge.",
                    title="SolarEdge temporary battery charge",
                    notification_id=f"{DOMAIN}_{self._entry.entry_id}_temporary_charge",
                )
