"""Privacy-preserving diagnostics for SolarEdge Monitoring Bridge."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import SolarEdgeBridgeConfigEntry
from .const import CONF_ENDPOINT, CONF_SHARED_SECRET

TO_REDACT = {CONF_ENDPOINT, CONF_SHARED_SECRET, "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SolarEdgeBridgeConfigEntry
) -> dict[str, Any]:
    """Return structure and health without raw metric values or identifiers."""
    coordinator = entry.runtime_data
    snapshot = coordinator.data
    return {
        "config_entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "bridge": {
            "last_update_success": coordinator.last_update_success,
            "schema_version": snapshot.schema_version,
            "connected": snapshot.connected,
            "observed_at": snapshot.observed_at_iso,
            "snapshot_age_seconds": snapshot.age_seconds(),
            "provider": snapshot.provider,
            "available_metrics": sorted(snapshot.site),
            "completeness": dict(snapshot.completeness),
            "operating_plan_available": snapshot.storage_operating_plan is not None,
        },
    }

