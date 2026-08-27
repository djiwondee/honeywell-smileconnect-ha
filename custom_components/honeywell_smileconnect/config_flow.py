"""Config flow for Honeywell Smile Connect."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant import config_entries, core, exceptions

from .api.login import Login
from .const import CONF_HOST, CONF_INTERVAL, CONF_PASSWORD, CONF_USER, DEFAULT_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USER): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_INTERVAL, default=DEFAULT_INTERVAL): int,
    }
)


async def validate_input(hass: core.HomeAssistant, data: dict) -> dict:
    """Try a real login against the gateway to validate the given input."""
    login = Login("http://" + data[CONF_HOST])
    try:
        await hass.async_add_executor_job(login.authorize, data[CONF_USER], data[CONF_PASSWORD])
    except ValueError as err:
        raise InvalidAuth from err
    except Exception as err:  # noqa: BLE001
        raise CannotConnect from err

    return {"title": f"Smile Connect ({data[CONF_HOST]})"}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Honeywell Smile Connect."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA)

        errors: dict[str, str] = {}
        try:
            info = await validate_input(self.hass, user_input)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected exception during config flow")
            errors["base"] = "unknown"
        else:
            return self.async_create_entry(title=info["title"], data={}, options=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate invalid auth."""
