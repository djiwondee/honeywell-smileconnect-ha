# Change log:
# - 2026-09-09: v1. Replaces the multi-value-in-one-run
#   manual_probe_leave_formula_ceiling.py after that script produced
#   non-monotonic, unexplainable results (1->12h, 3->12h, 5->6h, 6->12h,
#   8->5h - no linear/capped model fits). Suspected cause: five
#   activate/deactivate cycles ran back-to-back in one session with only
#   a 3s gap, and the gateway's internal state for a just-deactivated
#   scene may not have been fully settled before the next activation -
#   contaminating each measurement with leftover state from the previous
#   one. This script tests exactly ONE value per invocation, with an
#   explicit pre-flight check that Leave is confirmed inactive before
#   sending anything (polls up to 10s, not just a fixed sleep), so
#   repeated runs (as separate script invocations, ideally with a real
#   pause between them) give clean, uncontaminated data points.
"""Manual diagnostic: test exactly ONE Leave duration value per run, with
a verified-inactive pre-flight check.

Run this directly in the dev container terminal, once per value you want
to test, e.g.:

    python3 scripts/manual_probe_leave_single_value.py --sent 2
    (wait a bit, then run again with a different value)
    python3 scripts/manual_probe_leave_single_value.py --sent 4

Never stores credentials.
"""
from __future__ import annotations

import argparse
import getpass
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "custom_components"))

from honeywell_smileconnect.api.api_methods import ApiMethods  # noqa: E402
from honeywell_smileconnect.api.login import Login  # noqa: E402

LEAVE_SCENE_MAX_HOURS = 12
SETTLE_SECONDS = 5
PRE_FLIGHT_TIMEOUT_SECONDS = 10
PRE_FLIGHT_POLL_INTERVAL_SECONDS = 1


def wait_until_inactive(api: ApiMethods) -> bool:
    """Poll scene/status until Leave reports isActive=False, or time out.
    Returns True if confirmed inactive, False on timeout."""
    deadline = time.monotonic() + PRE_FLIGHT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status = api.get_specific_scene("Leave")
        if not status.get("isActive"):
            return True
        print(f"   still active, waiting... ({status})")
        time.sleep(PRE_FLIGHT_POLL_INTERVAL_SECONDS)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Isolated single-value Leave duration probe")
    parser.add_argument("--sent", type=float, required=True,
                        help="Raw duration value to send")
    args = parser.parse_args()

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

    if not api.get_scene_rooms("Leave"):
        api.set_scene_rooms("Leave", [room_id])

    print("Pre-flight: confirming Leave is currently inactive ...")
    if not wait_until_inactive(api):
        print("TIMEOUT: Leave still shows isActive=True after "
              f"{PRE_FLIGHT_TIMEOUT_SECONDS}s. Aborting - do not want to "
              "start this measurement on top of leftover state. Try "
              "again in a bit, or check the gateway/app directly.")
        return
    print("Confirmed inactive.\n")

    confirm = input(
        f"Will send duration={args.sent} to Leave, wait {SETTLE_SECONDS}s, "
        f"read back, then deactivate. Type 'yes' to proceed: "
    ).strip().lower()
    if confirm != "yes":
        print("Aborted.")
        return

    print(f"\nSending duration={args.sent} ...")
    set_response = api.set_scene("Leave", True, duration=args.sent)
    print(f"set_scene response: {set_response}")
    if not set_response.get("success"):
        print("set_scene did NOT report success - stopping here.")
        return

    time.sleep(SETTLE_SECONDS)

    scene_status = api.get_specific_scene("Leave")
    print(f"isActive after set: {scene_status.get('isActive')}")

    raw = api.get_scene_duration("Leave")
    implied_hours = raw * LEAVE_SCENE_MAX_HOURS
    print(
        f"get_scene_duration raw: {raw!r} -> implied {implied_hours:.2f} hours")
    print(f"(x3-formula would predict {args.sent * 3}h, "
          f"fraction-of-12h-formula would predict {min(args.sent, 1) * 12}h "
          f"if clamped like Boost/Party)")

    print("\nDeactivating ...")
    deactivate_response = api.set_scene("Leave", False, duration=0)
    print(f"deactivated: success={deactivate_response.get('success')}")

    print("Confirming deactivation actually took effect ...")
    if wait_until_inactive(api):
        print("Confirmed inactive - clean state for the next isolated run.")
    else:
        print("WARNING: still shows active after deactivation attempt - "
              "check manually before running the next test.")

    print(
        f"\nRESULT for sent={args.sent}: raw={raw!r}, implied={implied_hours:.2f}h")
    print("Bitte diese eine Zeile (plus die volle Ausgabe) zurückspiegeln, "
          "dann den nächsten Wert in einem NEUEN Aufruf testen.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - deliberately broad for a diagnostic script
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
