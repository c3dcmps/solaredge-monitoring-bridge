"""Coordinator for the local SolarEdge snapshot bridge."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Protocol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from custom_components.solaredge_one_bridge.ev_coordinator import SolarEdgeEVCoordinator
from custom_components.solaredge_one_bridge.portal_client import (
    SolarEdgePortalSnapshotClient,
)

from .client import (
    BridgeAuthenticationError,
    BridgeError,
)
from .const import DEFAULT_UPDATE_INTERVAL, DOMAIN
from .model import SolarEdgeSnapshot
from .portal import SolarEdgePortalAuthenticationError, SolarEdgePortalError

_LOGGER = logging.getLogger(__name__)


class SnapshotClient(Protocol):
    """Common boundary implemented by portal and local bridge clients."""

    async def async_get_snapshot(self) -> SolarEdgeSnapshot:
        """Return a validated snapshot."""


class SolarEdgeBridgeCoordinator(DataUpdateCoordinator[SolarEdgeSnapshot]):
    """Poll the bridge without reloading Home Assistant on transient outages."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: SnapshotClient,
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
        self.ev_coordinator: SolarEdgeEVCoordinator | None = None

    @property
    def portal_client(self) -> SolarEdgePortalSnapshotClient | None:
        """Return the portal source only when EV discovery is supported."""
        return (
            self._client
            if isinstance(self._client, SolarEdgePortalSnapshotClient)
            else None
        )

    async def _async_update_data(self) -> SolarEdgeSnapshot:
        try:
            return await self._client.async_get_snapshot()
        except (BridgeAuthenticationError, SolarEdgePortalAuthenticationError) as err:
            raise ConfigEntryAuthFailed("SolarEdge authentication failed") from err
        except (BridgeError, SolarEdgePortalError) as err:
            raise UpdateFailed(str(err)) from err
