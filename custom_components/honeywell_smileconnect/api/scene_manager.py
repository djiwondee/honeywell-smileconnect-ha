"""Helper for adding/removing a single room from a scene.

Ported and cleaned up from the original reverse-engineering scaffold.
NOTE: `setrooms` behaviour prior to scene activation is still marked as
untested in docs/protocol.md - validate carefully before relying on this in
a multi-room installation.
"""
from __future__ import annotations

from .apiMethods import ApiMethods


class SceneManager:
    def __init__(self, api: ApiMethods) -> None:
        self.api = api

    def is_member_of_scene(self, room_id, scene_name: str) -> bool:
        return room_id in self.api.get_scene_rooms(scene_name)

    def is_scene_active(self, scene_name: str) -> bool:
        return self.api.get_specific_scene(scene_name)["isActive"]

    def add_member_to_scene(self, room_id, scene_name: str) -> None:
        rooms = self.api.get_scene_rooms(scene_name)
        if room_id not in rooms:
            rooms.append(room_id)
            self.api.set_scene_rooms(scene_name, rooms)

        duration = self.api.get_scene_duration(scene_name)
        if self.is_scene_active(scene_name):
            # Re-trigger so the new member picks up the active scene state.
            self.api.set_scene(scene_name, active=False, duration=duration)
        self.api.set_scene(scene_name, active=True, duration=duration)

    def remove_member_from_scene(self, room_id, scene_name: str) -> None:
        rooms = self.api.get_scene_rooms(scene_name)
        if room_id not in rooms:
            return

        rooms = [r for r in rooms if r != room_id]
        duration = self.api.get_scene_duration(scene_name)

        if self.is_scene_active(scene_name):
            self.api.set_scene(scene_name, active=False, duration=duration)

        self.api.set_scene_rooms(scene_name, rooms)
        self.api.set_scene(scene_name, active=len(rooms) > 0, duration=duration)
