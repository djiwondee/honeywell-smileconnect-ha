"""Slow, independent DataUpdateCoordinator for room switching times.

Separate from SmileConnectCoordinator (coordinator.py) for two reasons:

1. **Cadence.** Switching times change on the order of weeks. Polling them
   at the main 30s cycle would cost one authenticated /get2 call per room
   every 30s for data that is almost never different.
2. **Failure isolation.** coordinator.py's documented contract is "a
   failure here fails the whole poll cycle" - folding schedule reads into
   it would mean one flaky /get2 marks every climate, weather and
   preset-duration entity unavailable.

Unlike ping_coordinator.py, this endpoint IS authenticated, so this
coordinator shares the main coordinator's logged-in ApiMethods session
rather than building its own (a second login would mean a second gateway
session with its own reqcount). It never touches `api` directly: every
call goes through SmileConnectCoordinator.async_api_call(), which holds
the shared lock serialising all authenticated access - see that module's
change log for why that matters.
"""
# Change log:
# - 2026-09-21: Initial version. Backs the per-room schedule sensor
#   (sensor.py) that in turn backs the Lovelace schedule card, so that a
#   schedule edited in the Smile App appears in Home Assistant on its own
#   instead of only when someone calls the get_schedule_room Action.
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .coordinator import SmileConnectCoordinator
from .switching_times import switching_times_to_weekday_dict

_LOGGER = logging.getLogger(__name__)


class SmileConnectScheduleCoordinator(DataUpdateCoordinator):
    """Polls every room's weekly switching times.

    `data` is keyed by room id (as a string, so it survives JSON round
    trips and does not depend on whether the gateway reports ids as int or
    str):

        {"1": {"switchingtimes": [ ...raw day-major list, may contain
                                   None... ],
               "schedule": {"monday": [{"from","to","type"}, ...], ...},
               "slots_per_day": 3},
         ...}

    `slots_per_day` is always derived from the live array's own length -
    never MAX_SLOTS_PER_DAY, never a hardcoded 3. The gateway rejects a
    write whose array width does not match the room's own configured
    width, so this value is what any editor must respect (see
    docs/switching-times-api.md, point 5).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        main_coordinator: SmileConnectCoordinator,
        interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="honeywell_smileconnect_schedule",
            update_interval=timedelta(seconds=interval),
        )
        self._main = main_coordinator

    async def _async_update_data(self) -> dict:
        # The room list comes from the main coordinator rather than a
        # second /api/room/list call of our own. If it has not produced
        # data yet there is simply nothing to poll - return empty rather
        # than raising, so this coordinator does not report a failure for
        # something that is not one.
        rooms = (self._main.data or {}).get("rooms", [])
        if not rooms:
            return {}

        result: dict[str, dict] = {}
        try:
            for room in rooms:
                room_id = room["data"]["id"]
                response = await self._main.async_api_call(
                    self._main.api.get_switching_times, room["name"], room_id
                )
                switchingtimes = response["switchingtimes"]
                # switching_times_to_weekday_dict() is pure and fast, so it
                # runs here on the event loop rather than inside the
                # executor job - the lock is released as early as possible.
                result[str(room_id)] = {
                    "switchingtimes": switchingtimes,
                    "schedule": switching_times_to_weekday_dict(switchingtimes),
                    "slots_per_day": len(switchingtimes) // 7,
                }
        except Exception as err:
            raise UpdateFailed(f"Error reading switching times: {err}") from err

        return result
