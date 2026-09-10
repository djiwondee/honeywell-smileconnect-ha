# Change log:
# - 2026-09-09: v1. Resolves a direct contradiction between docs/
#   protocol.md §4d (2026-09-01: Leave duration=2 -> 6h, duration=4 ->
#   12h, factor x3, verified via physical regler/app display) and this
#   session's live-confirmed fraction/clamp formula (Party/Leave both
#   scene_max=12h; any duration >1 should clamp to scene_max=12h
#   regardless of exact value, meaning 2 and 4 should be
#   INDISTINGUISHABLE - both full 12h - contradicting §4d's claim that
#   they differ). §4d predates the duration=0 bug fix and therefore had
#   no reliable API-based readback available at the time (get_scene_
#   duration() only showed noise then) - it relied on physical/app
#   display reading instead. get_scene_duration() is now proven reliable
#   (this session's Boost countdown test), so this can finally be
#   checked directly via the API rather than a physical display read.
"""Manual diagnostic: send Leave duration=2 (the exact value protocol.md
§4d claims produces 6h) and read back via get_scene_duration() to see
whether it actually gives ~6h (confirming §4d) or ~12h/clamped (confirming
this session's fraction/clamp formula, and meaning §4d's old data was
mismeasured or came from a different code path).

Run this directly in the dev container terminal:

    python3 scripts/manual_probe_leave_duration_contradiction.py

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

# confirmed live via scene/status, both today and 2026-09-01
LEAVE_SCENE_MAX_HOURS = 12
SETTLE_SECONDS = 3


def probe(api: ApiMethods, duration_value: float, label: str) -> float | None:
    print(f"\n-- {label}: sending duration={duration_value!r} --")
    set_response = api.set_scene("Leave", True, duration=duration_value)
    print(f"   set_scene response: {set_response}")
    if not set_response.get("success"):
        print("   set_scene did NOT report success - skipping readback.")
        return None

    time.sleep(SETTLE_SECONDS)

    scene_status = api.get_specific_scene("Leave")
    print(f"   isActive: {scene_status.get('isActive')}")

    raw = api.get_scene_duration("Leave")
    implied_hours = raw * LEAVE_SCENE_MAX_HOURS
    print(f"   get_scene_duration raw: {raw!r} -> implied {implied_hours:.2f} hours "
          f"(assuming fraction-of-{LEAVE_SCENE_MAX_HOURS}h model)")

    deactivate = api.set_scene("Leave", False, duration=0)
    print(f"   deactivated: success={deactivate.get('success')}")

    return implied_hours


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
    room_id = rooms[0]["data"]["id"]

    print("Ensuring Leave has rooms assigned ...")
    if not api.get_scene_rooms("Leave"):
        api.set_scene_rooms("Leave", [room_id])

    confirm = input(
        "\nThis will activate/deactivate Leave twice on your room. "
        "Type 'yes' to proceed, anything else to abort: "
    ).strip().lower()
    if confirm != "yes":
        print("Aborted.")
        return

    result_2 = probe(api, 2, "protocol.md §4d's claimed send-value for 6h")
    result_4 = probe(
        api, 4, "protocol.md §4d's claimed send-value for 12h (=Max)")

    print("\n" + "=" * 60)
    print("RESULT")
    print("=" * 60)
    print(
        f"  duration=2 -> implied {result_2:.2f}h" if result_2 is not None else "  duration=2 -> no result")
    print(
        f"  duration=4 -> implied {result_4:.2f}h" if result_4 is not None else "  duration=4 -> no result")

    if result_2 is not None and result_4 is not None:
        if abs(result_2 - 6) < 1 and abs(result_4 - 12) < 1:
            print("\n=> Matches protocol.md §4d (2 -> ~6h, 4 -> ~12h). "
                  "Leave genuinely uses a DIFFERENT formula than Party, "
                  "despite identical scene_max in scene/status. The "
                  "fraction/clamp model from this session's other tests "
                  "does NOT apply uniformly to Leave.")
        elif abs(result_2 - 12) < 1 and abs(result_4 - 12) < 1:
            print("\n=> Both clamp to ~12h (full max), matching THIS "
                  "session's fraction/clamp model, NOT protocol.md §4d. "
                  "§4d's old Leave data (2->6h, 4->12h) was likely "
                  "mismeasured or came from a different code path "
                  "(e.g. scene_manager.py applying its own scaling before "
                  "calling set_scene()) - needs correction in protocol.md.")
        else:
            print("\n=> Neither hypothesis matches cleanly - needs a closer "
                  "look before updating protocol.md either way.")

    print("\nBitte die komplette Ausgabe zurückspiegeln.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - deliberately broad for a diagnostic script
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
