"""Coordinator for the local SolarEdge snapshot bridge."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import (
    BridgeAuthenticationError,
    BridgeError,
    SolarEdgeBridgeClient,
)
from .const import DEFAULT_UPDATE_INTERVAL, DOMAIN
from .model import SolarEdgeSnapshot

_LOGGER = logging.getLogger(__name__)


class SolarEdgeBridgeCoordinator(DataUpdateCoordinator[SolarEdgeSnapshot]):
    """Poll the bridge without reloading Home Assistant on transient outages."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: SolarEdgeBridgeClient,
        *,
        update_interval: timedelta = DEFAULT_UPDATE_INTERVAL,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=update_interval,
        )
        self._client = client

    async def _async_update_data(self) -> SolarEdgeSnapshot:
        try:
            return await self._client.async_get_snapshot()
        except BridgeAuthenticationError as err:
            raise ConfigEntryAuthFailed("bridge authentication failed") from err
        except BridgeError as err:
            raise UpdateFailed(str(err)) from err

