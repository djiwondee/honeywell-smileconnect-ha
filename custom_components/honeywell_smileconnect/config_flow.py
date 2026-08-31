"""Config flow for Honeywell Smile Connect."""
# Change log:
# - 2026-08-27: Added CONF_PING_INTERVAL to the setup schema; capture the
#   gateway's own uniqueid via /api/ping during validation and register it
#   as this entry's native HA unique_id (via async_set_unique_id +
#   _abort_if_unique_id_configured) rather than inventing a custom data
#   key - this also finally makes the pre-existing but previously unused
#   "already_configured" abort string actually functional. Falls back to a
#   host-based id if /api/ping is unreachable during setup. Added
#   OptionsFlowHandler so host/credentials/both poll intervals can be
#   changed after initial setup without recreating the entry.
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant import config_entries, core, exceptions
from homeassistant.core import callback

from .api import ping as ping_api
from .api.login import Login
from .const import (
    CONF_HOST,
    CONF_INTERVAL,
    CONF_PASSWORD,
    CONF_PING_INTERVAL,
    CONF_USER,
    DEFAULT_INTERVAL,
    DEFAULT_PING_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _build_schema(defaults: dict | None = None) -> vol.Schema:
    """Shared schema for both the initial setup step and the options flow.

    When `defaults` is given (editing an existing entry via the options
    flow), fields are pre-filled with the current values; on first setup
    (defaults=None) fields start blank.
    """
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, vol.UNDEFINED)): str,
            vol.Required(CONF_USER, default=defaults.get(CONF_USER, vol.UNDEFINED)): str,
            vol.Required(
                CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, vol.UNDEFINED)
            ): str,
            vol.Optional(
                CONF_INTERVAL, default=defaults.get(CONF_INTERVAL, DEFAULT_INTERVAL)
            ): int,
            vol.Optional(
                CONF_PING_INTERVAL,
                default=defaults.get(CONF_PING_INTERVAL, DEFAULT_PING_INTERVAL),
            ): int,
        }
    )


async def validate_input(hass: core.HomeAssistant, data: dict) -> dict:
    """Try a real login against the gateway to validate the given input,
    and opportunistically capture the gateway's own uniqueid via the
    unauthenticated /api/ping endpoint for use as a stable HA unique_id
    (falls back to a host-based id if ping fails for any reason - this
    must never block setup just because the diagnostic endpoint had a
    hiccup, since the actual login already proved the gateway is reachable
    and correctly configured).
    """
    base_url = "http://" + data[CONF_HOST]

    login = Login(base_url)
    try:
        await hass.async_add_executor_job(login.authorize, data[CONF_USER], data[CONF_PASSWORD])
    except ValueError as err:
        raise InvalidAuth from err
    except Exception as err:
        raise CannotConnect from err

    unique_id = f"host:{data[CONF_HOST]}"
    try:
        ping_response = await hass.async_add_executor_job(ping_api.ping, base_url)
        if ping_response.get("uniqueid"):
            unique_id = ping_response["uniqueid"]
    except Exception as err:  # noqa: BLE001 - deliberately broad: any failure
        # here (timeout, network error, malformed JSON, ...) must fall back
        # to a host-based id rather than block setup, since the login above
        # already proved the gateway itself is reachable and configured
        # correctly - a diagnostic-endpoint hiccup shouldn't be fatal here.
        _LOGGER.warning(
            "Could not reach /api/ping during setup to capture a stable "
            "device id; falling back to a host-based id. Error: %s",
            err,
        )

    return {"title": f"Smile Connect ({data[CONF_HOST]})", "unique_id": unique_id}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Honeywell Smile Connect."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=_build_schema())

        errors: dict[str, str] = {}
        try:
            info = await validate_input(self.hass, user_input)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except Exception:
            _LOGGER.exception("Unexpected exception during config flow")
            errors["base"] = "unknown"
        else:
            await self.async_set_unique_id(info["unique_id"])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=info["title"], data={}, options=user_input)

        return self.async_show_form(
            step_id="user", data_schema=_build_schema(user_input), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowHandler:
        return OptionsFlowHandler()


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Lets host/credentials/both poll intervals be changed after initial
    setup, without recreating the config entry (which would otherwise
    discard the stable /api/ping-derived unique_id - see validate_input()).

    Does not override __init__ / assign self.config_entry manually: recent
    Home Assistant versions provide `self.config_entry` automatically on
    the base OptionsFlow class, and manual assignment is deprecated.
    """

    async def async_step_init(self, user_input=None):
        errors: dict[str, str] = {}
        current = dict(self.config_entry.options)

        if user_input is not None:
            try:
                await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception during options flow")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title="", data=user_input)
            current = user_input

        return self.async_show_form(
            step_id="init", data_schema=_build_schema(current), errors=errors
        )


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate invalid auth."""
