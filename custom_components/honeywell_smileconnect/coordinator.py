# Change log:
# - 2026-09-21 (b): Added a shared asyncio.Lock (async_api_call() /
#   async_api_session()) serialising EVERY authenticated gateway call in
#   the integration. api/credentials.py's next_reqcount() is a plain
#   post-incremented int, and the PBKDF2 request signature is computed over
#   that counter - two callers running concurrently in different executor
#   threads can therefore sign with the same value, or arrive at the
#   gateway out of order. Getting that counter wrong is exactly what
#   produced "Your session is finished, please log in again" during the
#   original protocol work (see credentials.py's own docstring).
#   The race is not new: every climate.py/number.py entity service already
#   shares this coordinator's ApiMethods with the poll cycle. It became
#   worth fixing now because 0.4.0 adds a THIRD caller
#   (schedule_coordinator.py) that polls once per room, turning a rare
#   interleave into a routine one.
#   Note ping_coordinator.py deliberately does NOT take this lock: it is
#   unauthenticated, holds no Credentials, and must keep working when the
#   authenticated session is broken - which is its entire reason to exist.
#   Note also that a preset change can hold the lock for up to ~10s
#   (SceneManager's activation verify loop), so a poll cycle can run that
#   much late; DataUpdateCoordinator simply reschedules, but it is visible
#   as a gap in history.
# - 2026-09-21 (a): Added automatic re-login on session expiry. Until now
#   async_login() was only called when `self.api is None`, which is true
#   exactly once per Home Assistant start - so a session that died later
#   (gateway reboot, network outage, anything the gateway treats as
#   session loss) was NEVER recovered from without a manual integration
#   reload. reqcount is reset only in Credentials.__init__, and only
#   Login.authorize() constructs one, so a fresh login is the only way
#   back.
#   That gap was invisible until now for a second reason, fixed in
#   api_request.py in the same release: a failed response was not detected
#   at all, so this coordinator reported a SUCCESSFUL update carrying
#   empty data instead of failing. UpdateFailed was unreachable for the
#   entire failure mode. With api_request.py now raising
#   SmileConnectSessionExpired, "the session is gone" is distinguishable
#   from every other error, which is what makes a targeted retry possible
#   rather than blindly re-logging-in on any hiccup.
# - 2026-09-11: Added "scene_duration_native" to coordinator.data - remaining
#   duration for each TIMED_PRESET_SCENE_NAMES scene, already converted to
#   its own real-world unit via the new ApiMethods.get_scene_duration_native()
#   (minutes for Boost, hours for Party/Leave, days for Holiday). Backs the
#   new per-room, per-preset "duration remaining" sensors (sensor.py).
#   _get_scene_active_rooms() renamed to _get_scene_active_rooms_and_
#   durations() and now returns both dicts from the ONE get_scene_status()
#   call it already made, rather than fetching scene state twice -
#   get_scene_duration_native() is only called for scenes that are
#   actually active, mirroring the existing get_scene_rooms() cost-
#   conscious pattern (most poll cycles have zero or one active timed
#   preset, not four).
# - 2026-09-01: Added "scene_active_rooms" to coordinator.data - per-scene
#   room membership for Standby + the four presets, fetched via
#   /api/scene/status + /api/scene/getrooms. roomstatus (already present
#   in each room's own data) was found unreliable for compound states: a
#   still-active Standby can be masked by a preset in roomstatus, and
#   Holiday specifically never resolves via roomstatus at all while
#   Standby is simultaneously active (a confirmed gateway firmware quirk,
#   not a bug in this integration - see docs/protocol.md §4e/§4f).
#   climate.py's hvac_mode/preset_mode now read this ground-truth field
#   directly instead of inferring state from roomstatus. Wrapped in the
#   same try/except as the existing two calls, matching this file's
#   established error-handling pattern (a failure here fails the whole
#   poll cycle, same as an existing get_weather failure already does).
# - 2026-08-27: coordinator.data restructured from a bare room list to
#   {"rooms": [...], "weather": {...}} so a single poll cycle (and a single
#   logged-in session) covers both climate entities and the new weather
#   sensors (see sensor.py). This is a breaking change for any code that
#   assumed coordinator.data was itself the room list - climate.py was
#   updated accordingly (see its own change log).
"""DataUpdateCoordinator for Honeywell Smile Connect."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.api_methods import ApiMethods
from .api.exceptions import SmileConnectSessionExpired
from .api.login import Login
from .const import TIMED_PRESET_SCENE_NAMES, TRACKED_SCENE_NAMES

_LOGGER = logging.getLogger(__name__)


class SmileConnectCoordinator(DataUpdateCoordinator):
    """Polls room list + weather + scene state in one cycle and exposes it
    to entities.

    `data` is a dict shaped as:
        {"rooms": [...room dicts as returned by ApiMethods.get_rooms_list()...],
         "weather": {...raw /api/weather response...},
         "scene_active_rooms": {scene_name: {room_id, ...}, ...} for every
             TRACKED_SCENE_NAME - the set of room IDs for which that scene
             is genuinely active right now (isActive AND room is a current
             member). See this file's change log and docs/protocol.md
             §4e/§4f for why this exists alongside (not instead of) each
             room's own roomstatus field.
         "scene_duration_native": {scene_name: float | None, ...} for every
             TIMED_PRESET_SCENE_NAMES scene - remaining duration in the
             scene's own real-world unit (minutes/hours/hours/days) if
             active, else None. See this file's change log.}
    """

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        username: str,
        password: str,
        interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="honeywell_smileconnect",
            update_interval=timedelta(seconds=interval),
        )
        self.host = host
        self.username = username
        self.password = password
        self.api: ApiMethods | None = None
        # Guards ALL authenticated gateway access - see this module's
        # change log. Created here rather than lazily so there is exactly
        # one lock object for the lifetime of the coordinator.
        self._api_lock = asyncio.Lock()

    async def async_api_call(self, func: Callable[..., Any], *args: Any) -> Any:
        """Run one blocking ApiMethods call in the executor, serialised.

        Every authenticated call in the integration goes through here
        (coordinators, entity services, schedule writes) so that no two
        requests can ever be signed with the same reqcount or reach the
        gateway out of order - see this module's change log.

        NOT re-entrant: asyncio.Lock deadlocks on a second acquire from the
        same task. Never call this from inside an async_api_session()
        block; use the `api` handle that block yields instead.
        """
        async with self._api_lock:
            return await self.hass.async_add_executor_job(func, *args)

    @asynccontextmanager
    async def async_api_session(self):
        """Hold the gateway lock across SEVERAL calls, as one unit.

        For read-modify-write sequences (the switching-times Actions read
        the current schedule, edit it, and write it straight back). Without
        this, two concurrent writes can both read the same base and the
        second silently discards the first's change - the gateway has no
        partial-update endpoint, so every write is a full-week replacement
        (docs/switching-times-api.md).

        Yields the ApiMethods handle; inside the block, call it via
        hass.async_add_executor_job() directly, NOT via async_api_call()
        (which would try to take the same non-re-entrant lock again).
        """
        async with self._api_lock:
            yield self.api

    async def async_login(self) -> None:
        """Perform the initial (or a re-)login and build the API client."""
        login_manager = Login("http://" + self.host)
        # Takes the lock directly rather than via async_api_call(): self.api
        # does not exist yet, and a re-login must not race an in-flight
        # request that is still using the old credentials object.
        async with self._api_lock:
            credentials = await self.hass.async_add_executor_job(
                login_manager.authorize, self.username, self.password
            )
            self.api = ApiMethods(credentials, "http://" + self.host)

    async def _async_update_data(self) -> dict:
        if self.api is None:
            await self.async_login()

        try:
            return await self._async_fetch_all()
        except SmileConnectSessionExpired as expired:
            _LOGGER.info(
                "Gateway session expired (%s) - logging in again and retrying", expired
            )
            try:
                await self.async_login()
                return await self._async_fetch_all()
            except Exception as err:
                # Deliberately no second retry: if a cycle fails straight
                # after a fresh login, another login will not help, and
                # looping here would hammer the gateway. A later cycle
                # tries again on its own schedule.
                raise UpdateFailed(
                    f"Error communicating with gateway after re-login: {err}"
                ) from err
        except Exception as err:
            raise UpdateFailed(f"Error communicating with gateway: {err}") from err

    async def _async_fetch_all(self) -> dict:
        """One full poll cycle, under ONE lock acquisition.

        Raises; the caller decides what that means (see
        _async_update_data, which retries this once after a re-login when
        the gateway reports the session gone).

        Holding the lock across all three calls keeps a poll internally
        consistent - rooms and scenes seen at the same moment - and stops
        a user action landing between them. Uses async_add_executor_job
        directly inside the session rather than async_api_call(), which
        would deadlock on the same non-re-entrant lock.
        """
        async with self.async_api_session() as api:
            rooms = await self.hass.async_add_executor_job(api.get_rooms_list)
            weather = await self.hass.async_add_executor_job(api.get_weather)
            scene_active_rooms, scene_duration_native = (
                await self.hass.async_add_executor_job(
                    self._get_scene_active_rooms_and_durations
                )
            )
        return {
            "rooms": rooms,
            "weather": weather,
            "scene_active_rooms": scene_active_rooms,
            "scene_duration_native": scene_duration_native,
        }

    def _get_scene_active_rooms_and_durations(
        self,
    ) -> tuple[dict[str, set], dict[str, float | None]]:
        """Ground-truth per-scene room membership (for every
        TRACKED_SCENE_NAME) and remaining duration (for every
        TIMED_PRESET_SCENE_NAMES scene) - see this module's change log and
        docs/protocol.md §4e/§4f for why roomstatus alone cannot be
        trusted for room membership.

        One /api/scene/status call covers isActive for every scene;
        /api/scene/getrooms and /api/scene/duration are only called for
        scenes that ARE active, to keep the common case (most scenes
        inactive most of the time) cheap.
        """
        active_names = {
            s["name"] for s in self.api.get_scene_status().get("scenes", []) if s.get("isActive")
        }
        active_rooms = {
            scene.value: set(self.api.get_scene_rooms(scene.value)) if scene.value in active_names else set()
            for scene in TRACKED_SCENE_NAMES
        }
        duration_native = {
            scene.value: self.api.get_scene_duration_native(scene.value) if scene.value in active_names else None
            for scene in TIMED_PRESET_SCENE_NAMES
        }
        return active_rooms, duration_native
