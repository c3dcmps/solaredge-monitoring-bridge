"""Poll EV devices independently of aggregate site telemetry."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from custom_components.solaredge_one_bridge.const import DOMAIN
from custom_components.solaredge_one_bridge.ev_charger import EVChargerSnapshot
from custom_components.solaredge_one_bridge.portal import SolarEdgePortalError
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
        self.data = {}

    async def _async_update_data(self) -> dict[str, EVChargerSnapshot]:
        try:
            return await self._client.async_get_ev_chargers()
        except (SolarEdgePortalError, ValueError) as err:
            raise UpdateFailed("SolarEdge EV charger data is unavailable") from err
