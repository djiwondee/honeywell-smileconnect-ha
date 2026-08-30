# Change log:
# - 2026-08-27: Initial version. Created after manual_check_roomstatus.py
#   showed roomstatus=12 for every scene tested via OUR OWN
#   scene_manager.py code, while /api/scene/status revealed "Standby" was
#   ALREADY active (isActive: true) throughout - our scene_manager never
#   explicitly deactivated it first, which may explain why roomstatus never
#   changed. This script sidesteps that entirely by using the Smile App
#   itself (known-correct) to change modes, and only ever READS state via
#   our code - zero write/POST-with-side-effects calls. Confirms scenes
#   are per-room (the user must select a room in the app too, not just a
#   mode), matching our getrooms/setrooms model.
"""Manual diagnostic: read roomstatus + scene/status while YOU change modes
via the Smile App itself - not via our own scene_manager.py code.

Run this directly in the dev container terminal:

    python3 scripts/manual_probe_roomstatus_via_app.py

This is 100% READ-ONLY on our side - it only calls get_rooms_list() and
get_scene_status() (both GET-shaped, no state-changing effect). All actual
mode changes are things YOU do yourself in the Smile App on your phone/
tablet, for the room in question - this isolates "what roomstatus number
corresponds to which real mode" from any possible bug in our own
scene_manager.py write path.

For each mode, it will:
  1. Ask you to set that mode (for the correct room!) via the Smile App,
     and press Enter here once you've confirmed it took effect in the app.
  2. Read /api/room/list and /api/scene/status, print the observed
     roomstatus and which scene(s) the gateway itself reports as active.
  3. Record it, then move to the next mode.

At the end it prints a summary table, and reminds you to set the room back
to whatever your normal/preferred mode is via the app (this script will
NOT do that for you - it changes nothing).
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "custom_components"))

from honeywell_smileconnect.api.apiMethods import ApiMethods  # noqa: E402
from honeywell_smileconnect.api.login import Login  # noqa: E402

# Shower/Towel deliberately excluded - no hot water control on this
# installation (see CLAUDE.md "Target System / Test Environment").
MODES_TO_PROBE = ["Standby", "Boost", "Party", "Leave", "Holiday"]


def read_state(api: ApiMethods, room_id) -> dict:
    rooms = api.get_rooms_list()
    room = next(r for r in rooms if r["data"]["id"] == room_id)
    roomstatus = room["data"].get("roomstatus")

    scene_status = api.get_scene_status()
    active_scenes = [s["name"]
                     for s in scene_status.get("scenes", []) if s.get("isActive")]

    return {"roomstatus": roomstatus, "active_scenes": active_scenes}


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

    rooms = api.get_rooms_list()
    if not rooms:
        print("No rooms found - nothing to probe.")
        return

    print("Rooms found:")
    for i, room in enumerate(rooms):
        print(f"  [{i}] {room['name']} (id={room['data']['id']})")
    if len(rooms) == 1:
        room_idx = 0
    else:
        room_idx = int(input(
            f"Which room will you be changing modes for? [0-{len(rooms) - 1}]: ").strip())
    room_id = rooms[room_idx]["data"]["id"]
    room_name = rooms[room_idx]["name"]
    print(f"\nProbing against room: {room_name} (id={room_id})\n")

    results: dict[str, dict] = {}

    for mode in MODES_TO_PROBE:
        print(f"\n{'=' * 60}")
        print(
            f"Please set room '{room_name}' to mode '{mode}' now, via the Smile App")
        print("(remember to select the correct room in the app).")
        print(f"{'=' * 60}")
        answer = input(
            "Press Enter once done, or type 's' to skip this mode: ").strip().lower()
        if answer == "s":
            print(f"Skipped {mode}.")
            results[mode] = {"roomstatus": "SKIPPED", "active_scenes": []}
            continue

        state = read_state(api, room_id)
        print(
            f"--> roomstatus = {state['roomstatus']}, gateway-reported active scene(s) = {state['active_scenes']}")
        results[mode] = state

    print(f"\n{'=' * 60}")
    print("SUMMARY: mode (set via app) -> roomstatus / gateway-active scene(s)")
    print(f"{'=' * 60}")
    for mode in MODES_TO_PROBE:
        r = results.get(
            mode, {"roomstatus": "NOT TESTED", "active_scenes": []})
        print(
            f"  {mode:10s} -> roomstatus={r['roomstatus']!s:6s}  active_scenes={r['active_scenes']}")
    print(f"{'=' * 60}")
    print("\nThis script changed nothing - please set the room back to your")
    print("normal/preferred mode via the Smile App now if needed.")
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
