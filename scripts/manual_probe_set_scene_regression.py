# Change log:
# - 2026-09-09: v1. Regression check for the set_scene() rewrite (see
#   api_methods.py's own 2026-09-09 change log entry): the new `target`
#   parameter should reproduce exactly the results already confirmed
#   manually with raw `duration` values earlier today, and the new
#   room-assignment guard should raise ValueError when a scene has no
#   rooms and active=True is requested.
"""Manual diagnostic: regression-test set_scene()'s new `target` parameter
and room-assignment guard against the real gateway.

Run this directly in the dev container terminal:

    python3 scripts/manual_probe_set_scene_regression.py

Part A: for Boost/Party/Leave, call set_scene(scene, True, target=<half of
native max>) and confirm get_scene_duration() implies ~half remaining -
this should exactly match the manually-confirmed duration=0.5 results
from earlier today, just going through the new target= conversion path
instead of a hand-computed fraction.

Part B: temporarily clear a scene's room assignment, confirm set_scene()
now raises ValueError instead of silently no-op'ing, then restore the
original room assignment.

Never stores credentials.
"""
from __future__ import annotations

import getpass
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "custom_components"))

from honeywell_smileconnect.api.api_methods import (  # noqa: E402
    SCENE_MAX,
    ApiMethods,
)
from honeywell_smileconnect.api.login import Login  # noqa: E402

SETTLE_SECONDS = 3
NATIVE_UNIT = {"Boost": "minutes", "Party": "hours", "Leave": "hours"}


def test_target_param(api: ApiMethods, scene_name: str) -> None:
    scene_max = SCENE_MAX[scene_name]
    target = scene_max / 2  # native units, e.g. 60min for Boost, 6h for Party/Leave
    print(
        f"\n-- {scene_name}: set_scene(target={target} {NATIVE_UNIT[scene_name]}) --")

    set_response = api.set_scene(scene_name, True, target=target)
    print(f"   set_scene response: {set_response}")
    if not set_response.get("success"):
        print("   set_scene did NOT report success - skipping duration check.")
        return

    time.sleep(SETTLE_SECONDS)

    scene_status = api.get_specific_scene(scene_name)
    print(f"   isActive: {scene_status.get('isActive')}")

    raw_duration = api.get_scene_duration(scene_name)
    implied = raw_duration * scene_max
    print(
        f"   get_scene_duration raw: {raw_duration!r} -> implied {implied:.2f} {NATIVE_UNIT[scene_name]}")

    diff = implied - target
    # generous tolerance, settle delay is tiny vs native unit here
    ok = abs(diff) < 0.1 * scene_max
    print(
        f"   expected ~{target}, diff {diff:+.2f} -> {'OK' if ok else 'MISMATCH'}")

    deactivate = api.set_scene(scene_name, False, duration=0)
    print(f"   deactivated: success={deactivate.get('success')}")


def test_room_guard(api: ApiMethods, scene_name: str) -> None:
    print(f"\n-- {scene_name}: room-assignment guard test --")
    original_rooms = api.get_scene_rooms(scene_name)
    print(f"   original rooms: {original_rooms}")
    if not original_rooms:
        print("   scene already has no rooms - guard test would be trivial, skipping.")
        return

    print("   clearing room assignment temporarily ...")
    clear_response = api.set_scene_rooms(scene_name, [])
    print(f"   set_scene_rooms([]) response: {clear_response}")

    try:
        api.set_scene(scene_name, True, target=1)
        print("   UNEXPECTED: set_scene() did NOT raise ValueError with no rooms assigned.")
    except ValueError as exc:
        print(f"   OK: set_scene() raised ValueError as expected: {exc}")

    print("   restoring original room assignment ...")
    restore_response = api.set_scene_rooms(scene_name, original_rooms)
    print(f"   restore response: {restore_response}")
    restored = api.get_scene_rooms(scene_name)
    print(f"   rooms after restore: {restored}")
    if restored != original_rooms:
        print("   WARNING: restored rooms do not match original - check manually!")


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

    confirm = input(
        "This will activate/deactivate Boost, Party, Leave (target= test) and "
        "briefly clear/restore Boost's room assignment (guard test). "
        "Type 'yes' to proceed, anything else to abort: "
    ).strip().lower()
    if confirm != "yes":
        print("Aborted.")
        return

    print("\n" + "=" * 60)
    print("PART A: target= parameter regression")
    print("=" * 60)
    for scene_name in ("Boost", "Party", "Leave"):
        test_target_param(api, scene_name)

    print("\n" + "=" * 60)
    print("PART B: room-assignment guard")
    print("=" * 60)
    test_room_guard(api, "Boost")

    print("\n" + "=" * 60)
    print("Bitte die komplette Ausgabe zurückspiegeln.")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - deliberately broad for a diagnostic script
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
