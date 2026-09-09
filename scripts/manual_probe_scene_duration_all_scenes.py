# Change log:
# - 2026-09-09: v1. Extends the Boost-only falsification test
#   (manual_probe_scene_duration.py) to all scenes with a duration
#   parameter: Boost, Party, Leave, Holiday. Standby/Towel excluded -
#   SCENES_NO_DURATION per old apiMethods.py, and not physically
#   relevant here (no hot water hardware - see project overview).
#
#   For Boost/Party/Leave, scene_max is known from a live scene/status
#   capture (2026-09-09): Boost=120min, Party=12h, Leave=12h. Live
#   countdown validation (immediate + delayed reads) already confirmed
#   the fraction formula for Boost specifically.
#
#   For Party/Leave/Holiday, a full countdown validation (like Boost's)
#   is not practical in one session - Party/Leave run on an hours scale
#   where a few minutes of wait is too small a fraction to measure
#   reliably, and Holiday runs on a days scale entirely. Instead this
#   script runs the structural clamp test that WAS conclusive for Boost:
#   a fraction value (0.5) should be echoed back unchanged, while a raw
#   value >1 (assumed target from the old apiMethods.py's "direct hours/
#   days" claim) should get clamped to 1 if the same fraction-based field
#   is reused across scenes. This confirms/refutes the SHAPE of the
#   formula without needing scene_max for Holiday, but does NOT establish
#   Holiday's actual scene_max - flagged as still open even if the
#   pattern matches.
"""Manual diagnostic: test the scene/set `duration` fraction-vs-raw
clamp pattern across Boost, Party, Leave, and Holiday.

Run this directly in the dev container terminal:

    python3 scripts/manual_probe_scene_duration_all_scenes.py

For each scene:
    1. Ensure the scene has rooms assigned.
    2. Send duration=0.5 (fraction hypothesis) - expect it echoed back
       ~unchanged via get_scene_duration().
    3. Deactivate.
    4. Send duration=<raw_value> (raw hypothesis, a value >1 in the
       scene's native unit) - expect it clamped to 1 if the same
       fraction field applies.
    5. Deactivate.

Never stores credentials.
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

SETTLE_SECONDS = 3

# scene_max is only known for Boost/Party/Leave, from a live scene/status
# capture (2026-09-09). Holiday has no min/max/step in scene/status - None
# here means "unknown", the script skips the fraction->minutes conversion
# for it and just reports the raw duration value.
SCENE_CONFIG = {
    "Boost":   {"scene_max": 120, "unit": "minutes", "raw_test_value": 30},
    "Party":   {"scene_max": 12,  "unit": "hours",   "raw_test_value": 6},
    "Leave":   {"scene_max": 12,  "unit": "hours",   "raw_test_value": 6},
    "Holiday": {"scene_max": None, "unit": "days (unconfirmed max)", "raw_test_value": 15},
}


def ensure_scene_has_rooms(api: ApiMethods, scene_name: str, room_id) -> None:
    rooms = api.get_scene_rooms(scene_name)
    if rooms:
        print(f"  Scene '{scene_name}' already has rooms assigned: {rooms}")
        return
    print(
        f"  Scene '{scene_name}' has NO rooms assigned - assigning room {room_id} now.")
    result = api.set_scene_rooms(scene_name, [room_id])
    print(f"  set_scene_rooms result: {result}")


def probe(api: ApiMethods, scene_name: str, duration_value: float, label: str) -> float | None:
    print(f"\n  -- {label}: sending duration={duration_value!r}")
    set_response = api.set_scene(scene_name, True, duration=duration_value)
    if not set_response.get("success"):
        print(f"     set_scene did NOT report success: {set_response}")
        return None

    time.sleep(SETTLE_SECONDS)

    scene_status = api.get_specific_scene(scene_name)
    print(f"     isActive after set: {scene_status.get('isActive')}")

    raw_duration = api.get_scene_duration(scene_name)
    print(f"     get_scene_duration raw value: {raw_duration!r}")

    deactivate_response = api.set_scene(scene_name, False, duration=0)
    print(f"     deactivated: success={deactivate_response.get('success')}")

    return raw_duration


def run_scene(api: ApiMethods, scene_name: str, room_id) -> None:
    config = SCENE_CONFIG[scene_name]
    print("\n" + "=" * 60)
    print(f"SCENE: {scene_name}  (unit hint: {config['unit']}, "
          f"scene_max={config['scene_max']})")
    print("=" * 60)

    ensure_scene_has_rooms(api, scene_name, room_id)

    fraction_result = probe(api, scene_name, 0.5, "fraction hypothesis (0.5)")
    raw_result = probe(api, scene_name, config["raw_test_value"],
                       f"raw hypothesis ({config['raw_test_value']})")

    print(f"\n  SUMMARY for {scene_name}:")
    if fraction_result is not None:
        unclamped = abs(fraction_result - 0.5) < 0.05
        print(f"    fraction (0.5) echoed as {fraction_result!r} -> "
              f"{'unclamped, as expected' if unclamped else 'UNEXPECTED - does not match 0.5'}")
    if raw_result is not None:
        clamped = abs(raw_result - 1.0) < 0.05
        print(f"    raw ({config['raw_test_value']}) echoed as {raw_result!r} -> "
              f"{'clamped to 1, same pattern as Boost' if clamped else 'NOT clamped - different behavior than Boost!'}")
    if fraction_result is not None and raw_result is not None:
        if abs(fraction_result - 0.5) < 0.05 and abs(raw_result - 1.0) < 0.05:
            print(
                f"    => {scene_name} matches the Boost fraction/clamp pattern.")
        else:
            print(
                f"    => {scene_name} does NOT cleanly match the Boost pattern - needs closer look.")


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
        print("No rooms found - nothing to test.")
        return

    print("Rooms found:")
    for i, room in enumerate(rooms):
        print(f"  [{i}] {room['name']} (id={room['data']['id']})")
    room_idx = 0 if len(rooms) == 1 else int(
        input(f"Which room to test? [0-{len(rooms) - 1}]: ").strip()
    )
    room_id = rooms[room_idx]["data"]["id"]
    room_name = rooms[room_idx]["name"]
    print(f"\nUsing room: {room_name} (id={room_id})\n")

    scenes_input = input(
        "Scenes to test, comma-separated [Boost,Party,Leave,Holiday]: "
    ).strip()
    scene_names = (
        [s.strip() for s in scenes_input.split(",")]
        if scenes_input
        else ["Boost", "Party", "Leave", "Holiday"]
    )
    for name in scene_names:
        if name not in SCENE_CONFIG:
            print(f"Unknown scene '{name}' - skipping.")

    confirm = input(
        f"\nThis will activate/deactivate {', '.join(scene_names)} twice each "
        f"on room '{room_name}'. Type 'yes' to proceed, anything else to abort: "
    ).strip().lower()
    if confirm != "yes":
        print("Aborted.")
        return

    for name in scene_names:
        if name in SCENE_CONFIG:
            run_scene(api, name, room_id)

    print("\n" + "=" * 60)
    print("Bitte die komplette Ausgabe (alle Szenen) zurückspiegeln.")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - deliberately broad for a diagnostic script
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
