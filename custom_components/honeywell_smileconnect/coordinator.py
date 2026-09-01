# Change log:
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

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.api_methods import ApiMethods
from .api.login import Login
from .const import TRACKED_SCENE_NAMES

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
             room's own roomstatus field.}
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

    async def async_login(self) -> None:
        """Perform the initial (or a re-)login and build the API client."""
        login_manager = Login("http://" + self.host)
        credentials = await self.hass.async_add_executor_job(
            login_manager.authorize, self.username, self.password
        )
        self.api = ApiMethods(credentials, "http://" + self.host)

    async def _async_update_data(self) -> dict:
        if self.api is None:
            await self.async_login()

        try:
            rooms = await self.hass.async_add_executor_job(self.api.get_rooms_list)
            weather = await self.hass.async_add_executor_job(self.api.get_weather)
            scene_active_rooms = await self.hass.async_add_executor_job(
                self._get_scene_active_rooms
            )
        except Exception as err:
            raise UpdateFailed(f"Error communicating with gateway: {err}") from err

        return {"rooms": rooms, "weather": weather, "scene_active_rooms": scene_active_rooms}

    def _get_scene_active_rooms(self) -> dict[str, set]:
        """Ground-truth per-scene room membership, for every
        TRACKED_SCENE_NAME - see this module's change log and
        docs/protocol.md §4e/§4f for why roomstatus alone cannot be
        trusted for this.

        One /api/scene/status call covers isActive for every scene;
        /api/scene/getrooms is only called for scenes that ARE active, to
        keep the common case (most scenes inactive most of the time)
        cheap.
        """
        active_names = {
            s["name"] for s in self.api.get_scene_status().get("scenes", []) if s.get("isActive")
        }
        return {
            scene.value: set(self.api.get_scene_rooms(scene.value)) if scene.value in active_names else set()
            for scene in TRACKED_SCENE_NAMES
        }
