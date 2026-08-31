# Change log:
# - 2026-08-30: Initial version. Created alongside the fix for a real
#   production bug: remove_member_from_scene() calling set_scene_rooms()
#   with an empty room list caused the gateway's firmware to hang/timeout
#   (10s ReadTimeout, observed twice with two different wire encodings of
#   the empty value - see api_request.py's and this module's own change
#   logs). These tests lock in the fix: set_scene_rooms() must be skipped
#   entirely when the resulting room list would be empty.
"""Tests for honeywell_smileconnect.api.scene_manager.SceneManager.

Uses a mocked ApiMethods throughout - scene_manager.py has no HA
dependency and no network calls of its own, so this is pure interaction-
based testing (asserting which underlying api.* methods get called with
what arguments, not real HTTP).
"""
from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.honeywell_smileconnect.api.scene_manager import SceneManager


def _make_api(scene_rooms: list, is_active: bool) -> MagicMock:
    api = MagicMock()
    api.get_scene_rooms.return_value = list(scene_rooms)
    api.get_specific_scene.return_value = {"isActive": is_active}
    api.get_scene_duration.return_value = 30
    return api


class TestRemoveMemberFromSceneSkipsEmptySetrooms:
    """The core regression test: removing the last/only room from a scene
    must NOT call set_scene_rooms() with an empty list - that call is
    what hung the real gateway. set_scene(active=False) alone is
    sufficient to deactivate the scene.
    """

    def test_does_not_call_set_scene_rooms_when_list_becomes_empty(self):
        api = _make_api(scene_rooms=[1], is_active=True)
        manager = SceneManager(api)

        manager.remove_member_from_scene(1, "Standby")

        api.set_scene_rooms.assert_not_called()

    def test_still_deactivates_the_scene(self):
        api = _make_api(scene_rooms=[1], is_active=True)
        manager = SceneManager(api)

        manager.remove_member_from_scene(1, "Standby")

        # Called twice with active=False: once as the pre-emptive
        # deactivate (scene was already active), once more at the end
        # reflecting the now-empty room list (len(remaining_rooms) > 0
        # is False).
        assert api.set_scene.call_count == 2
        for call in api.set_scene.call_args_list:
            assert call.kwargs["active"] is False

    def test_does_call_set_scene_rooms_when_other_rooms_remain(self):
        # Multi-room scenario (not this project's current hardware, but
        # the logic must stay correct for it): removing one of several
        # rooms leaves a non-empty list, which DOES need to be sent.
        api = _make_api(scene_rooms=[1, 2, 3], is_active=True)
        manager = SceneManager(api)

        manager.remove_member_from_scene(1, "Standby")

        api.set_scene_rooms.assert_called_once_with("Standby", [2, 3])

    def test_no_op_if_room_was_never_a_member(self):
        api = _make_api(scene_rooms=[2, 3], is_active=True)
        manager = SceneManager(api)

        manager.remove_member_from_scene(1, "Standby")

        api.set_scene_rooms.assert_not_called()
        api.set_scene.assert_not_called()


class TestAddMemberToScene:
    def test_calls_set_scene_rooms_with_new_member_appended(self):
        api = _make_api(scene_rooms=[], is_active=False)
        manager = SceneManager(api)

        manager.add_member_to_scene(1, "Boost")

        api.set_scene_rooms.assert_called_once_with("Boost", [1])

    def test_activates_the_scene(self):
        api = _make_api(scene_rooms=[], is_active=False)
        manager = SceneManager(api)

        manager.add_member_to_scene(1, "Boost")

        last_call = api.set_scene.call_args_list[-1]
        assert last_call.kwargs["active"] is True

    def test_no_duplicate_setrooms_call_if_already_a_member(self):
        api = _make_api(scene_rooms=[1], is_active=False)
        manager = SceneManager(api)

        manager.add_member_to_scene(1, "Boost")

        api.set_scene_rooms.assert_not_called()
