# Change log:
# - 2026-09-17: v1. New capture addon to investigate whether/how
#   `desiredTempDay`/`desiredTempDay2`/`desiredTempNight` (the three fixed
#   per-room temperatures a switching-time slot's `type` selects between -
#   confirmed 2026-09-15/17, see CLAUDE.md "TOP PRIORITY" under "Next
#   planned work") can be WRITTEN. Reading them is already free via
#   `/api/room/list` (`ApiMethods.get_rooms_list()`), but nothing in this
#   project has ever tried to write them, and `/api/room/settemperature`'s
#   `change_mode` parameter is only ever sent as `0` (live setpoint) by
#   `set_temperature()` today - whether other `change_mode` values address
#   these fields, or a different endpoint entirely is needed, is unknown.
#
#   Scoped to ALL `/api/` traffic (broader than the precedent
#   `mitm_scene_capture.py`, which scoped to `/api/scene/`), because the
#   user reports these values are set in the Smile App's WEEKLY SCHEDULE
#   screen (the same screen that drives `switchingtimes/set2`), not
#   anywhere obviously room-settemperature-shaped - the app may well call a
#   completely different, not-yet-seen endpoint for this. Following this
#   project's own validation principle (CLAUDE.md "Reverse-Engineering
#   Method"): capture broadly and let the real traffic tell us the
#   endpoint, rather than guessing narrow and risking a second capture
#   session.
"""mitmproxy capture addon for the desiredTempDay/Day2/Night write investigation.

Purpose: capture the real Smile App's own traffic while the user sets and
saves "Comfort Hi"/"Comfort Lo" (desiredTempDay/desiredTempDay2) for a room
in the app's weekly schedule screen, to find out which endpoint and wire
format the app actually uses to write these values - unlike the scene
duration investigation this generalizes from, the target endpoint itself is
not yet known here.

Setup (see CLAUDE.md "Reverse-Engineering Method", point 6):
    Route the phone's WLAN traffic through mitmproxy running on this Mac
    (listening on 0.0.0.0 so the phone can reach it over LAN). The gateway
    itself is plain HTTP, so no TLS root certificate is needed on the phone
    for gateway traffic - only needed if you also want to see the app's
    traffic to Honeywell's own cloud (not needed for this investigation).

Usage:
    mitmdump -s scripts/mitm_desired_temp_capture.py

Every /api/* request+response pair is:
    - printed to stdout live (for following along during the session)
    - appended as one JSON object per line to
      scripts/mitm_desired_temp_capture.log (so the session can be
      reviewed/diffed afterwards without transcribing anything by hand)

Suggested test protocol:
    1. Start the capture, then open the Smile App's weekly schedule screen
       for the room under test.
    2. Change "Comfort Hi" (desiredTempDay) to a distinctive, easy-to-spot
       value (e.g. 23.5) and save. Note the exact value and timestamp.
    3. Repeat for "Comfort Lo" (desiredTempDay2) with a different
       distinctive value (e.g. 17.5) and save.
    4. If the screen supports it, also try changing both in a single save
       to see whether the app batches them into one request or sends two.
    5. Afterwards, grep the log for the two known-changed values to find
       the request(s) that carried them, and check the *response* body of
       the following `/api/room/list` (or equivalent) call to confirm the
       gateway actually echoed the new values back - matching this
       project's long-standing "verify via a fresh read, not just
       success:true" principle (see CLAUDE.md's many api_methods.py
       incidents).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from mitmproxy import http

LOG_PATH = Path(__file__).resolve().parent / "mitm_desired_temp_capture.log"

# Deliberately broad (all of /api/), not scoped to /api/room/ - see the
# change-log header above for why: the app screen involved is the weekly
# schedule editor, which already drives a different endpoint
# (switchingtimes/set2) for slot data, so the endpoint for the two fixed
# per-slot-type temperatures is genuinely unknown up front.
API_PATH_PREFIX = "/api/"


def _body_as_dict(message: http.Request | http.Response) -> dict:
    """Best-effort decode of a form-urlencoded or JSON body into a dict.
    Falls back to the raw decoded text if neither applies, since we'd
    rather capture something unparsed than silently drop an unexpected
    body shape."""
    raw = message.get_text(strict=False) or ""
    if not raw:
        return {}
    try:
        return dict(message.urlencoded_form)  # type: ignore[union-attr]
    except AttributeError:
        pass
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"_raw_text": raw}


def response(flow: http.HTTPFlow) -> None:
    if API_PATH_PREFIX not in flow.request.path:
        return

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": flow.request.method,
        "path": flow.request.path,
        "request_body": _body_as_dict(flow.request),
        "response_status": flow.response.status_code if flow.response else None,
        "response_body": _body_as_dict(flow.response) if flow.response else None,
    }

    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"\n[{entry['timestamp']}] {entry['method']} {entry['path']}")
    print(f"  request_body:  {entry['request_body']}")
    print(f"  response_body: {entry['response_body']}")
