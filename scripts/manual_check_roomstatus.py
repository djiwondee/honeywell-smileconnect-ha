# Change log:
# - 2026-08-27: Initial version. One-off manual diagnostic script, NOT part
#   of the automated test suite - lives in scripts/, not tests/, so
#   `pytest tests/` never runs it and never triggers live scene changes.
#   Once roomstatus values are confirmed here, they get hand-transcribed
#   into const.py's ROOM_STATUS_* constants and docs/protocol.md; this
#   script itself is not re-run automatically afterwards.
"""Manual diagnostic: verify roomstatus codes for each known scene.

Run this directly in the dev container terminal:

    python3 scripts/manual_check_roomstatus.py

Interactive, scene-by-scene (Option B from project discussion): before
touching anything, it shows exactly what it's about to do and waits for
you to confirm or skip - nothing happens automatically/unattended.

For each scene, this:
  1. Shows what's about to happen and asks for confirmation
     ([Enter] = proceed, [s] = skip this scene, [q] = quit entirely).
  2. Adds the room to the scene (turns it on).
  3. Re-fetches /api/room/list and records the resulting `roomstatus`.
  4. Removes the room from the scene again (best-effort cleanup - runs
     even if step 3 raised an error, via `finally`).
  5. At the end, prints a summary table: scene -> observed roomstatus.

SAFETY NOTE: this activates REAL heating scenes on your real system,
briefly, one at a time. Confirm each one deliberately; skip anything
you're not comfortable testing right now - e.g. "Holiday" may put the
regler into frost-protection / effectively off, which you may want to
test only when that's actually fine for your home at that moment.

This is also, incidentally, the first real end-to-end exercise of
api/scene_manager.py against a live gateway - not just a roomstatus probe.
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "custom_components"))

from honeywell_smileconnect.api.apiMethods import ApiMethods  # noqa: E402
from honeywell_smileconnect.api.login import Login  # noqa: E402
from honeywell_smileconnect.api.scene_manager import SceneManager  # noqa: E402
from honeywell_smileconnect.const import SceneName  # noqa: E402

SCENES_TO_TEST = [
    SceneName.BOOST.value,
    SceneName.PARTY.value,
    SceneName.LEAVE.value,
    SceneName.HOLIDAY.value,
    SceneName.STANDBY.value,
]


def prompt_action(scene_name: str) -> str:
    print(f"\n{'=' * 60}")
    print(f"About to activate scene: {scene_name}")
    print(f"{'=' * 60}")
    print("This will briefly turn this scene ON for the room, check the")
    print("resulting roomstatus, then turn it back OFF again.")
    answer = input(
        "[Enter] = proceed   [s] = skip this scene   [q] = quit entirely: "
    ).strip().lower()
    return answer


def main() -> None:
    host = input(
        "Gateway host/IP [192.168.1.132]: ").strip() or "192.168.1.132"
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
        status = room["data"].get("roomstatus")
        print(
            f"  [{i}] {room['name']} (id={room['data']['id']}, current roomstatus={status})")

    if len(rooms) == 1:
        room_idx = 0
    else:
        room_idx = int(
            input(f"Which room to test? [0-{len(rooms) - 1}]: ").strip())

    room_id = rooms[room_idx]["data"]["id"]
    room_name = rooms[room_idx]["name"]
    print(f"\nTesting against room: {room_name} (id={room_id})\n")

    results: dict[str, object] = {}

    for scene_name in SCENES_TO_TEST:
        answer = prompt_action(scene_name)
        if answer == "q":
            print("Quitting as requested.")
            break
        if answer == "s":
            print(f"Skipped {scene_name}.")
            results[scene_name] = "SKIPPED"
            continue

        try:
            print(f"Activating {scene_name} for {room_name} ...")
            scene_manager.add_member_to_scene(room_id, scene_name)

            print("Re-fetching room list ...")
            updated_rooms = api.get_rooms_list()
            updated_room = next(
                r for r in updated_rooms if r["data"]["id"] == room_id)
            observed_status = updated_room["data"].get("roomstatus")
            print(
                f"--> Observed roomstatus while {scene_name} is active: {observed_status}")
            results[scene_name] = observed_status

        except Exception as exc:  # noqa: BLE001 - diagnostic script, show and continue
            print(f"ERROR while testing {scene_name}: {exc}")
            results[scene_name] = f"ERROR: {exc}"

        finally:
            try:
                print(f"Deactivating {scene_name} again for {room_name} ...")
                scene_manager.remove_member_from_scene(room_id, scene_name)
                print("Deactivated.")
            except Exception as exc:  # noqa: BLE001
                print(
                    f"WARNING: could not clean up {scene_name} automatically: {exc}")
                print(
                    "Please check your gateway/app manually to confirm the scene is off.")

    print(f"\n{'=' * 60}")
    print("SUMMARY: scene -> observed roomstatus")
    print(f"{'=' * 60}")
    for scene_name in SCENES_TO_TEST:
        print(f"  {scene_name:10s} -> {results.get(scene_name, 'NOT TESTED')}")
    print(f"{'=' * 60}")
    print("\nNext step: transcribe these confirmed values into const.py's")
    print("ROOM_STATUS_* constants and docs/protocol.md.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    except Exception as exc:  # noqa: BLE001
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
