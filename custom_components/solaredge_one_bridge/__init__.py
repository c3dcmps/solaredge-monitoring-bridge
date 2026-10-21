"""SolarEdge Monitoring Bridge integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
    async_get_clientsession,
)
from homeassistant.util import dt as dt_util

from .client import SolarEdgeBridgeClient
from .const import (
    CONF_ENDPOINT,
    CONF_MAX_SNAPSHOT_AGE,
    CONF_MODE,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_REQUEST_TIMEOUT,
    CONF_SHARED_SECRET,
    CONF_SITE_ID,
    CONF_TIME_ZONE,
    CONF_USERNAME,
    DEFAULT_MAX_SNAPSHOT_AGE_SECONDS,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    MODE_BRIDGE,
    MODE_PORTAL,
)
from .coordinator import SolarEdgeBridgeCoordinator
from .portal_client import SolarEdgePortalSnapshotClient

PLATFORMS = [Platform.SENSOR]

SolarEdgeBridgeConfigEntry = ConfigEntry[SolarEdgeBridgeCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: SolarEdgeBridgeConfigEntry
) -> bool:
    """Set up an unofficial portal or local bridge config entry."""
    try:
        timeout_seconds = int(
            entry.options.get(CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT_SECONDS)
        )
        max_snapshot_age_seconds = int(
            entry.options.get(CONF_MAX_SNAPSHOT_AGE, DEFAULT_MAX_SNAPSHOT_AGE_SECONDS)
        )
        mode = entry.data.get(CONF_MODE, MODE_BRIDGE)
        if mode == MODE_PORTAL:
            time_zone = await dt_util.async_get_time_zone(entry.data[CONF_TIME_ZONE])
            if time_zone is None:
                raise ValueError("SolarEdge portal timezone is invalid")
            # HA releases this entry's private cookie jar on unload or failed setup.
            session = async_create_clientsession(hass)
            client = SolarEdgePortalSnapshotClient(
                session,
                username=entry.data[CONF_USERNAME],
                password=entry.data[CONF_PASSWORD],
                site_id=entry.data[CONF_SITE_ID],
                time_zone=time_zone,
                timeout_seconds=timeout_seconds,
                max_snapshot_age_seconds=max_snapshot_age_seconds,
            )
        elif mode == MODE_BRIDGE:
            client = SolarEdgeBridgeClient(
                async_get_clientsession(hass),
                entry.data[CONF_ENDPOINT],
                entry.data[CONF_SHARED_SECRET],
                timeout_seconds=timeout_seconds,
                max_snapshot_age_seconds=max_snapshot_age_seconds,
            )
        else:
            raise ValueError("unsupported connection mode")
    except ValueError as err:
        raise ConfigEntryNotReady("SolarEdge configuration is invalid") from err

    coordinator = SolarEdgeBridgeCoordinator(
        hass,
        entry,
        client,
        update_interval=timedelta(
            seconds=int(
                entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL_SECONDS)
            )
        ),
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: SolarEdgeBridgeConfigEntry
) -> bool:
    """Unload a bridge config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(
    hass: HomeAssistant, entry: SolarEdgeBridgeConfigEntry
) -> None:
    """Reload after polling or freshness options change."""
    await hass.config_entries.async_reload(entry.entry_id)
