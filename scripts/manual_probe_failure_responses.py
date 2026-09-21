# Change log:
# - 2026-09-21 (c): Both remaining steps confirmed live against the real
#   gateway. Step 2: no endpoint exemption needed (see (b) below).
#   Step 3: the real session-expiry payload is
#   {"success": false, "message": "Your session is finished, please log
#   in again.", "loginRejected": true, "product": "honeywell-smile",
#   "language": "en", "performance": 0.09}, now committed as
#   tests/fixtures/session_expired_response.json. Unexpected second
#   finding in the same run: the FIRST attempt (reqcount jumped to
#   999_999) was ACCEPTED - the gateway returned success:true with real
#   room data. It was the SECOND attempt, with a corrupted
#   authorization_token, that got rejected. The gateway enforces the
#   request signature, not a strict counter; see CLAUDE.md's reqcount
#   paragraph for what that does and does not say about the original
#   reqcount finding.
# - 2026-09-21 (b): Fixed step 3, which tested nothing. It corrupted
#   credentials.device_token - but that field is ONLY the challenge token
#   used during login (login.py:93/107/112) and is never touched again
#   afterwards. api_request.py signs with credentials.authorization_token
#   (the DECRYPTED devicetoken) instead, so the corrupted call went
#   through completely normally and returned real room data. Step 3 now
#   invalidates the fields the signature actually depends on, tries them
#   in order, and reports which one the gateway rejects.
#   Step 2 result on the real gateway (2026-09-21): BOTH probed endpoints
#   answer success:true - /api/scene/duration on an inactive scene
#   returns {"success": true, "duration": 0}, and switchingtimes/get2
#   returns success:true. No endpoint exemption is needed in
#   api_request._raise_for_payload().
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
  3. Capture the real session-expiry payload by invalidating the parts
     of the local session the request signature actually depends on -
     reqcount, then the authorization token, then the user id - and
     issuing one more call after each. Its output belongs in
     tests/fixtures/session_expired_response.json, replacing the
     constructed placeholder currently committed there.

Nothing is written to the gateway. Step 3 only breaks this script's own
in-memory session; the gateway is untouched and the next login works
normally. (Note the session IS likely dead for the rest of the run after
the first attempt, which is the point - later attempts are reported but
no longer independent.)
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
from honeywell_smileconnect.api.exceptions import (  # noqa: E402
    SmileConnectApiError,
)
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
    print("  (breaking this script's own session only; the gateway is untouched)")

    # NOT device_token: that is only the challenge token used during login
    # (login.py) and is never referenced again afterwards - corrupting it
    # does nothing at all, which is exactly the mistake the first version
    # of this script made. api_request.request() depends on reqcount,
    # authorization_token (the signature salt) and user_id.
    attempts = (
        (
            "reqcount jumped far ahead",
            # The documented real-world cause of "Your session is finished,
            # please log in again." - see CLAUDE.md's reqcount section.
            lambda: setattr(credentials, "reqcount", 999_999),
        ),
        (
            "authorization token corrupted (signature salt)",
            lambda: setattr(
                credentials, "authorization_token", "invalid-" + credentials.authorization_token
            ),
        ),
        (
            "user id corrupted",
            lambda: setattr(credentials, "user_id", 999_999),
        ),
    )

    for label, break_it in attempts:
        print(f"\n  attempt: {label}")
        break_it()
        try:
            payload = api.get_raw_rooms()
        except SmileConnectApiError as err:
            # The 0.3.1 check is in place, so a rejection raises rather than
            # returning - but the exception carries the raw payload, which
            # is exactly what we came for.
            _dump("SESSION-EXPIRY RESPONSE (goes into tests/fixtures/)", err.payload)
            print(f"  -> rejected via {type(err).__name__}: {err}")
            print("\nCopy the JSON above into")
            print("tests/fixtures/session_expired_response.json, keeping the")
            print("_comment field and recording today's date as the capture date.")
            return
        except Exception as err:  # noqa: BLE001 - diagnostic script
            print(f"  -> unexpected error: {err!r}")
            return

        _verdict("/api/room/list", payload)
        if payload.get("loginRejected") or payload.get("success") is False:
            # Only reachable if run against pre-0.3.1 code.
            _dump("SESSION-EXPIRY RESPONSE (goes into tests/fixtures/)", payload)
            return
        print("  -> still accepted; trying the next attempt")

    print("\n  None of the attempts made the gateway reject the request.")
    print("  That is itself a finding worth recording: this gateway may not")
    print("  validate these fields the way the protocol notes assume. Try")
    print("  rebooting the gateway mid-session instead, and capture what the")
    print("  next poll receives.")


def _duration_params(scene_name: str):
    """Mirror ApiMethods.get_scene_duration()'s own parameter object."""
    from honeywell_smileconnect.api.default_params import DefaultApiParams

    params = DefaultApiParams()
    params.scene = scene_name
    return params


if __name__ == "__main__":
    main()
