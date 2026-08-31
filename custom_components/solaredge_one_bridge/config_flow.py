"""Config flow for SolarEdge Monitoring."""

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
    CONF_MODE,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_REQUEST_TIMEOUT,
    CONF_SHARED_SECRET,
    CONF_SITE_ID,
    CONF_TIME_ZONE,
    CONF_USERNAME,
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
    MODE_BRIDGE,
    MODE_PORTAL,
)
from .portal import (
    SolarEdgePortalAuthenticationError,
    SolarEdgePortalClient,
    SolarEdgePortalConfig,
    SolarEdgePortalError,
)


async def _async_validate_bridge(
    hass: HomeAssistant, endpoint: str, shared_secret: str
) -> str | None:
    """Validate bridge credentials and return the sanitized provider label."""
    client = SolarEdgeBridgeClient(
        async_get_clientsession(hass), endpoint, shared_secret
    )
    snapshot = await client.async_get_snapshot()
    return snapshot.provider


async def _async_validate_portal(
    hass: HomeAssistant,
    *,
    username: str,
    password: str,
    site_id: str,
    timezone_name: str,
) -> None:
    """Validate portal credentials against one fixed read-only endpoint."""
    config = SolarEdgePortalConfig(
        username=username,
        password=password,
        site_id=site_id,
        timezone_name=timezone_name,
        timeout_seconds=DEFAULT_REQUEST_TIMEOUT_SECONDS,
    )
    client = SolarEdgePortalClient(config, session=async_get_clientsession(hass))
    await client.live_power_flow()


def _bridge_schema(
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


def _portal_schema(
    defaults: dict[str, Any],
    *,
    password_required: bool = True,
) -> vol.Schema:
    password_marker = vol.Required if password_required else vol.Optional
    return vol.Schema(
        {
            vol.Required(
                CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")
            ): vol.All(str, vol.Length(min=1, max=320)),
            password_marker(CONF_PASSWORD): vol.All(str, vol.Length(min=1, max=512)),
            vol.Required(CONF_SITE_ID, default=defaults.get(CONF_SITE_ID, "")): vol.All(
                str, vol.Match(r"^[0-9]{1,32}$")
            ),
            vol.Required(
                CONF_TIME_ZONE, default=defaults.get(CONF_TIME_ZONE, "UTC")
            ): vol.All(str, vol.Length(min=1, max=128)),
        }
    )


class SolarEdgeMonitoringBridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure one read-only SolarEdge monitoring source."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> BridgeOptionsFlow:
        """Return the options flow."""
        return BridgeOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose an unofficial portal connection or local bridge."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_show_menu(
            step_id="user", menu_options=[MODE_PORTAL, MODE_BRIDGE]
        )

    async def async_step_portal(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure direct read-only monitoring-portal access."""
        errors: dict[str, str] = {}
        defaults = {CONF_TIME_ZONE: self.hass.config.time_zone}
        if user_input is not None:
            values = {
                CONF_USERNAME: user_input[CONF_USERNAME].strip(),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_SITE_ID: user_input[CONF_SITE_ID].strip(),
                CONF_TIME_ZONE: user_input[CONF_TIME_ZONE].strip(),
            }
            try:
                await _async_validate_portal(
                    self.hass,
                    username=values[CONF_USERNAME],
                    password=values[CONF_PASSWORD],
                    site_id=values[CONF_SITE_ID],
                    timezone_name=values[CONF_TIME_ZONE],
                )
            except SolarEdgePortalAuthenticationError:
                errors["base"] = "invalid_auth"
            except (SolarEdgePortalError, ValueError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="SolarEdge Monitoring Portal",
                    data={CONF_MODE: MODE_PORTAL, **values},
                )
            defaults.update(values)
        return self.async_show_form(
            step_id="portal",
            data_schema=_portal_schema(defaults),
            errors=errors,
        )

    async def async_step_bridge(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure a compatible local snapshot bridge."""
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
                    await self.async_set_unique_id(DOMAIN)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title="SolarEdge Local Bridge",
                        data={
                            CONF_MODE: MODE_BRIDGE,
                            CONF_ENDPOINT: endpoint,
                            CONF_SHARED_SECRET: secret,
                        },
                    )
        return self.async_show_form(
            step_id="bridge",
            data_schema=_bridge_schema(user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start reauthentication after credentials are rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Replace only rejected credentials."""
        entry = self._get_reauth_entry()
        mode = entry.data.get(CONF_MODE, MODE_BRIDGE)
        errors: dict[str, str] = {}
        if mode == MODE_PORTAL:
            defaults = {
                CONF_USERNAME: entry.data[CONF_USERNAME],
                CONF_SITE_ID: entry.data[CONF_SITE_ID],
                CONF_TIME_ZONE: entry.data[CONF_TIME_ZONE],
            }
            if user_input is not None:
                try:
                    await _async_validate_portal(
                        self.hass,
                        username=user_input[CONF_USERNAME].strip(),
                        password=user_input[CONF_PASSWORD],
                        site_id=entry.data[CONF_SITE_ID],
                        timezone_name=entry.data[CONF_TIME_ZONE],
                    )
                except SolarEdgePortalAuthenticationError:
                    errors["base"] = "invalid_auth"
                except (SolarEdgePortalError, ValueError):
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_USERNAME: user_input[CONF_USERNAME].strip(),
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                        },
                        reason="reauth_successful",
                    )
            schema = vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME, default=defaults[CONF_USERNAME]
                    ): vol.All(str, vol.Length(min=1, max=320)),
                    vol.Required(CONF_PASSWORD): vol.All(
                        str, vol.Length(min=1, max=512)
                    ),
                }
            )
        else:
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
            schema = vol.Schema(
                {
                    vol.Required(CONF_SHARED_SECRET): vol.All(
                        str, vol.Length(min=16, max=512)
                    )
                }
            )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=schema, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change connection details without changing modes."""
        entry = self._get_reconfigure_entry()
        mode = entry.data.get(CONF_MODE, MODE_BRIDGE)
        errors: dict[str, str] = {}
        if mode == MODE_PORTAL:
            defaults = dict(entry.data)
            if user_input is not None:
                values = {
                    CONF_USERNAME: user_input[CONF_USERNAME].strip(),
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_SITE_ID: user_input[CONF_SITE_ID].strip(),
                    CONF_TIME_ZONE: user_input[CONF_TIME_ZONE].strip(),
                }
                try:
                    await _async_validate_portal(
                        self.hass,
                        username=values[CONF_USERNAME],
                        password=values[CONF_PASSWORD],
                        site_id=values[CONF_SITE_ID],
                        timezone_name=values[CONF_TIME_ZONE],
                    )
                except SolarEdgePortalAuthenticationError:
                    errors["base"] = "invalid_auth"
                except (SolarEdgePortalError, ValueError):
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={CONF_MODE: MODE_PORTAL, **values},
                        reason="reconfigure_successful",
                    )
                defaults.update(values)
            schema = _portal_schema(defaults)
        else:
            defaults = {CONF_ENDPOINT: entry.data[CONF_ENDPOINT]}
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
                                CONF_MODE: MODE_BRIDGE,
                                CONF_ENDPOINT: endpoint,
                                CONF_SHARED_SECRET: secret,
                            },
                            reason="reconfigure_successful",
                        )
            schema = _bridge_schema(user_input or defaults)
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )


class BridgeOptionsFlow(config_entries.OptionsFlow):
    """Configure polling, timeout, and freshness limits."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage polling options."""
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
