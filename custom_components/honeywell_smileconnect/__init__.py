"""The Honeywell Smile Connect integration."""
# Change log:
# - 2026-09-22 (d): Load the card through exactly ONE mechanism. Both the
#   Lovelace resource and add_extra_js_url() were active, so the same URL
#   was requested twice CONCURRENTLY on every page load. Home Assistant's
#   service worker routes anything it does not recognise through a
#   catch-all CacheFirst strategy (see its last registerRoute) whose catch
#   handler answers Response.error() for non-document requests. Two
#   concurrent CacheFirst requests for one cache key make one lose; that
#   error reaches the dynamic import HA writes into its index page, the
#   import rejects, and the element is never defined - on roughly every
#   second load. Invisible in the console, because that import carries no
#   .catch(). add_extra_js_url() is now used ONLY when the Lovelace
#   resource could not be registered (YAML mode).
# - 2026-09-22 (c): Put the content hash in the FILENAME instead of a
#   `?v=` query string, and serve it from its own route. Home Assistant's
#   service worker intercepts requests, and with a query-string URL the
#   dynamic import HA writes into its index page rejected on roughly
#   every other page load - the element was then never defined and the
#   dashboard showed "Custom element doesn't exist". User-confirmed: with
#   the service worker's "Bypass for network" enabled, 20+ reloads were
#   clean; without it, every second one broke. HA's own bundles put the
#   hash in the filename (core.<hash>.js) for the same reason. The
#   unhashed directory stays registered so YAML-mode dashboards keep a
#   stable URL to reference.
# - 2026-09-22 (b): Cache-bust the card URL with a CONTENT HASH, not just
#   the integration version. The version alone does not change when the
#   card file is edited without a release, so the browser keeps its cached
#   copy - and whether it revalidates at all is heuristic, so the same
#   page works on one load and fails on the next. That cost a long,
#   confusing debugging session where a fixed file was live on the server
#   while the browser kept running the old one. The hash also makes
#   _async_register_lovelace_resource() update the stored resource URL by
#   itself, since it compares URLs.
# - 2026-09-22 (a): Only the static path and add_extra_js_url() stay behind
#   the once-per-process guard; the Lovelace resource is now (re)checked
#   on EVERY setup. The resource lives in persistent storage rather than
#   hass.data, so it outlives the process and has to be re-checked after
#   an upgrade to pick up the new ?v=. Guarding it behind the same flag
#   meant upgrading the integration in a RUNNING Home Assistant and
#   reloading the entry never created the resource at all - the flag was
#   already set by the previous version's setup, so the whole function
#   returned immediately. Anyone who upgraded without a full restart got
#   none of (b)'s fix. The resource registration is idempotent by design,
#   so running it every setup costs nothing.
#   Also raised the "no Lovelace resource" message from debug to warning:
#   it means the card is running without its ordering guarantee, which is
#   exactly the failure that is hard to diagnose from the browser.
# - 2026-09-21 (b): Also register the card as a LOVELACE RESOURCE, not
#   only via add_extra_js_url(). Live testing found a load-order race:
#   add_extra_js_url() is a generic "load this script sometime" with no
#   ordering guarantee relative to Lovelace rendering its cards. On a cold
#   first page load the dashboard bundle is slow enough that our small
#   module wins the race and the card appears - on a RELOAD everything is
#   warm, Lovelace renders immediately, calls customElements.get() before
#   our module has executed, and shows "Custom element doesn't exist".
#   Confirmed in a private window (works once, fails after refresh) and by
#   customElements.get(...) still being undefined on the broken page while
#   the page itself demonstrably contained the script tag - so this was
#   never a caching problem, which is what it first looked like.
#   Lovelace resources are loaded by the Lovelace panel BEFORE it creates
#   cards, which is exactly the ordering guarantee this needs, and is what
#   HACS-installed frontend plugins use. add_extra_js_url() is KEPT as a
#   fallback: the resource collection is read-only in YAML mode, where
#   only the extra_module_url path works. Loading the same URL twice is
#   harmless - it is one URL, so the browser's module registry executes it
#   once.
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

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import DOMAIN as LOVELACE_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

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
CARD_STEM = CARD_FILENAME.removesuffix(".js")
# Separate base for the content-addressed URL, so its route cannot collide
# with the directory served at FRONTEND_URL_BASE (which stays as the
# stable, unhashed URL that YAML-mode dashboards have to reference by
# hand - see README).
CARD_URL_BASE = f"/{DOMAIN}/card"
# Top-level key on purpose - see this module's change log.
DATA_FRONTEND_REGISTERED = f"{DOMAIN}_frontend_registered"
DATA_EXTRA_JS_ADDED = f"{DOMAIN}_extra_js_added"


def _hashed_card_url(fingerprint: str) -> str:
    """Content-addressed URL for the card, hash in the FILENAME.

    Not a `?v=` query string, which is what this used to be. Home
    Assistant's service worker intercepts requests, and a query-string
    URL turned out to be served unreliably through it: the dynamic
    import in HA's index page rejected on roughly every other page load,
    the element was therefore never defined, and the dashboard showed
    "Custom element doesn't exist". Confirmed by the user: with the
    service worker's "Bypass for network" enabled, 20+ reloads were
    clean; without it, every second one failed.

    Home Assistant's own bundles put the hash in the filename for the
    same reason (core.9c169bb8cc5569b5.js), so this simply follows the
    convention that is known to work with that service worker.
    """
    return f"{CARD_URL_BASE}/{CARD_STEM}.{fingerprint}.js"


def _is_our_card_resource(url: object) -> bool:
    """Whether a Lovelace resource entry points at our card, any version.

    Deliberately loose about the shape: it has to recognise the older
    `?v=`-style URLs so an upgrade rewrites them instead of leaving a
    second, dead entry behind.
    """
    path = str(url).split("?", 1)[0]
    return path.startswith(f"/{DOMAIN}/") and CARD_STEM in path and path.endswith(".js")


def _card_fingerprint() -> str:
    """Short content hash of the card file, for cache busting.

    The integration version alone is not enough: editing the card without
    releasing a new version leaves the URL unchanged, so browsers keep
    serving the previous file from their heuristic cache - intermittently,
    since whether they revalidate is up to them. That produced a genuinely
    baffling "works sometimes, broken other times" during development.

    A content hash changes exactly when the file does, which also makes
    _async_register_lovelace_resource() update the stored resource URL on
    its own (it compares URLs). Not a security primitive - sha256 is used
    purely as a content digest, and only the first 8 characters are kept
    because this only has to distinguish one build from the next.

    Blocking file I/O, so callers run it in the executor.
    """
    try:
        return hashlib.sha256(FRONTEND_DIR.joinpath(CARD_FILENAME).read_bytes()).hexdigest()[:8]
    except OSError:
        # Missing or unreadable file is the static path's problem to
        # report, not ours; fall back to a constant so the URL is still
        # well-formed.
        return "0"


def _lovelace_resources(hass: HomeAssistant):
    """The Lovelace resource collection, or None if it cannot be used.

    hass.data["lovelace"] has been both a plain dict and a dataclass
    across Home Assistant versions, so both are handled. In YAML mode the
    collection is read-only (no async_create_item), which is why the
    caller falls back to add_extra_js_url() alone there.
    """
    data = hass.data.get(LOVELACE_DOMAIN)
    if data is None:
        return None
    resources = getattr(data, "resources", None)
    if resources is None and isinstance(data, dict):
        resources = data.get("resources")
    if resources is None or not hasattr(resources, "async_create_item"):
        return None
    return resources


async def _async_register_lovelace_resource(hass: HomeAssistant, url: str) -> bool:
    """Make sure exactly one Lovelace resource points at our card.

    Idempotent, and version-aware: an existing entry for this card whose
    URL carries an older ?v= is UPDATED rather than joined by a second
    one, so upgrades don't accumulate stale resources.

    Returns True when the resource is in place.
    """
    resources = _lovelace_resources(hass)
    if resources is None:
        return False

    # Loads the collection from storage on first access.
    await resources.async_get_info()

    for item in resources.async_items() or []:
        if not _is_our_card_resource(item.get("url", "")):
            continue
        if item["url"] != url:
            await resources.async_update_item(item["id"], {"url": url})
            _LOGGER.debug("Updated Lovelace resource to %s", url)
        return True

    await resources.async_create_item({"res_type": "module", "url": url})
    _LOGGER.debug("Created Lovelace resource %s", url)
    return True


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the bundled card and make the frontend load it.

    Two halves with DIFFERENT lifetimes, which is why the guard below
    covers only one of them:

    * The static path and add_extra_js_url() may only ever run once per
      Home Assistant process - registering the same aiohttp route twice
      raises, and the URL set would just collect duplicates.
    * The Lovelace resource must be checked on EVERY setup. It lives in
      persistent storage, not in hass.data, so it outlives the process,
      and it has to be re-checked after an upgrade to pick up the new
      ?v= version. Guarding it behind the once-per-process flag meant
      that upgrading the integration in a RUNNING Home Assistant and
      reloading the entry never created or updated the resource at all -
      the flag was already set by the previous version's setup. It is
      idempotent by design (see _async_register_lovelace_resource), so
      running it every time costs nothing.

    See this module's change log for the load-order race that made the
    resource route necessary, and why add_extra_js_url stays as the
    YAML-mode fallback.
    """
    fingerprint = await hass.async_add_executor_job(_card_fingerprint)
    url = _hashed_card_url(fingerprint)

    if not hass.data.get(DATA_FRONTEND_REGISTERED):
        await hass.http.async_register_static_paths(
            [
                # The stable, unhashed directory. Not what the frontend is
                # pointed at, but it keeps a predictable URL available for
                # YAML-mode dashboards, which cannot reference a hash.
                StaticPathConfig(FRONTEND_URL_BASE, str(FRONTEND_DIR), False),
                # The content-addressed URL the frontend actually loads.
                # cache_headers=True is safe and correct here precisely
                # because the URL changes whenever the file does.
                StaticPathConfig(url, str(FRONTEND_DIR / CARD_FILENAME), True),
            ]
        )
        hass.data[DATA_FRONTEND_REGISTERED] = True
        _LOGGER.debug("Serving the schedule card at %s", url)

    try:
        registered = await _async_register_lovelace_resource(hass, url)
    except Exception as err:  # noqa: BLE001 - deliberately broad, see below
        # The resource collection is a semi-public API that has changed
        # shape between releases. Never let it break setup: the
        # add_extra_js_url() path still loads the card, just without the
        # ordering guarantee.
        _LOGGER.warning(
            "Could not register the schedule card as a Lovelace resource (%s); "
            "falling back to extra_module_url. The card may need a second page "
            "load to appear.",
            err,
        )
        registered = False

    if registered:
        return

    # Fallback ONLY. Never alongside the Lovelace resource: both would load
    # the same URL at the same time, and Home Assistant's service worker
    # routes anything it does not know through a catch-all CacheFirst
    # strategy whose catch handler answers Response.error() for
    # non-document requests. Two concurrent CacheFirst requests for one
    # cache key make one of them lose, that error reaches the dynamic
    # import HA writes into its index page, the import rejects, and the
    # element is never defined - on roughly every second page load. The
    # rejection is invisible because that import has no .catch(), so it
    # only ever surfaces as HA's "Cannot parse given Error object".
    if not hass.data.get(DATA_EXTRA_JS_ADDED):
        add_extra_js_url(hass, url)
        hass.data[DATA_EXTRA_JS_ADDED] = True
    _LOGGER.warning(
        "The schedule card is not registered as a Lovelace resource "
        "(YAML-mode dashboards?). Falling back to extra_module_url, which "
        "gives no ordering guarantee - the card may need a second page load "
        "to appear. See the README for the manual resource entry."
    )


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
