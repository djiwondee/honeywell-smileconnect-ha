# Change log:
# - 2026-09-09: Initial version. Captures the real /api/room/switchingtimes/get2
#   response before and after a manual schedule change made via the Smile App,
#   to verify the response structure live (no fixture exists yet) and infer
#   the field format set2 will need. Deliberately does NOT call set2 - that
#   endpoint has never been verified live, so we observe get2 first and only
#   attempt set2 once its expected shape is understood.
"""Manual diagnostic: capture /api/room/switchingtimes/get2 before and after
a manual schedule change made through the Smile App.

Run this directly in the dev container terminal:

    python3 scripts/manual_probe_switchingtimes.py

Workflow:
    1. Logs in and lists rooms, same as the other manual_* scripts.
    2. Calls get2 for the chosen room and pretty-prints the raw response.
    3. Pauses and asks you to change ONE switching time for that room via
       the Smile App (e.g. move a single "on" time by 15 minutes).
    4. Calls get2 again and pretty-prints the new raw response.
    5. Does a naive field-by-field diff between before/after so the changed
       value(s) are easy to spot even in a large response.

This is read-only against the gateway (get2 only) - the actual schedule
change happens on your phone, not through this script. Nothing here calls
set2. Never stores credentials.
"""
from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "custom_components"))

from honeywell_smileconnect.api.api_methods import ApiMethods  # noqa: E402
from honeywell_smileconnect.api.login import Login  # noqa: E402


def _diff(before: dict, after: dict, path: str = "") -> list[str]:
    """Naive recursive diff, good enough to surface changed leaf values in
    a nested JSON response without pulling in an extra dependency.
    """
    changes: list[str] = []
    keys = set(before.keys()) | set(after.keys())
    for key in sorted(keys, key=str):
        p = f"{path}.{key}" if path else str(key)
        b = before.get(key, "<missing>")
        a = after.get(key, "<missing>")
        if isinstance(b, dict) and isinstance(a, dict):
            changes.extend(_diff(b, a, p))
        elif isinstance(b, list) and isinstance(a, list):
            if b != a:
                changes.append(f"{p}:\n    before: {b}\n    after:  {a}")
        elif b != a:
            changes.append(f"{p}: {b!r} -> {a!r}")
    return changes


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
    room_idx = 0 if len(rooms) == 1 else int(
        input(f"Which room to test? [0-{len(rooms) - 1}]: ").strip()
    )
    room_id = rooms[room_idx]["data"]["id"]
    room_name = rooms[room_idx]["name"]
    print(f"\nUsing room: {room_name} (id={room_id})\n")

    print("Calling get2 (BEFORE) ...\n")
    before = api.get_switching_times(room_name, room_id)
    print("=" * 60)
    print("RAW get2 RESPONSE (BEFORE):")
    print("=" * 60)
    print(json.dumps(before, indent=2, ensure_ascii=False))
    print("=" * 60)

    print(
        f"\nBitte jetzt in der Smile App für Raum '{room_name}' GENAU EINE "
        "Schaltzeit ändern (z.B. eine 'Ein'-Zeit um 15 Minuten verschieben). "
        "Notier dir kurz, was du geändert hast, zum Abgleich mit dem Diff unten."
    )
    input("Enter drücken, sobald die Änderung gespeichert ist ...")

    print("\nCalling get2 (AFTER) ...\n")
    after = api.get_switching_times(room_name, room_id)
    print("=" * 60)
    print("RAW get2 RESPONSE (AFTER):")
    print("=" * 60)
    print(json.dumps(after, indent=2, ensure_ascii=False))
    print("=" * 60)

    print("\n" + "=" * 60)
    print("DIFF (before -> after):")
    print("=" * 60)
    if isinstance(before, dict) and isinstance(after, dict):
        changes = _diff(before, after)
        if changes:
            for change in changes:
                print(f"  {change}")
        else:
            print("  No field-level differences detected - double-check the")
            print("  change was actually saved, or the response may be")
            print("  structured differently than this diff expects.")
    else:
        print("  Response is not a plain dict at the top level - showing")
        print("  raw before/after above for manual comparison instead.")
    print("=" * 60)
    print(
        "\nBitte diese komplette Ausgabe (BEFORE, AFTER, DIFF) zurückspiegeln, "
        "zusammen mit einer kurzen Notiz, was genau am Handy geändert wurde."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - deliberately broad for a diagnostic script
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
