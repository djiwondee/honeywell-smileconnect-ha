# Change log:
# - 2026-09-21: Initial version. Pre-flight for the 0.3.1 session-recovery
#   fix: api_request.py now raises on a failed gateway response, which
#   changes the behaviour of EVERY endpoint at once. Per this project's
#   standing rule (verify live before adopting), the two things the code
#   audit could not settle on its own get checked against the real
#   gateway first, and the real session-expiry payload gets captured
#   instead of guessed.
"""Manual diagnostic: how does this gateway report FAILURE?

Run this in the dev container terminal, BEFORE merging the 0.3.1 fix:

    python3 scripts/manual_probe_failure_responses.py

Three steps, all read-only against the gateway except step 3, which only
invalidates THIS script's own in-memory session token:

  1. Baseline - confirm a normal /api/room/list succeeds, so a failure
     later in the run means something.
  2. Do any ROUTINE calls answer `success: false`? The audit could not
     rule this out from the code for two endpoints: /api/scene/duration
     on an INACTIVE scene, and switchingtimes/get2. If either does, it
     needs an explicit exemption in api_request._raise_for_payload()
     rather than a hard raise - otherwise the fix would break a path that
     works today.
  3. Capture the real session-expiry payload by corrupting the device
     token and issuing one more call. Its output belongs in
     tests/fixtures/session_expired_response.json, replacing the
     constructed placeholder currently committed there.

Nothing is written to the gateway. Step 3 only breaks the local session
object; the gateway is untouched and the next login works normally.
"""
from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path

# Make the HA-independent api/ layer importable without needing Home
# Assistant installed - mirrors tests/conftest.py's approach.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "custom_components"))

from honeywell_smileconnect.api.api_methods import ApiMethods  # noqa: E402
from honeywell_smileconnect.api.login import Login  # noqa: E402

SEPARATOR = "=" * 68


def _dump(label: str, payload) -> None:
    print(SEPARATOR)
    print(label)
    print(SEPARATOR)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print()


def _verdict(label: str, payload: dict) -> None:
    """Report whether this response would now raise, and what that means."""
    if payload.get("loginRejected"):
        print(f"  -> {label}: loginRejected=true (would raise SmileConnectSessionExpired)")
    elif payload.get("success") is False:
        print(f"  -> {label}: success=false  ** WOULD NOW RAISE - needs an exemption **")
    elif payload.get("success") is True:
        print(f"  -> {label}: success=true (unaffected)")
    else:
        print(f"  -> {label}: no 'success' field at all (unaffected - we check `is False`)")


def main() -> None:
    host = input("Gateway host/IP [192.168.1.132]: ").strip() or "192.168.1.132"
    username = input("Username: ").strip()
    password = getpass.getpass("Password (hidden): ")

    base_url = f"http://{host}"
    print(f"\nLogging in to {base_url} ...")
    credentials = Login(base_url).authorize(username, password)
    api = ApiMethods(credentials, base_url)
    print("Login successful.\n")

    # --- Step 1: baseline -------------------------------------------------
    print("STEP 1 - baseline /api/room/list")
    rooms_raw = api.get_raw_rooms()
    _verdict("/api/room/list", rooms_raw)
    rooms = [
        {"id": room["id"], "name": room["name"]}
        for group in rooms_raw.get("groups", [])
        for room in group.get("rooms", [])
    ]
    print(f"  -> {len(rooms)} room(s): {rooms}\n")
    if not rooms:
        print("No rooms reported - cannot continue meaningfully. Aborting.")
        return

    room = rooms[0]

    # --- Step 2: do routine calls ever say success:false? -----------------
    print("STEP 2 - do any ROUTINE calls report success:false?")

    scene_status = api.get_scene_status()
    _verdict("/api/scene/status", scene_status)
    active = {s["name"] for s in scene_status.get("scenes", []) if s.get("isActive")}
    inactive = [s["name"] for s in scene_status.get("scenes", []) if not s.get("isActive")]
    print(f"  -> active scenes: {sorted(active) or 'none'}")

    if inactive:
        # The interesting case: duration of a scene that is NOT running.
        probe_scene = inactive[0]
        print(f"  -> probing /api/scene/duration for INACTIVE scene {probe_scene!r}")
        duration_raw = api._request.request(  # noqa: SLF001 - diagnostic script
            api.base_url + "/api/scene/duration",
            api.credentials,
            _duration_params(probe_scene),
        )
        _dump(f"/api/scene/duration ({probe_scene}, inactive)", duration_raw)
        _verdict("/api/scene/duration (inactive scene)", duration_raw)
    else:
        print("  -> every scene is active; cannot probe the inactive case right now.")

    switching = api.get_switching_times(room["name"], room["id"])
    _verdict("switchingtimes/get2", switching)
    print(f"  -> {len(switching.get('switchingtimes', []))} slot entries\n")

    # --- Step 3: capture the real session-expiry payload ------------------
    print("STEP 3 - capturing the real session-expiry payload")
    print("  (corrupting this script's own device token; the gateway is untouched)")
    credentials.device_token = "invalid-" + credentials.device_token
    try:
        expired = api.get_raw_rooms()
    except Exception as err:  # noqa: BLE001 - diagnostic script
        print(f"  -> the call raised instead of returning a payload: {err!r}")
        print("     (expected once the 0.3.1 check is in place - run this on the")
        print("      pre-fix code, or temporarily disable _raise_for_payload, to")
        print("      capture the raw body)")
        return

    _dump("SESSION-EXPIRY RESPONSE (goes into tests/fixtures/)", expired)
    _verdict("/api/room/list with a broken token", expired)
    print("Copy the JSON above into tests/fixtures/session_expired_response.json,")
    print("keeping the existing _comment field and noting today's date in it.")


def _duration_params(scene_name: str):
    """Mirror ApiMethods.get_scene_duration()'s own parameter object."""
    from honeywell_smileconnect.api.default_params import DefaultApiParams

    params = DefaultApiParams()
    params.scene = scene_name
    return params


if __name__ == "__main__":
    main()
