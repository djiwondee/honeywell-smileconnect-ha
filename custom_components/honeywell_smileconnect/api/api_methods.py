"""High-level API methods for the Honeywell Smile Connect gateway.

This is a cleaned-up version of the working, live-verified methods from the
original reverse-engineering scaffold. Endpoints marked "verified" have been
tested against a real SCN-10 gateway; others are ports of the generic
HeatApp shape and should be re-verified before relying on them.
"""
# Change log:
# - 2026-09-09: Rewrote set_switching_times() after live-verifying
#   /api/room/switchingtimes/set2 via a mitmproxy capture of the real
#   Smile App request (see project learnings for the full investigation):
#     1. Wire field name is "from", not "from_". The previous version set
#        `params.from_ = from_times` - since `from` is a reserved Python
#        word, that produced the Python attribute name "from_", which
#        went straight onto the wire unchanged. The gateway never
#        received a `from` field at all, causing
#        `success:false, "The input format is invalid: 1"` on every call.
#        Fixed via `setattr(params, "from", ...)`, since `params.from = x`
#        is a syntax error but setattr() accepts any string key, and
#        ApiRequest.request() reads params via vars(), which picks up
#        setattr-assigned keys identically to normal attributes - no
#        change needed in api_request.py.
#     2. Hours must be sent WITHOUT a leading zero (e.g. "4:30"), even
#        though get2 returns them WITH one ("04:30"). Confirmed via the
#        real Smile App request, which uses unpadded hours throughout.
#     3. Slots within a day must be filled contiguously from slot 1
#        upward - confirmed live that the gateway does NOT reject a gap
#        (e.g. filling slot 3 while slot 2 is still empty). Instead it
#        silently shifts the value to the first free slot in that day AND
#        drops the sent `type`, replacing it with the day's existing
#        first slot's type. Since the gateway gives no error for this,
#        _validate_switching_times() enforces it client-side before a
#        request is ever built.
#   Signature changed from three separate from_times/to_times/types
#   strings to a single `switchingtimes` list in the exact shape
#   get_switching_times() already returns (list of {"from","to","type"}
#   dicts or None per slot) - this endpoint has no existing callers yet
#   (no HA entity uses it), so this is a clean break, not a compatibility
#   concern. slots_per_day is derived from the list length rather than
#   hardcoded to 3, since the gateway protocol never declares this count
#   explicitly (see learnings) and it may differ on other hardware.
# - 2026-08-30: Renamed from apiMethods.py to api_methods.py for PEP 8 /
#   ruff N999 compliance (module names must be snake_case). The ApiMethods
#   class name itself is unchanged - only the file/import path changed.
#   All internal and external references updated in the same commit; see
#   CLAUDE.md for the full list of touched files.
from __future__ import annotations

from .api_request import ApiRequest
from .credentials import Credentials
from .default_params import DefaultApiParams


class ApiMethods:
    """Thin wrapper turning gateway endpoints into Python calls."""

    def __init__(self, credentials: Credentials, base_url: str) -> None:
        self.credentials = credentials
        self.base_url = base_url
        self._request = ApiRequest()

    # -- rooms ----------------------------------------------------------
    # verified: room list retrieval

    def get_raw_rooms(self) -> dict:
        return self._request.request(
            self.base_url + "/api/room/list", self.credentials, DefaultApiParams()
        )

    def get_rooms_list(self) -> list[dict]:
        raw = self.get_raw_rooms()
        results = []
        for group in raw.get("groups", []):
            for room in group.get("rooms", []):
                results.append({"name": room["name"], "data": room})
        return results

    def get_specific_room(self, room_id) -> dict | None:
        for room in self.get_rooms_list():
            if room["data"]["id"] == room_id:
                return room
        return None

    # verified: temperature setting
    def set_temperature(self, temperature: float, room_id) -> dict:
        params = DefaultApiParams()
        params.roomid = room_id
        params.change_mode = 0
        params.temperature = temperature
        return self._request.request(
            self.base_url + "/api/room/settemperature", self.credentials, params
        )

    # verified: switching-times (schedule) retrieval
    def get_switching_times(self, room_name: str, room_id) -> dict:
        params = DefaultApiParams()
        params.roomid = room_id
        params.roomname = room_name
        return self._request.request(
            self.base_url + "/api/room/switchingtimes/get2", self.credentials, params
        )

    # verified: switching-times (schedule) writing
    def set_switching_times(
        self, room_name: str, room_id, switchingtimes: list[dict | None]
    ) -> dict:
        """Write a room's full switching-time schedule.

        `switchingtimes` must be in the SAME shape get_switching_times()
        returns under the `"switchingtimes"` key: a flat, day-major list
        (length = 7 * slots_per_day, slots_per_day inferred from the list
        itself), each entry either `None` (empty slot) or
        `{"from": "HH:MM", "to": "HH:MM", "type": "H"/"L"}`.

        This always writes the FULL schedule - the gateway has no partial-
        update endpoint, so any slot you want to keep must be included
        unchanged (typically: read via get_switching_times(), modify one
        entry, pass the whole list back).

        Raises ValueError if the schedule violates the gateway's
        undeclared-but-confirmed contiguous-slot rule (see
        _validate_switching_times) - the gateway itself does not reject
        this, it silently corrupts the write instead, so we catch it here.
        """
        self._validate_switching_times(switchingtimes)

        froms, tos, types = [], [], []
        for slot in switchingtimes:
            if slot is None:
                froms.append("")
                tos.append("")
                types.append("")
            else:
                froms.append(self._strip_leading_zero_hour(slot["from"]))
                tos.append(self._strip_leading_zero_hour(slot["to"]))
                types.append(slot["type"])

        params = DefaultApiParams()
        params.roomid = room_id
        params.roomname = room_name
        # "from" is a reserved word - cannot be a Python attribute name via
        # dot notation (params.from = ... is a SyntaxError). setattr()
        # accepts any string key, and ApiRequest.request() reads params via
        # vars(), which picks up setattr-assigned keys identically to
        # normal attributes - confirmed live, see change log above.
        setattr(params, "from", ",".join(froms))
        params.to = ",".join(tos)
        params.type = ",".join(types)
        return self._request.request(
            self.base_url + "/api/room/switchingtimes/set2", self.credentials, params
        )

    @staticmethod
    def _strip_leading_zero_hour(time_str: str) -> str:
        """"04:30" -> "4:30", "14:50" unchanged, "06:00" -> "6:00".

        Confirmed live via mitmproxy capture of the real Smile App
        request: set2 expects hours without a leading zero, even though
        get2 returns them zero-padded.
        """
        hour, _, minute = time_str.partition(":")
        if len(hour) == 2 and hour.startswith("0"):
            hour = hour[1]
        return f"{hour}:{minute}"

    @staticmethod
    def _validate_switching_times(switchingtimes: list) -> None:
        """Enforce the gateway's contiguous-slot rule client-side.

        Confirmed live: the gateway does not reject a gap (e.g. slot 3
        filled while slot 2 is still empty on the same day). Instead it
        silently shifts the value to the first free slot in that day AND
        drops the sent `type`, replacing it with the day's existing first
        slot's type - a silent data-corrupting write, not a clean error.
        Since the protocol never declares slots_per_day explicitly, it is
        derived from the list length (must be a multiple of 7) rather than
        hardcoded to 3.
        """
        if len(switchingtimes) % 7 != 0:
            raise ValueError(
                f"switchingtimes length ({len(switchingtimes)}) is not a "
                "multiple of 7 - expected a day-major list (7 days x N "
                "slots/day)."
            )
        slots_per_day = len(switchingtimes) // 7
        for day in range(7):
            day_slots = switchingtimes[day *
                                       slots_per_day: (day + 1) * slots_per_day]
            seen_gap = False
            for slot_idx, slot in enumerate(day_slots):
                if slot is None:
                    seen_gap = True
                elif seen_gap:
                    raise ValueError(
                        f"Day {day}, slot {slot_idx + 1}: slot is filled after "
                        "an earlier empty slot on the same day. The gateway "
                        "does not reject this - it silently reorders the "
                        "value and drops the 'type' field. Fill slots "
                        "contiguously from slot 1 upward."
                    )

    # -- weather / system -------------------------------------------------

    def get_weather(self) -> dict:
        return self._request.request(
            self.base_url + "/api/weather", self.credentials, DefaultApiParams()
        )

    def get_system_state(self) -> dict:
        return self._request.request(
            self.base_url + "/api/systemstate", self.credentials, DefaultApiParams()
        )

    def get_portal_data(self) -> dict:
        return self._request.request(
            self.base_url + "/api/portal/access/data", self.credentials, DefaultApiParams()
        )

    def get_users_list(self) -> dict:
        return self._request.request(
            self.base_url + "/api/user/list", self.credentials, DefaultApiParams()
        )

    # -- scenes -----------------------------------------------------------
    # verified: scene status retrieval, scene activation (Boost/Party/Leave/
    # Holiday/Standby)

    def get_scene_status(self) -> dict:
        return self._request.request(
            self.base_url + "/api/scene/status", self.credentials, DefaultApiParams()
        )

    def get_specific_scene(self, scene_name: str) -> dict:
        for scene in self.get_scene_status().get("scenes", []):
            if scene["name"] == scene_name:
                return scene
        raise ValueError(f"Scene '{scene_name}' does not exist")

    def get_scene_rooms(self, scene_name: str) -> list:
        params = DefaultApiParams()
        params.scene = scene_name
        result = self._request.request(
            self.base_url + "/api/scene/getrooms", self.credentials, params
        )
        return result["rooms"]

    def get_scene_duration(self, scene_name: str):
        params = DefaultApiParams()
        params.scene = scene_name
        result = self._request.request(
            self.base_url + "/api/scene/duration", self.credentials, params
        )
        return result["duration"]

    def set_scene_rooms(self, scene_name: str, room_ids: list) -> dict:
        params = DefaultApiParams()
        params.scene = scene_name
        params.rooms = room_ids
        return self._request.request(
            self.base_url + "/api/scene/setrooms", self.credentials, params
        )

    def set_scene(self, scene_name: str, active: bool, duration=None) -> dict:
        """Activate/deactivate a scene.

        `duration` semantics depend on the scene (minutes for Boost, hours
        for Party/Leave, days for Holiday, ignored for Standby) - see
        docs/protocol.md.
        """
        params = DefaultApiParams()
        params.scene = scene_name
        params.active = active
        params.duration = 1 if scene_name == "Standby" else duration
        return self._request.request(
            self.base_url + "/api/scene/set", self.credentials, params
        )
