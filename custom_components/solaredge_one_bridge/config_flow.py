"""Config flow for SolarEdge Monitoring Bridge."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import (
    BridgeAuthenticationError,
    BridgeConnectionError,
    SolarEdgeBridgeClient,
    is_local_endpoint,
)
from .const import (
    CONF_ENDPOINT,
    CONF_MAX_SNAPSHOT_AGE,
    CONF_POLL_INTERVAL,
    CONF_REQUEST_TIMEOUT,
    CONF_SHARED_SECRET,
    DEFAULT_ENDPOINT,
    DEFAULT_MAX_SNAPSHOT_AGE_SECONDS,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DOMAIN,
    MAX_MAX_SNAPSHOT_AGE_SECONDS,
    MAX_POLL_INTERVAL_SECONDS,
    MAX_REQUEST_TIMEOUT_SECONDS,
    MIN_MAX_SNAPSHOT_AGE_SECONDS,
    MIN_POLL_INTERVAL_SECONDS,
    MIN_REQUEST_TIMEOUT_SECONDS,
)


async def _async_validate_bridge(
    hass: HomeAssistant, endpoint: str, shared_secret: str
) -> str | None:
    """Validate credentials and return the sanitized provider label."""
    client = SolarEdgeBridgeClient(
        async_get_clientsession(hass), endpoint, shared_secret
    )
    snapshot = await client.async_get_snapshot()
    return snapshot.provider


def _connection_schema(
    defaults: dict[str, Any] | None = None,
    *,
    secret_required: bool = True,
) -> vol.Schema:
    values = defaults or {}
    secret_marker = vol.Required if secret_required else vol.Optional
    return vol.Schema(
        {
            vol.Required(
                CONF_ENDPOINT, default=values.get(CONF_ENDPOINT, DEFAULT_ENDPOINT)
            ): vol.All(str, vol.Length(min=1, max=2048)),
            secret_marker(CONF_SHARED_SECRET): vol.All(
                str, vol.Length(min=16, max=512)
            ),
        }
    )


class SolarEdgeMonitoringBridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure one local, read-only SolarEdge snapshot bridge."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> BridgeOptionsFlow:
        """Return the options flow."""
        return BridgeOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle initial setup."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}
        if user_input is not None:
            endpoint = user_input[CONF_ENDPOINT].strip()
            secret = user_input[CONF_SHARED_SECRET]
            if not is_local_endpoint(endpoint):
                errors["base"] = "invalid_endpoint"
            else:
                try:
                    await _async_validate_bridge(self.hass, endpoint, secret)
                except BridgeAuthenticationError:
                    errors["base"] = "invalid_auth"
                except (BridgeConnectionError, ValueError):
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_create_entry(
                        title="SolarEdge Monitoring Bridge",
                        data={CONF_ENDPOINT: endpoint, CONF_SHARED_SECRET: secret},
                    )
        return self.async_show_form(
            step_id="user",
            data_schema=_connection_schema(user_input),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after an HTTP 401 or 403."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Replace only the rejected shared secret."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            secret = user_input[CONF_SHARED_SECRET]
            try:
                await _async_validate_bridge(
                    self.hass, entry.data[CONF_ENDPOINT], secret
                )
            except BridgeAuthenticationError:
                errors["base"] = "invalid_auth"
            except (BridgeConnectionError, ValueError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_SHARED_SECRET: secret},
                    reason="reauth_successful",
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SHARED_SECRET): vol.All(
                        str, vol.Length(min=16, max=512)
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the endpoint or rotate its shared secret."""
        entry = self._get_reconfigure_entry()
        defaults = {CONF_ENDPOINT: entry.data[CONF_ENDPOINT]}
        errors: dict[str, str] = {}
        if user_input is not None:
            endpoint = user_input[CONF_ENDPOINT].strip()
            secret = user_input[CONF_SHARED_SECRET]
            if not is_local_endpoint(endpoint):
                errors["base"] = "invalid_endpoint"
            else:
                try:
                    await _async_validate_bridge(self.hass, endpoint, secret)
                except BridgeAuthenticationError:
                    errors["base"] = "invalid_auth"
                except (BridgeConnectionError, ValueError):
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_ENDPOINT: endpoint,
                            CONF_SHARED_SECRET: secret,
                        },
                        reason="reconfigure_successful",
                    )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_connection_schema(user_input or defaults),
            errors=errors,
        )


class BridgeOptionsFlow(config_entries.OptionsFlow):
    """Configure polling, timeout, and freshness limits."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage bridge polling options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=options.get(
                            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL_SECONDS
                        ),
                    ): vol.All(
                        int,
                        vol.Range(
                            min=MIN_POLL_INTERVAL_SECONDS,
                            max=MAX_POLL_INTERVAL_SECONDS,
                        ),
                    ),
                    vol.Required(
                        CONF_MAX_SNAPSHOT_AGE,
                        default=options.get(
                            CONF_MAX_SNAPSHOT_AGE,
                            DEFAULT_MAX_SNAPSHOT_AGE_SECONDS,
                        ),
                    ): vol.All(
                        int,
                        vol.Range(
                            min=MIN_MAX_SNAPSHOT_AGE_SECONDS,
                            max=MAX_MAX_SNAPSHOT_AGE_SECONDS,
                        ),
                    ),
                    vol.Required(
                        CONF_REQUEST_TIMEOUT,
                        default=options.get(
                            CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT_SECONDS
                        ),
                    ): vol.All(
                        int,
                        vol.Range(
                            min=MIN_REQUEST_TIMEOUT_SECONDS,
                            max=MAX_REQUEST_TIMEOUT_SECONDS,
                        ),
                    ),
                }
            ),
        )

