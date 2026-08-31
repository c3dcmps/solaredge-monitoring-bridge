"""SolarEdge Monitoring Bridge integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import SolarEdgeBridgeClient
from .const import (
    CONF_ENDPOINT,
    CONF_MAX_SNAPSHOT_AGE,
    CONF_POLL_INTERVAL,
    CONF_REQUEST_TIMEOUT,
    CONF_SHARED_SECRET,
    DEFAULT_MAX_SNAPSHOT_AGE_SECONDS,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
)
from .coordinator import SolarEdgeBridgeCoordinator

PLATFORMS = [Platform.SENSOR]

SolarEdgeBridgeConfigEntry = ConfigEntry[SolarEdgeBridgeCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: SolarEdgeBridgeConfigEntry
) -> bool:
    """Set up a local bridge config entry."""
    try:
        client = SolarEdgeBridgeClient(
            async_get_clientsession(hass),
            entry.data[CONF_ENDPOINT],
            entry.data[CONF_SHARED_SECRET],
            timeout_seconds=int(
                entry.options.get(
                    CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT_SECONDS
                )
            ),
            max_snapshot_age_seconds=int(
                entry.options.get(
                    CONF_MAX_SNAPSHOT_AGE, DEFAULT_MAX_SNAPSHOT_AGE_SECONDS
                )
            ),
        )
    except ValueError as err:
        raise ConfigEntryNotReady("bridge configuration is invalid") from err

    coordinator = SolarEdgeBridgeCoordinator(
        hass,
        entry,
        client,
        update_interval=timedelta(
            seconds=int(
                entry.options.get(
                    CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL_SECONDS
                )
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
