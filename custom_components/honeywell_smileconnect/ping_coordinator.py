"""Independent DataUpdateCoordinator for the unauthenticated /api/ping
endpoint.

Deliberately separate from SmileConnectCoordinator (coordinator.py): the
whole point of polling /api/ping is to report gateway reachability even
when login/session is broken, so it must not share state, credentials, or
failure modes with the authenticated coordinator. A failed authenticated
login must never make this coordinator's connectivity sensor look wrong.
"""
# Change log:
# - 2026-08-27: Initial version, configurable poll interval (default 15s,
#   see const.DEFAULT_PING_INTERVAL) via the options flow.
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ping as ping_api

_LOGGER = logging.getLogger(__name__)


class SmileConnectPingCoordinator(DataUpdateCoordinator):
    """Polls /api/ping independently of authentication state.

    `data` is the raw parsed /api/ping JSON response, e.g.:
        {"success": true, "uniqueid": "...", "configured": true,
         "remoteAddress": "...", "performance": 0.06, ...}
    """

    def __init__(self, hass: HomeAssistant, host: str, interval: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="honeywell_smileconnect_ping",
            update_interval=timedelta(seconds=interval),
        )
        self.base_url = f"http://{host}"

    async def _async_update_data(self) -> dict:
        try:
            return await self.hass.async_add_executor_job(ping_api.ping, self.base_url)
        except Exception as err:  # noqa: BLE001
            # A failed ping is meaningful data (gateway unreachable), but
            # UpdateFailed is still the correct HA mechanism here: it marks
            # the connectivity/response-time entities "unavailable" rather
            # than leaving them showing stale or misleading prior state.
            raise UpdateFailed(f"Gateway unreachable via /api/ping: {err}") from err
