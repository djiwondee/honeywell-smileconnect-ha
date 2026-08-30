# Change log:
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

from .api.apiMethods import ApiMethods
from .api.login import Login

_LOGGER = logging.getLogger(__name__)


class SmileConnectCoordinator(DataUpdateCoordinator):
    """Polls room list + weather in one cycle and exposes it to entities.

    `data` is a dict shaped as:
        {"rooms": [...room dicts as returned by ApiMethods.get_rooms_list()...],
         "weather": {...raw /api/weather response...}}
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
        except Exception as err:  # noqa: BLE001 - surfaced via UpdateFailed
            raise UpdateFailed(f"Error communicating with gateway: {err}") from err

        return {"rooms": rooms, "weather": weather}
