"""The Honeywell Smile Connect integration."""
# Change log:
# - 2026-08-27 (b): Added a second, independent SmileConnectPingCoordinator
#   (see ping_coordinator.py) alongside the existing authenticated
#   coordinator, plus Platform.BINARY_SENSOR for the new connectivity
#   entity. Both coordinators are now wrapped in a small SmileConnectData
#   dataclass stored in hass.data, instead of storing the coordinator
#   directly - this is what climate.py/sensor.py/binary_sensor.py now read
#   from. `unique_id` on that dataclass is the entry's HA-native
#   config_entry.unique_id (captured via /api/ping during setup - see
#   config_flow.py), used as the anchor for device.py's device_info
#   builders.
# - 2026-08-27 (a): Added Platform.SENSOR (outside temperature/min/max).
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_HOST,
    CONF_INTERVAL,
    CONF_PASSWORD,
    CONF_PING_INTERVAL,
    CONF_USER,
    DOMAIN,
)
from .coordinator import SmileConnectCoordinator
from .ping_coordinator import SmileConnectPingCoordinator

PLATFORMS: list[Platform] = [Platform.CLIMATE, Platform.SENSOR, Platform.BINARY_SENSOR]


@dataclass
class SmileConnectData:
    """Everything the platforms (climate/sensor/binary_sensor) need."""

    coordinator: SmileConnectCoordinator
    ping_coordinator: SmileConnectPingCoordinator
    unique_id: str


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

    ping_coordinator = SmileConnectPingCoordinator(
        hass,
        config_entry.options[CONF_HOST],
        config_entry.options[CONF_PING_INTERVAL],
    )
    await ping_coordinator.async_config_entry_first_refresh()

    # config_entry.unique_id was set during the config flow via
    # async_set_unique_id() using /api/ping's "uniqueid" (or a host-based
    # fallback) - see config_flow.py validate_input(). It is never None by
    # the time an entry exists.
    hass.data[DOMAIN][config_entry.entry_id] = SmileConnectData(
        coordinator=coordinator,
        ping_coordinator=ping_coordinator,
        unique_id=config_entry.unique_id,
    )
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
