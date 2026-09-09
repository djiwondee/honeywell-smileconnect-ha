# Change log:
# - 2026-09-09: v3. Regression check after api_methods.py was updated with
#   the fixed set_switching_times() (wire key "from" via setattr, leading-
#   zero stripping, contiguous-slot validation - see api_methods.py's own
#   change log and project learnings for the full investigation). Unlike
#   v2, this calls api.set_switching_times() directly instead of building
#   the set2 request by hand - so it now tests the actual integration
#   code path, not just a hand-verified format guess. Also much simpler:
#   since set_switching_times() now accepts the exact same shape get2
#   returns, the echo is just "pass get2's switchingtimes list straight
#   back in" - no manual CSV-building or leading-zero handling needed
#   here anymore, that all lives in api_methods.py now.
"""Manual diagnostic: echo test for ApiMethods.set_switching_times(),
calling the real (fixed) integration code directly.

Run this directly in the dev container terminal:

    python3 scripts/manual_probe_switchingtimes_echo_v3.py

Workflow:
    1. Calls get2 (BEFORE) via api.get_switching_times().
    2. Passes before["switchingtimes"] straight into
       api.set_switching_times() unchanged - a pure echo, exercising the
       real production code path (CSV-building, leading-zero stripping,
       contiguous-slot validation all happen inside api_methods.py now).
    3. Re-calls get2 (AFTER) and diffs against BEFORE.

Never stores credentials.
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

    print("Calling get_switching_times() (BEFORE) ...\n")
    before = api.get_switching_times(room_name, room_id)
    print(json.dumps(before, indent=2, ensure_ascii=False))

    if not before.get("success"):
        print("\nget2 itself did not report success - aborting before touching set2.")
        return

    print("\n" + "=" * 60)
    print("Calling api.set_switching_times() with the UNCHANGED schedule")
    print("just read above - real integration code path, not a hand-built")
    print("request.")
    print("=" * 60)

    confirm = input(
        "\nThis will WRITE to the real gateway. Type 'yes' to proceed, "
        "anything else to abort: "
    ).strip().lower()
    if confirm != "yes":
        print("Aborted - set_switching_times() was not called.")
        return

    print("\nCalling set_switching_times() ...\n")
    try:
        set_response = api.set_switching_times(
            room_name, room_id, before["switchingtimes"])
    except ValueError as exc:
        print(
            f"\nset_switching_times() raised ValueError before sending anything: {exc}")
        print("(This would mean the echoed-back schedule itself violates the")
        print(" contiguous-slot rule, which would be surprising since it came")
        print(" straight from get2 - worth investigating if it happens.)")
        return

    print("=" * 60)
    print("RAW set2 RESPONSE:")
    print("=" * 60)
    print(json.dumps(set_response, indent=2, ensure_ascii=False))
    print("=" * 60)

    print("\nRe-calling get_switching_times() (AFTER) to verify nothing changed ...\n")
    after = api.get_switching_times(room_name, room_id)
    print(json.dumps(after, indent=2, ensure_ascii=False))

    print("\n" + "=" * 60)
    if before["switchingtimes"] == after["switchingtimes"]:
        print("RESULT: switchingtimes IDENTICAL before/after.")
        print("-> api.set_switching_times() round-trips correctly via the real")
        print("   integration code.")
    else:
        print("RESULT: switchingtimes DIFFERS before/after - something regressed.")
        print(f"\n  BEFORE: {before['switchingtimes']}")
        print(f"\n  AFTER:  {after['switchingtimes']}")
    print("=" * 60)
    print(
        "\nBitte die komplette Ausgabe (set2-Response, get2 AFTER, RESULT) "
        "zurückspiegeln."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - deliberately broad for a diagnostic script
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
