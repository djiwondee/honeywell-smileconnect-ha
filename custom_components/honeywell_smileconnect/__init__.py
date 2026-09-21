"""The Honeywell Smile Connect integration."""
# Change log:
# - 2026-09-21: Added the schedule coordinator (schedule_coordinator.py)
#   and self-registration of the bundled Lovelace card. The card's JS is
#   served from this component's own frontend/ directory via
#   async_register_static_paths() and injected with add_extra_js_url(), so
#   a HACS install needs no manual "Dashboard -> Resources" step and the
#   card can never drift out of version sync with the integration that
#   feeds it. Cache busting uses the manifest version, read through
#   async_get_integration() rather than duplicated as a constant here.
#   Registration is guarded by a flag at a TOP-LEVEL hass.data key, not
#   inside hass.data[DOMAIN] - async_unload_entry pops that one, so a
#   reload would clear the flag and the second
#   async_register_static_paths() call would raise on the duplicate route.
#   Nothing is unregistered on unload: neither static paths nor
#   add_extra_js_url have a public removal path, and leaving them for the
#   process lifetime is the normal behaviour for an integration-bundled
#   card.
#   CONF_SCHEDULE_INTERVAL is read with .get(<default>) rather than []:
#   entries created before 0.4.0 have no such key and would otherwise
#   KeyError on upgrade. CONF_PING_INTERVAL was changed to match - it had
#   the same latent problem and only got away with it because no entry
#   predates it any more.
# - 2026-09-18: Added Platform.NUMBER (number.py) - per-room sliders for
#   the three fixed schedule temperatures (desiredTempDay/Day2/Night).
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

import logging
from dataclasses import dataclass
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import (
    CONF_HOST,
    CONF_INTERVAL,
    CONF_PASSWORD,
    CONF_PING_INTERVAL,
    CONF_SCHEDULE_INTERVAL,
    CONF_USER,
    DEFAULT_PING_INTERVAL,
    DEFAULT_SCHEDULE_INTERVAL,
    DOMAIN,
)
from .coordinator import SmileConnectCoordinator
from .ping_coordinator import SmileConnectPingCoordinator
from .schedule_coordinator import SmileConnectScheduleCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
]

# Where the bundled Lovelace card is served from, and the local directory
# it is served out of. The URL is deliberately namespaced under the domain
# so it cannot collide with another integration doing the same thing.
FRONTEND_URL_BASE = f"/{DOMAIN}/frontend"
FRONTEND_DIR = Path(__file__).parent / "frontend"
CARD_FILENAME = "smileconnect-schedule-card.js"
# Top-level key on purpose - see this module's change log.
DATA_FRONTEND_REGISTERED = f"{DOMAIN}_frontend_registered"


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the bundled card and tell the frontend to load it.

    Runs at most once per Home Assistant process: registering the same
    static path twice raises on the duplicate aiohttp route, and
    add_extra_js_url would queue the same URL repeatedly.
    """
    if hass.data.get(DATA_FRONTEND_REGISTERED):
        return

    integration = await async_get_integration(hass, DOMAIN)
    await hass.http.async_register_static_paths(
        [
            # cache_headers=False so an edited card shows up on a reload
            # during development; the ?v= query below is what actually
            # busts a released user's browser cache between versions.
            StaticPathConfig(FRONTEND_URL_BASE, str(FRONTEND_DIR), False)
        ]
    )
    add_extra_js_url(hass, f"{FRONTEND_URL_BASE}/{CARD_FILENAME}?v={integration.version}")
    hass.data[DATA_FRONTEND_REGISTERED] = True
    _LOGGER.debug("Registered Lovelace card at %s/%s", FRONTEND_URL_BASE, CARD_FILENAME)


@dataclass
class SmileConnectData:
    """Everything the platforms (climate/sensor/binary_sensor/number) need."""

    coordinator: SmileConnectCoordinator
    ping_coordinator: SmileConnectPingCoordinator
    schedule_coordinator: SmileConnectScheduleCoordinator
    unique_id: str


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up Honeywell Smile Connect from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    await _async_register_frontend(hass)

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
        config_entry.options.get(CONF_PING_INTERVAL, DEFAULT_PING_INTERVAL),
    )
    await ping_coordinator.async_config_entry_first_refresh()

    # Built after the main coordinator's first refresh on purpose: it reads
    # the room list from that coordinator's data rather than making a
    # second /api/room/list call of its own.
    schedule_coordinator = SmileConnectScheduleCoordinator(
        hass,
        coordinator,
        config_entry.options.get(CONF_SCHEDULE_INTERVAL, DEFAULT_SCHEDULE_INTERVAL),
    )
    # async_refresh(), NOT async_config_entry_first_refresh(): the latter
    # raises ConfigEntryNotReady on failure, which would take the WHOLE
    # integration down because one secondary endpoint had a bad moment -
    # the exact coupling this coordinator was split out to avoid. A
    # failure here just leaves the schedule sensors unavailable until the
    # next cycle.
    await schedule_coordinator.async_refresh()

    # config_entry.unique_id was set during the config flow via
    # async_set_unique_id() using /api/ping's "uniqueid" (or a host-based
    # fallback) - see config_flow.py validate_input(). It is never None by
    # the time an entry exists.
    hass.data[DOMAIN][config_entry.entry_id] = SmileConnectData(
        coordinator=coordinator,
        ping_coordinator=ping_coordinator,
        schedule_coordinator=schedule_coordinator,
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
