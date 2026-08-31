"""Helper for adding/removing a single room from a scene.

Ported and cleaned up from the original reverse-engineering scaffold.
"""
# Change log:
# - 2026-08-30: remove_member_from_scene() no longer calls
#   set_scene_rooms() when the resulting room list would be EMPTY. Two
#   consecutive live tests against the real gateway both produced a
#   10-second ReadTimeout on /api/scene/setrooms when removing the
#   last/only room from a scene - first with an (incorrectly) empty wire
#   value, then again after fixing that to the gateway-JS-correct literal
#   "undefined" (see api_request.py's change log). Since the SAME timeout
#   persisted across two different encodings of the same empty-list
#   request, the gateway's firmware itself appears unable to handle
#   /api/scene/setrooms with zero rooms at all - this is not a wire-format
#   problem. The fix sidesteps the call entirely for this case:
#   set_scene(active=False) (confirmed successful in the same failing log)
#   is apparently sufficient on its own to deactivate a scene; there's no
#   need to also clear room membership down to zero. Room membership only
#   ever needs updating via set_scene_rooms() when it's non-empty (both
#   here and in add_member_to_scene(), which always ADDS a room and so
#   never produces an empty list in the first place).
from __future__ import annotations

from .api_methods import ApiMethods


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

        remaining_rooms = [r for r in rooms if r != room_id]
        duration = self.api.get_scene_duration(scene_name)

        if self.is_scene_active(scene_name):
            self.api.set_scene(scene_name, active=False, duration=duration)

        if remaining_rooms:
            self.api.set_scene_rooms(scene_name, remaining_rooms)
        # else: deliberately skip set_scene_rooms() - see module change
        # log. Calling it with an empty list appears to hang the
        # gateway's firmware regardless of wire encoding; set_scene(
        # active=False) above already fully deactivates the scene.

        self.api.set_scene(scene_name, active=len(remaining_rooms) > 0, duration=duration)
