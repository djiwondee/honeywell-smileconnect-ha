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
    """Polls room list + scene status and exposes it to entities."""

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

    async def _async_update_data(self) -> list[dict]:
        if self.api is None:
            await self.async_login()

        try:
            return await self.hass.async_add_executor_job(self.api.get_rooms_list)
        except Exception as err:  # noqa: BLE001 - surfaced via UpdateFailed
            raise UpdateFailed(f"Error communicating with gateway: {err}") from err
