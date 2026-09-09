"""High-level API methods for the Honeywell Smile Connect gateway.

This is a cleaned-up version of the working, live-verified methods from the
original reverse-engineering scaffold. Endpoints marked "verified" have been
tested against a real SCN-10 gateway; others are ports of the generic
HeatApp shape and should be re-verified before relying on them.
"""
# Change log:
# - 2026-09-09: Added client-side enforcement of the Smile App's own UI
#   limits per scene, confirmed directly by the user (not derived from
#   gateway behavior):
#     Boost:   30-120 minutes, raster of 30/60/90/120 (0 not selectable)
#     Party:   1-12 hours (0 not selectable)
#     Leave:   1-12 hours (0 not selectable)
#     Holiday: 1-30 days (0 not selectable)
#   This matters because the gateway itself does NOT enforce these for
#   Holiday - a live test sent raw values up to 100 days and the gateway
#   accepted and echoed all of them without clamping (unlike Boost/Party/
#   Leave, which self-clamp at their scene_max=1.0 fraction). Without a
#   client-side check, target= could silently set a far longer holiday
#   mode than the app itself allows. Enforced only against `target=`
#   (the real-world-unit path) - `duration=` (the raw wire value) still
#   bypasses this for callers who know exactly what they're sending.
# - 2026-09-09: Added a client-side guard to set_scene_rooms() rejecting
#   an empty room_ids list with ValueError before any request is sent.
#   Triggered by a live incident during regression testing: a diagnostic
#   script called set_scene_rooms("Boost", []) to test set_scene()'s new
#   room-assignment guard, and the gateway hung (ReadTimeout) - matching
#   a note already in project learnings ("setrooms mit einer leeren
#   Liste verursacht einen Firmware-Hang unabhaengig von der Kodierung -
#   muss komplett uebersprungen werden") that this call path had not
#   respected. Gateway recovered on its own without a power cycle;
#   Boost's room assignment ([1]) was confirmed intact afterwards. No
#   known safe way to clear a scene's rooms via this endpoint currently
#   exists - treat as a hard restriction, not just a client nicety.
# - 2026-09-09: Confirmed scene/set `duration` semantics via live
#   falsification testing across all four timed scenes (see project
#   learnings for the full investigation):
#     - Boost, Party, Leave: `duration` is a FRACTION of the scene's own
#       scene_max from scene/status (Boost=120min, Party=12h, Leave=12h).
#       Values >1 are silently clamped to 1 (=scene_max) by the gateway,
#       no error returned. Confirmed for Boost via a live countdown
#       (28min actual remaining vs. formula-predicted value); confirmed
#       for Party/Leave via the same clamp pattern (0.5 echoed unchanged,
#       6 clamped to 1). Re-confirmed via the new target= parameter
#       itself (Boost/Party/Leave all landed exactly on the expected
#       value in a live regression run).
#     - Holiday: `duration` is RAW DAYS, not a fraction - sending 15 was
#       echoed back as ~15.0014 (NOT clamped to 1 like the other three).
#       This confirms the original 3-month-old apiMethods.py claim
#       ("Holiday: Tage direkt") was correct for Holiday specifically,
#       even though the same file's claim for Boost ("Minuten direkt")
#       was wrong. The gateway itself has no confirmed ceiling for
#       Holiday (tested up to 100 days, no clamping observed) - see the
#       app-UI-limits entry above for how this is now bounded client-side.
#     - Standby/Towel: no duration parameter is meaningful.
#   The previous set_scene() docstring incorrectly generalized "minutes
#   for Boost, hours for Party/Leave, days for Holiday" as if `duration`
#   were always a direct, real-world value - only true for Holiday.
#   set_scene() gained a `target` parameter that does the correct
#   conversion per scene, while `duration` (raw wire value) still works
#   unchanged for existing callers.
#   Also added a room-assignment guard: activating a scene with no rooms
#   assigned returns success=True from the gateway but isActive silently
#   stays False (confirmed live, matches a note in the 3-month-old
#   apiMethods.py's activateScene() that this rewrite had dropped). Now
#   raises ValueError instead of silently no-op'ing, unless the caller
#   passes room_ids to assign them first.
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

# Duration semantics confirmed live 2026-09-09 (see change log above).
# Native units are what a human would set in the app: minutes for Boost,
# hours for Party/Leave, days for Holiday.
# scene_max from scene/status
SCENE_MAX = {"Boost": 120, "Party": 12, "Leave": 12}
FRACTION_DURATION_SCENES = frozenset(SCENE_MAX)  # Boost, Party, Leave
RAW_DAYS_DURATION_SCENES = frozenset({"Holiday"})
NO_DURATION_SCENES = frozenset({"Standby", "Towel"})

# Smile App UI limits per scene, confirmed directly by the user
# (2026-09-09) - NOT derived from gateway behavior, and in Holiday's case
# NOT enforced by the gateway at all (see change log). "step" is the
# app's selectable raster where one exists (Boost only); None means any
# value in [min, max] is selectable in the app.
SCENE_APP_LIMITS = {
    "Boost":   {"min": 30, "max": 120, "step": 30},    # minutes
    "Party":   {"min": 1,  "max": 12,  "step": None},  # hours
    "Leave":   {"min": 1,  "max": 12,  "step": None},  # hours
    "Holiday": {"min": 1,  "max": 30,  "step": None},  # days
}


def _validate_target_against_app_limits(scene_name: str, target: float) -> None:
    """Raise ValueError if target falls outside the app's own UI range/
    raster for this scene. See SCENE_APP_LIMITS for sources."""
    limits = SCENE_APP_LIMITS.get(scene_name)
    if limits is None:
        return

    if target < limits["min"] or target > limits["max"]:
        raise ValueError(
            f"target={target} is outside the app's UI range for "
            f"'{scene_name}' ({limits['min']}-{limits['max']}, confirmed "
            f"by the user 2026-09-09). The gateway itself may not enforce "
            f"this (confirmed for Holiday: values up to 100 accepted "
            f"without clamping) - this check exists to match real app "
            f"behavior, not a protocol limit."
        )

    step = limits["step"]
    if step is not None:
        offset = target - limits["min"]
        remainder = offset % step
        # tolerate float rounding at either edge of the step
        if remainder > 1e-6 and (step - remainder) > 1e-6:
            raise ValueError(
                f"target={target} is not on the app's raster for "
                f"'{scene_name}' (step={step}, starting at {limits['min']}, "
                f"confirmed by the user 2026-09-09)."
            )


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
    # verified: scene status retrieval, scene room retrieval/assignment.
    # scene activation duration semantics confirmed 2026-09-09 for Boost/
    # Party/Leave (fraction of scene_max) and Holiday (raw days) - see
    # change log. App-UI limits (SCENE_APP_LIMITS) confirmed directly by
    # the user 2026-09-09 and enforced client-side for target=.

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
        """Remaining duration for the scene, in the gateway's raw wire
        format: a fraction of scene_max for Boost/Party/Leave, raw days
        for Holiday. Use SCENE_MAX to convert Boost/Party/Leave results
        into a real-world unit; Holiday's value is already in days.
        """
        params = DefaultApiParams()
        params.scene = scene_name
        result = self._request.request(
            self.base_url + "/api/scene/duration", self.credentials, params
        )
        return result["duration"]

    def set_scene_rooms(self, scene_name: str, room_ids: list) -> dict:
        """Assign rooms to a scene.

        Raises ValueError for an empty room_ids list WITHOUT sending any
        request - the gateway hangs (ReadTimeout, no response at all) on
        setrooms with an empty list regardless of encoding (confirmed
        live, 2026-09-09, during regression testing of set_scene()'s
        room-assignment guard). The gateway recovered on its own without
        a power cycle in that incident, but there is currently no known
        safe way to clear a scene's room assignment via this endpoint -
        treat this as a hard restriction, not just client-side caution.
        """
        if not room_ids:
            raise ValueError(
                "set_scene_rooms() refuses to send an empty room_ids list - "
                "the gateway hangs on setrooms with an empty list "
                "regardless of encoding (confirmed live, 2026-09-09). "
                "No known safe workaround exists yet."
            )
        params = DefaultApiParams()
        params.scene = scene_name
        params.rooms = room_ids
        return self._request.request(
            self.base_url + "/api/scene/setrooms", self.credentials, params
        )

    def set_scene(
        self,
        scene_name: str,
        active: bool,
        duration=None,
        target=None,
        room_ids: list | None = None,
    ) -> dict:
        """Activate/deactivate a scene.

        Two ways to specify duration:
          - `duration`: the raw wire value, passed through unchanged, NOT
            checked against SCENE_APP_LIMITS. Use only if you already
            know the exact wire format for this scene (a fraction for
            Boost/Party/Leave, raw days for Holiday).
          - `target`: a real-world duration in the scene's native unit
            (minutes for Boost, hours for Party/Leave, days for Holiday).
            Validated against SCENE_APP_LIMITS first (raises ValueError
            if outside the app's own min/max/raster - confirmed by the
            user 2026-09-09), then converted internally to the correct
            wire format:
              * Boost/Party/Leave: target / scene_max (a fraction of the
                scene's own max from scene/status). Live-confirmed
                2026-09-09 - see change log.
              * Holiday: target is sent as-is (raw days). Live-confirmed
                2026-09-09 that Holiday does NOT use the fraction formula
                the other three use, and that the gateway itself does NOT
                enforce the app's 30-day limit (accepted up to 100 days
                without clamping in testing) - the SCENE_APP_LIMITS check
                is the only thing enforcing this now.
            Ignored for Standby/Towel.

        If both `duration` and `target` are given, `duration` wins (no
        warning raised - caller error).

        Raises ValueError if `target` is given for a scene with no known
        duration mode, or if `target` is outside that scene's app-UI
        limits.

        Room assignment: activating a scene with no rooms assigned
        returns success=True from the gateway, but isActive silently
        stays False (confirmed live, 2026-09-09). Pass `room_ids` to
        assign rooms before activating; if `active=True`, no `room_ids`
        is given, and the scene currently has no rooms assigned, this
        raises ValueError instead of silently no-op'ing.

        Passing `room_ids=[]` is rejected by set_scene_rooms() before any
        request is sent - see its docstring for why.
        """
        if room_ids is not None:
            self.set_scene_rooms(scene_name, room_ids)
        if active and not self.get_scene_rooms(scene_name):
            raise ValueError(
                f"Scene '{scene_name}' has no rooms assigned - pass room_ids "
                f"to assign them first. Without rooms, the gateway returns "
                f"success=True but isActive stays False (confirmed live, "
                f"2026-09-09)."
            )

        params = DefaultApiParams()
        params.scene = scene_name
        params.active = active

        if scene_name in NO_DURATION_SCENES:
            params.duration = 1
        elif duration is not None:
            params.duration = duration
        elif target is not None:
            _validate_target_against_app_limits(scene_name, target)
            if scene_name in FRACTION_DURATION_SCENES:
                params.duration = target / SCENE_MAX[scene_name]
            elif scene_name in RAW_DAYS_DURATION_SCENES:
                params.duration = target
            else:
                raise ValueError(
                    f"No known duration mode for scene '{scene_name}'")
        else:
            params.duration = None

        return self._request.request(
            self.base_url + "/api/scene/set", self.credentials, params
        )
