# Change log:
# - 2026-08-30 (c): Fixed the actual bug the user spotted: after
#   deactivating Standby, the script blindly slept 3 seconds and then
#   proceeded WITHOUT verifying the deactivation actually took effect, AND
#   without re-fetching the room - it kept using stale desiredTemperature/
#   bounds data captured before the mode change. This produced a false
#   "MISMATCH" result in an earlier run (Standby was very likely still
#   active when the temperature was set, unrelated to decimal notation at
#   all). Replaced the blind sleep with an active poll-and-verify loop
#   (wait_for_roomstatus()), and the room data used for computing the test
#   value is now freshly re-fetched AFTER confirming the mode change took
#   effect, not reused from before it.
# - 2026-08-30 (b): Deactivates Standby before testing (temperature
#   changes are rejected while Standby is active - confirmed against the
#   real Smile App), restores it afterward. Uses scheduleTempMin/
#   scheduleTempMax (confirmed real bounds: 12-25) instead of the
#   unreliable minTemperature/maxTemperature fields.
# - 2026-08-30 (a): Initial version. Tests whether /api/room/settemperature
#   correctly accepts dot-notation decimal values (e.g. 20.5) as sent by
#   our existing api_methods.set_temperature().
#   Only 0.5-step granularity is tested (not 0.1) - the Smile App itself
#   only supports 0.5 steps, and so does this integration's
#   target_temperature_step in climate.py.
"""Manual diagnostic: verify decimal (0.5-step) temperature values are
correctly transmitted and interpreted by /api/room/settemperature.

Run this directly in the dev container terminal:

    python3 scripts/manual_check_decimal_temperature.py

This DOES write to your real system:
  - Briefly deactivates Standby if it's currently active (temperature
    changes are rejected while Standby is on), ACTIVELY VERIFIES the
    deactivation took effect (polls, does not just sleep-and-hope), then
    restores Standby afterward if it was on.
  - Briefly changes a room's setpoint by +/-0.5C, then offers to restore
    the original value at the end.
Low risk, easily reversible, but not purely read-only. Confirm the room/
values shown before proceeding at each prompt.
"""
from __future__ import annotations

import getpass
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "custom_components"))

from honeywell_smileconnect.api.api_methods import ApiMethods  # noqa: E402
from honeywell_smileconnect.api.login import Login  # noqa: E402
from honeywell_smileconnect.api.scene_manager import SceneManager  # noqa: E402
from honeywell_smileconnect.const import ROOM_STATUS_STANDBY, SceneName  # noqa: E402

# Confirmed by the user against the real Smile App: these are the actual
# selectable min/max bounds, used as a fallback if scheduleTempMin/
# scheduleTempMax are ever missing from a room's data.
FALLBACK_MIN_TEMP = 12
FALLBACK_MAX_TEMP = 25

POLL_ATTEMPTS = 6
POLL_INTERVAL_SECONDS = 2


def fetch_room(api: ApiMethods, room_id) -> dict:
    rooms = api.get_rooms_list()
    return next(r for r in rooms if r["data"]["id"] == room_id)


def wait_for_standby_state(api: ApiMethods, room_id, *, want_active: bool) -> dict | None:
    """Poll until the room's roomstatus reflects the desired Standby
    state, instead of blindly sleeping and hoping. Returns the final,
    freshly-fetched room dict if the desired state was reached within
    POLL_ATTEMPTS, or None if it never did (caller must handle that).
    """
    for attempt in range(1, POLL_ATTEMPTS + 1):
        time.sleep(POLL_INTERVAL_SECONDS)
        room = fetch_room(api, room_id)
        is_standby = room["data"].get("roomstatus") == ROOM_STATUS_STANDBY
        print(f"  [poll {attempt}/{POLL_ATTEMPTS}] roomstatus={room['data'].get('roomstatus')} "
              f"(Standby active={is_standby})")
        if is_standby == want_active:
            return room
    return None


def main() -> None:
    host = input("Gateway host/IP [192.168.1.132]: ").strip() or "192.168.1.132"
    username = input("Username: ").strip()
    password = getpass.getpass("Password (hidden): ")
    base_url = f"http://{host}"

    print(f"\nLogging in to {base_url} ...")
    login = Login(base_url)
    credentials = login.authorize(username, password)
    print("Login successful.\n")

    api = ApiMethods(credentials, base_url)
    scene_manager = SceneManager(api)

    rooms = api.get_rooms_list()
    if not rooms:
        print("No rooms found - nothing to test.")
        return

    print("Rooms found:")
    for i, room in enumerate(rooms):
        current = room["data"].get("desiredTemperature")
        status = room["data"].get("roomstatus")
        print(f"  [{i}] {room['name']} (id={room['data']['id']}, desiredTemperature={current}, roomstatus={status})")

    if len(rooms) == 1:
        room_idx = 0
    else:
        room_idx = int(input(f"Which room to test? [0-{len(rooms) - 1}]: ").strip())

    room = rooms[room_idx]
    room_id = room["data"]["id"]
    room_name = room["name"]
    was_standby_active = room["data"].get("roomstatus") == ROOM_STATUS_STANDBY

    if was_standby_active:
        print(f"\n{room_name} is currently in Standby - temperature changes are")
        print("rejected while Standby is active (confirmed against the real Smile")
        print("App). Deactivating Standby for this test; will restore it afterward.")
        answer = input("Proceed with deactivating Standby? [Enter] = yes, anything else = abort: ").strip()
        if answer:
            print("Aborted, nothing changed.")
            return

        scene_manager.remove_member_from_scene(room_id, SceneName.STANDBY.value)
        print("Verifying Standby actually deactivated (polling, not just waiting) ...")
        room = wait_for_standby_state(api, room_id, want_active=False)
        if room is None:
            print("\nFAILED: Standby did not deactivate within the poll window.")
            print("Aborting before touching temperature - please check the room's")
            print("mode manually (e.g. in the Smile App) before retrying.")
            return
        print(f"Confirmed: Standby is now inactive (roomstatus={room['data'].get('roomstatus')}).\n")

    # Always work from a freshly-fetched room here - either the poll above
    # already got us a current one, or Standby was never active and the
    # very first fetch (moments ago) is still fresh enough.
    room = fetch_room(api, room_id)
    original_temp = room["data"].get("desiredTemperature")
    if original_temp is None:
        print("This room has no current desiredTemperature - cannot compute a test value safely. Aborting.")
        if was_standby_active:
            scene_manager.add_member_to_scene(room_id, SceneName.STANDBY.value)
        return

    min_temp = room["data"].get("scheduleTempMin", FALLBACK_MIN_TEMP)
    max_temp = room["data"].get("scheduleTempMax", FALLBACK_MAX_TEMP)

    test_temp = round(float(original_temp) + 0.5, 1)
    if test_temp > max_temp:
        test_temp = round(float(original_temp) - 0.5, 1)
        print(f"(+0.5 would exceed max={max_temp}, testing -0.5 instead)")
    if test_temp < min_temp:
        print(f"Cannot find a valid 0.5-step test value within [{min_temp}, {max_temp}] near {original_temp}. Aborting.")
        if was_standby_active:
            scene_manager.add_member_to_scene(room_id, SceneName.STANDBY.value)
        return

    print(f"Room: {room_name} (id={room_id})")
    print(f"Current desiredTemperature: {original_temp}  (valid range: {min_temp}-{max_temp})")
    print(f"Will set test value: {test_temp}  (sent as Python float -> dot notation, e.g. '{test_temp}')")
    answer = input("Proceed? [Enter] = yes, anything else = abort: ").strip()
    if answer:
        print("Aborted before changing temperature.")
        if was_standby_active:
            print("Restoring Standby (was deactivated for this test) ...")
            scene_manager.add_member_to_scene(room_id, SceneName.STANDBY.value)
        return

    print(f"\nSending set_temperature({test_temp}, room_id={room_id}) ...")
    api.set_temperature(test_temp, room_id)

    print("Waiting 3 seconds for the gateway to apply the change ...")
    time.sleep(3)

    print("Re-fetching room list ...")
    updated_room = fetch_room(api, room_id)
    readback_temp = updated_room["data"].get("desiredTemperature")

    print(f"\n{'=' * 60}")
    print("RESULT")
    print(f"{'=' * 60}")
    print(f"Sent:      {test_temp}")
    print(f"Read back: {readback_temp}")
    if readback_temp == test_temp:
        print("MATCH - dot notation is correctly interpreted. No code change needed.")
    else:
        print("MISMATCH - dot notation was NOT interpreted as expected.")
        print("This suggests the gateway may need comma notation (or something")
        print("else) for decimal temperature values - needs further investigation")
        print("before relying on decimal setpoints from Home Assistant.")
        print("(Double-check the room was really out of Standby the whole time -")
        print("see the poll log above - before concluding it's a notation issue.)")
    print(f"{'=' * 60}")

    restore = input(f"\nRestore original value ({original_temp})? [Enter] = yes, anything else = leave as-is: ").strip()
    if not restore:
        print(f"Restoring {room_name} to {original_temp} ...")
        api.set_temperature(original_temp, room_id)
        print("Restored.")
    else:
        print(f"Left as {test_temp} - remember to reset it yourself if needed.")

    if was_standby_active:
        print("\nRe-activating Standby (was active before this test) ...")
        scene_manager.add_member_to_scene(room_id, SceneName.STANDBY.value)
        confirmed = wait_for_standby_state(api, room_id, want_active=True)
        if confirmed is None:
            print("WARNING: could not confirm Standby re-activated within the poll")
            print("window - please check manually (e.g. in the Smile App).")
        else:
            print("Confirmed: Standby is active again.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    except Exception as exc:  # noqa: BLE001 - deliberately broad for a diagnostic script
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
