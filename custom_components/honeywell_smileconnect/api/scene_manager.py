"""Helper for adding/removing a single room from a scene.

Ported and cleaned up from the original reverse-engineering scaffold.
"""
# Change log:
# - 2026-09-01: Fixed add_member_to_scene() root-cause bug behind presets
#   appearing to "revert" after selection in HA: it resent whatever
#   get_scene_duration() reported for the scene - which is 0 (or
#   meaningless noise) while inactive - and set_scene(active=True,
#   duration=0) is silently rejected by the gateway (success:true, but
#   isActive never flips). Now uses the confirmed, factor-corrected
#   send-values from const.SCENE_ACTIVATION_DURATION instead. Full
#   investigation (including the per-scene multiplicative factors and
#   caps) in docs/protocol.md §4d. remove_member_from_scene()'s own
#   get_scene_duration() call is deliberately left untouched - only
#   ACTIVATION with duration=0 was ever shown to be silently rejected,
#   not deactivation.
# - 2026-08-30 (b): Added active poll-and-verify after every write
#   (_wait_for_scene_active_state), matching the pattern already
#   established and documented in scripts/manual_check_roomstatus_via_app.py
#   etc. - now applied to PRODUCTION code for the first time. Root cause:
#   climate.py has ZERO local/optimistic state - hvac_mode/preset_mode are
#   computed live from coordinator.data on every access, and climate.py
#   calls coordinator.async_request_refresh() immediately after every
#   write. If the gateway hasn't yet internally propagated the scene
#   change by the time that immediate refresh fires, the re-fetched data
#   is stale, and the HA UI briefly shows the OLD state (reported by the
#   user as "mode jumps back to Off/Auto, only correcting itself later").
#   This adds a bounded wait (up to ~10s) INSIDE the write path itself,
#   confirming via /api/scene/status that the change actually took effect
#   before returning control to climate.py's subsequent refresh call -
#   so that refresh is now much more likely to see already-correct data.
#   Does not raise/fail if verification times out (logs a warning and
#   proceeds) - HA's regular 30s poll cycle will still eventually pick up
#   the change even if this bounded wait wasn't long enough.
#   NOTE: this verifies via /api/scene/status's isActive flag, NOT
#   /api/room/list's roomstatus field directly (which is what climate.py
#   actually reads) - if the user still sees stale display after this fix
#   despite scene/status confirming quickly, that would point to a
#   SEPARATE staleness/caching quirk specific to the roomstatus field on
#   the gateway, needing further investigation (see CLAUDE.md).
# - 2026-08-30 (a): remove_member_from_scene() no longer calls
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

import logging
import time

from ..const import SCENE_ACTIVATION_DURATION
from .api_methods import ApiMethods

_LOGGER = logging.getLogger(__name__)

# Matches the pattern already proven out in scripts/manual_check_*.py:
# up to ~10s total (5 waits x 2s) before giving up and proceeding anyway.
_VERIFY_ATTEMPTS = 6
_VERIFY_INTERVAL_SECONDS = 2.0


class SceneManager:
    def __init__(self, api: ApiMethods) -> None:
        self.api = api

    def is_member_of_scene(self, room_id, scene_name: str) -> bool:
        return room_id in self.api.get_scene_rooms(scene_name)

    def is_scene_active(self, scene_name: str) -> bool:
        return self.api.get_specific_scene(scene_name)["isActive"]

    def _wait_for_scene_active_state(self, scene_name: str, want_active: bool) -> bool:
        """Poll /api/scene/status (via is_scene_active) until scene_name's
        isActive matches want_active, or give up after _VERIFY_ATTEMPTS.
        Never raises - a timeout just means the caller's subsequent
        coordinator refresh might still show slightly-stale data, which
        HA's regular poll cycle will self-correct shortly after anyway.
        """
        for attempt in range(_VERIFY_ATTEMPTS):
            if self.is_scene_active(scene_name) == want_active:
                return True
            if attempt < _VERIFY_ATTEMPTS - 1:
                time.sleep(_VERIFY_INTERVAL_SECONDS)

        confirmed = self.is_scene_active(scene_name) == want_active
        if not confirmed:
            _LOGGER.warning(
                "Scene '%s' did not reach active=%s on the gateway within "
                "%d attempts (~%ds) - proceeding anyway; a later regular "
                "poll cycle should still pick up the change once the "
                "gateway catches up.",
                scene_name,
                want_active,
                _VERIFY_ATTEMPTS,
                int((_VERIFY_ATTEMPTS - 1) * _VERIFY_INTERVAL_SECONDS),
            )
        return confirmed

    def add_member_to_scene(self, room_id, scene_name: str) -> None:
        rooms = self.api.get_scene_rooms(scene_name)
        if room_id not in rooms:
            rooms.append(room_id)
            self.api.set_scene_rooms(scene_name, rooms)

        # NOT self.api.get_scene_duration(scene_name) - that call is
        # confirmed unreliable in every tested state (returns 0 or
        # meaningless noise) and resending it caused activation to be
        # silently rejected by the gateway. See this module's change log
        # and docs/protocol.md §4d.
        duration = SCENE_ACTIVATION_DURATION[scene_name]
        if self.is_scene_active(scene_name):
            # Re-trigger so the new member picks up the active scene state.
            self.api.set_scene(scene_name, active=False, duration=duration)
        self.api.set_scene(scene_name, active=True, duration=duration)

        self._wait_for_scene_active_state(scene_name, want_active=True)

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

        want_active = len(remaining_rooms) > 0
        self.api.set_scene(scene_name, active=want_active, duration=duration)

        self._wait_for_scene_active_state(scene_name, want_active=want_active)
