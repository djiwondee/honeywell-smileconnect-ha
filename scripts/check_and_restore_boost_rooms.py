# Change log:
# - 2026-09-09: v1. Minimal recovery-check script after
#   manual_probe_set_scene_regression.py caused a gateway hang by calling
#   set_scene_rooms("Boost", []) - see project learnings:
#   "setrooms mit einer leeren Liste verursacht einen Firmware-Hang
#   unabhängig von der Kodierung, muss komplett uebersprungen werden."
#   This script ONLY reads the current state and, if needed, restores
#   room [1] - it never sends an empty list.
"""Check Boost's current room assignment and restore [1] if it's empty.

Run this directly in the dev container terminal:

    python3 scripts/check_and_restore_boost_rooms.py
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "custom_components"))

from honeywell_smileconnect.api.api_methods import ApiMethods  # noqa: E402
from honeywell_smileconnect.api.login import Login  # noqa: E402


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

    print("Reading current Boost room assignment ...")
    rooms = api.get_scene_rooms("Boost")
    print(f"Boost rooms right now: {rooms}")

    scene_status = api.get_specific_scene("Boost")
    print(f"Boost isActive: {scene_status.get('isActive')}")

    if rooms:
        print("\nRoom assignment looks intact - nothing to restore.")
        return

    print("\nBoost has NO rooms assigned - restoring room 1.")
    confirm = input(
        "Type 'yes' to restore [1], anything else to abort: ").strip().lower()
    if confirm != "yes":
        print("Aborted - room assignment left as-is.")
        return

    restore_response = api.set_scene_rooms("Boost", [1])
    print(f"set_scene_rooms([1]) response: {restore_response}")

    rooms_after = api.get_scene_rooms("Boost")
    print(f"Boost rooms after restore: {rooms_after}")
    if rooms_after == [1]:
        print("Restored successfully.")
    else:
        print(
            "WARNING: rooms after restore do not match [1] - check manually.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - deliberately broad for a diagnostic script
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
