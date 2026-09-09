# Change log:
# - 2026-09-09: v1. Falsification test for the scene/set `duration`
#   semantics. Live mitmproxy capture (2026-09-09) showed the Smile App
#   sends `duration` as a FRACTION of scene_max (remaining/scene_max),
#   contradicting the 3-month-old apiMethods.py header comment claiming
#   "Boost: Minuten direkt (0-120), live getestet und verifiziert" - that
#   claim was likely only checked against success:true, not against the
#   actual remaining time. This script tests both hypotheses directly
#   against the real, unmodified set_scene()/get_scene_duration() in
#   api_methods.py - no source changes needed, since set_scene() passes
#   `duration` straight through without interpreting it itself.
#
#   Also guards against a gap versus the old apiMethods.py: the current
#   set_scene() does NOT check whether the scene has rooms assigned
#   before activating (the old activateScene() did, and raised if not -
#   without rooms, the gateway returns success=True but isActive stays
#   False). This script does that check itself before testing, since a
#   roomless activation would otherwise look like a false confirmation.
"""Manual diagnostic: test whether /api/scene/set's `duration` parameter
is interpreted as a fraction of scene_max or as raw minutes/hours/days.

Run this directly in the dev container terminal:

    python3 scripts/manual_probe_scene_duration.py

Workflow per hypothesis (fraction, then raw):
    1. Ensure the scene has rooms assigned (set_scene_rooms if needed).
    2. Call set_scene(scene, True, duration=<test value>).
    3. Wait a short settle time.
    4. Call get_scene_duration(scene) and compare the implied remaining
       time against what was actually requested.
    5. Deactivate the scene before moving to the next hypothesis.

Never stores credentials.
"""
from __future__ import annotations

import getpass
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "custom_components"))

from honeywell_smileconnect.api.api_methods import ApiMethods  # noqa: E402
from honeywell_smileconnect.api.login import Login  # noqa: E402

# scene/status min/max/step, live-captured 2026-09-09. Used only to compute
# the fraction hypothesis's test value and to interpret get_scene_duration()
# results - NOT written back into api_methods.py by this script.
SCENE_MAX = {"Boost": 120, "Party": 12, "Leave": 12}
SETTLE_SECONDS = 3


def ensure_scene_has_rooms(api: ApiMethods, scene_name: str, room_id) -> None:
    """set_scene() does not check this itself (unlike the old apiMethods.py's
    activateScene) - without it, activation reports success=True but
    isActive silently stays False."""
    rooms = api.get_scene_rooms(scene_name)
    if rooms:
        print(f"  Scene '{scene_name}' already has rooms assigned: {rooms}")
        return
    print(
        f"  Scene '{scene_name}' has NO rooms assigned - assigning room {room_id} now.")
    result = api.set_scene_rooms(scene_name, [room_id])
    print(f"  set_scene_rooms result: {result}")


def run_hypothesis(
    api: ApiMethods,
    scene_name: str,
    label: str,
    duration_value: float,
    expected_remaining_minutes: float,
) -> None:
    print("\n" + "=" * 60)
    print(f"HYPOTHESIS: {label}")
    print(f"Sending duration={duration_value!r}")
    print("=" * 60)

    set_response = api.set_scene(scene_name, True, duration=duration_value)
    print(f"set_scene response: {set_response}")

    if not set_response.get("success"):
        print("set_scene did not report success - skipping duration read.")
        return

    time.sleep(SETTLE_SECONDS)

    scene_status = api.get_specific_scene(scene_name)
    print(f"scene status after set: isActive={scene_status.get('isActive')}")
    if not scene_status.get("isActive"):
        print("WARNING: isActive is False after set_scene - activation did not "
              "actually take effect (see room-assignment note in script header).")

    raw_duration = api.get_scene_duration(scene_name)
    print(f"get_scene_duration raw value: {raw_duration!r}")

    scene_max = SCENE_MAX[scene_name]
    implied_as_fraction = raw_duration * scene_max
    print(f"If interpreted as a fraction of scene_max ({scene_max}): "
          f"{implied_as_fraction:.2f} minutes remaining")

    expected_after_delay = expected_remaining_minutes - (SETTLE_SECONDS / 60.0)
    diff = implied_as_fraction - expected_after_delay
    print(
        f"Expected remaining after {SETTLE_SECONDS}s settle: ~{expected_after_delay:.2f} minutes")
    print(f"Difference: {diff:+.2f} minutes")

    if abs(diff) < 1.0:
        print(
            f"=> '{label}' MATCHES the fraction-read formula within 1min tolerance.")
    else:
        print(f"=> '{label}' does NOT match within 1min tolerance.")

    deactivate_response = api.set_scene(scene_name, False, duration=0)
    print(f"Deactivated scene for next test: {deactivate_response}")


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

    scene_name = input("Scene to test [Boost]: ").strip() or "Boost"
    if scene_name not in SCENE_MAX:
        print(
            f"No known scene_max for '{scene_name}' - add it to SCENE_MAX first.")
        return

    print(f"\nChecking room assignment for scene '{scene_name}' ...")
    ensure_scene_has_rooms(api, scene_name, room_id)

    scene_max = SCENE_MAX[scene_name]
    target_minutes = min(30.0, scene_max)  # a safe, short test duration

    confirm = input(
        f"\nThis will activate '{scene_name}' on room '{room_name}' twice "
        f"(once per hypothesis) and deactivate it after each. Type 'yes' to "
        f"proceed, anything else to abort: "
    ).strip().lower()
    if confirm != "yes":
        print("Aborted.")
        return

    # Hypothesis A: duration is a fraction of scene_max (matches today's
    # mitmproxy capture of the app's own scene/duration READ behavior).
    run_hypothesis(
        api, scene_name,
        label="duration = fraction of scene_max",
        duration_value=target_minutes / scene_max,
        expected_remaining_minutes=target_minutes,
    )

    # Hypothesis B: duration is the raw target value directly (matches the
    # 3-month-old apiMethods.py header claim - now suspect, being tested).
    run_hypothesis(
        api, scene_name,
        label="duration = raw target value (old apiMethods.py assumption)",
        duration_value=target_minutes,
        expected_remaining_minutes=target_minutes,
    )

    print("\n" + "=" * 60)
    print("Bitte die komplette Ausgabe beider Hypothesen zurückspiegeln.")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - deliberately broad for a diagnostic script
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
