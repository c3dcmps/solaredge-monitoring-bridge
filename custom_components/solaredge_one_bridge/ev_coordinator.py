"""Poll EV devices independently of aggregate site telemetry."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from custom_components.solaredge_one_bridge.const import DOMAIN
from custom_components.solaredge_one_bridge.ev_charger import EVChargerSnapshot
from custom_components.solaredge_one_bridge.portal import (
    SolarEdgeEVCommandError,
    SolarEdgePortalError,
)
from custom_components.solaredge_one_bridge.portal_client import (
    SolarEdgePortalSnapshotClient,
)

_LOGGER = logging.getLogger(__name__)


class SolarEdgeEVCoordinator(DataUpdateCoordinator[dict[str, EVChargerSnapshot]]):
    """Keep EV failures local, including accounts without EV endpoint permission."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: SolarEdgePortalSnapshotClient,
        *,
        update_interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN}_ev_chargers",
            config_entry=entry,
            update_interval=update_interval,
        )
        self._client = client
        self._command_lock = asyncio.Lock()
        self.data = {}

    async def async_set_charging(self, key: str, *, charging: bool) -> None:
        """Serialize manual requests and refresh provider-reported state afterward.

        Args:
            key: Opaque identity of a currently available charger.
            charging: True for Charge now, False for Stop charging.

        Raises:
            HomeAssistantError: If the charger is unavailable or the request fails.
        """
        async with self._command_lock:
            if not self.last_update_success or key not in self.data:
                raise HomeAssistantError(
                    "SolarEdge EV charger is unavailable; wait for a successful refresh"
                )
            try:
                await self._client.async_set_ev_charging(key, charging=charging)
            except (SolarEdgePortalError, ValueError) as err:
                await self.async_request_refresh()
                if isinstance(err, SolarEdgeEVCommandError):
                    raise HomeAssistantError(
                        f"SolarEdge could not confirm the EV command: {err}"
                    ) from err
                raise HomeAssistantError(
                    "SolarEdge could not confirm the EV command; "
                    "check the charger state before retrying"
                ) from err
            await self.async_request_refresh()

    async def _async_update_data(self) -> dict[str, EVChargerSnapshot]:
        try:
            return await self._client.async_get_ev_chargers()
        except (SolarEdgePortalError, ValueError) as err:
            raise UpdateFailed("SolarEdge EV charger data is unavailable") from err
