"""High-level API methods for the Honeywell Smile Connect gateway.

This is a cleaned-up version of the working, live-verified methods from the
original reverse-engineering scaffold. Endpoints marked "verified" have been
tested against a real SCN-10 gateway; others are ports of the generic
HeatApp shape and should be re-verified before relying on them.
"""
from __future__ import annotations

from .apiRequest import ApiRequest
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

    def get_switching_times(self, room_name: str, room_id) -> dict:
        params = DefaultApiParams()
        params.roomid = room_id
        params.roomname = room_name
        return self._request.request(
            self.base_url + "/api/room/switchingtimes/get2", self.credentials, params
        )

    def set_switching_times(self, room_name: str, room_id, from_times, to_times, types) -> dict:
        params = DefaultApiParams()
        params.roomid = room_id
        params.roomname = room_name
        params.from_ = from_times  # comma-joined string expected by the gateway
        params.to = to_times
        params.type = types
        return self._request.request(
            self.base_url + "/api/room/switchingtimes/set2", self.credentials, params
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
