"""The Honeywell Smile Connect integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_HOST, CONF_INTERVAL, CONF_PASSWORD, CONF_USER, DOMAIN
from .coordinator import SmileConnectCoordinator

PLATFORMS: list[Platform] = [Platform.CLIMATE]


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up Honeywell Smile Connect from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    coordinator = SmileConnectCoordinator(
        hass,
        config_entry.options[CONF_HOST],
        config_entry.options[CONF_USER],
        config_entry.options[CONF_PASSWORD],
        config_entry.options[CONF_INTERVAL],
    )
    await coordinator.async_login()
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][config_entry.entry_id] = coordinator
    config_entry.async_on_unload(config_entry.add_update_listener(_update_listener))

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(config_entry.entry_id)
    return unload_ok


async def _update_listener(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(config_entry.entry_id)
