# Change log:
# - 2026-09-11: v1. New capture addon to investigate Leave's still-unsolved
#   write-side `duration` formula (see CLAUDE.md "Still untested / open"
#   and docs/protocol.md §4d) by capturing the REAL Smile App's own traffic
#   instead of more single-point probes against our own ApiMethods.set_scene()
#   guesses. Follows the mitmproxy technique already documented in CLAUDE.md
#   "Reverse-Engineering Method" point 6 (used previously for
#   switchingtimes/set2) - this is the first time that technique is captured
#   as a reusable repo script instead of being done purely ad-hoc.
#
#   Rationale for capturing full request bodies (not just known params) and
#   ALL /api/scene/* traffic (not just scene/set): every purely numeric-
#   formula hypothesis for Leave's `duration` has already been falsified
#   (see docs/protocol.md §4d) despite rigorous isolated testing. The
#   remaining live hypotheses are (a) the app sends a field we don't know
#   about at all for Leave specifically, or (b) the result is genuinely
#   session/time-dependent in a way our own single-shot probes couldn't see.
#   Capturing the app's own real requests directly, including repeats of the
#   IDENTICAL UI selection, is the only way to distinguish these from "the
#   gateway is genuinely non-deterministic for identical input".
"""mitmproxy capture addon for the Honeywell Smile Connect scene endpoints.

Purpose: capture the real Smile App's own traffic for the `Leave` preset's
duration control, to find out whether identical UI input always produces an
identical wire request/response (deterministic), or whether the app itself
sends something time/session-dependent that our own ApiMethods.set_scene()
calls don't replicate.

Setup (see CLAUDE.md "Reverse-Engineering Method", point 6):
    Route the phone's WLAN traffic through mitmproxy running on this Mac
    (listening on 0.0.0.0 so the phone can reach it over LAN). The gateway
    itself is plain HTTP, so no TLS root certificate is needed on the phone
    for gateway traffic - only needed if you also want to see the app's
    traffic to Honeywell's own cloud (not needed for this investigation).

Usage:
    mitmdump -s scripts/mitm_scene_capture.py

Every /api/scene/* request+response pair is:
    - printed to stdout live (for following along during the session)
    - appended as one JSON object per line to scripts/mitm_scene_capture.log
      (so a session can be reviewed/diffed afterwards without having to
      transcribe anything by hand)

Suggested test protocol for the Leave investigation (see docs/protocol.md
§4d for why): in the Smile App, activate Leave at the SAME duration setting
(e.g. 6h) at least 2-3 times, ideally spaced apart in wall-clock time
(different times of day / different days), deactivating and confirming
inactive between each. Also capture a couple of DIFFERENT duration settings
across the app's full 1-12h range. Compare the captured request bodies
byte-for-byte for the repeated-identical-input case - if they differ, the
app itself is computing something dynamic; if they're identical but the
duration/status readback still differs, the non-determinism is gateway-side.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from mitmproxy import http

LOG_PATH = Path(__file__).resolve().parent / "mitm_scene_capture.log"

# Scope to the scene endpoints relevant to this investigation. Deliberately
# broad within /api/scene/ (not just scene/set and scene/duration) in case
# the app touches an endpoint we haven't considered around a Leave
# activation (e.g. getrooms/status calls we don't already expect).
SCENE_PATH_PREFIX = "/api/scene/"


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
    if SCENE_PATH_PREFIX not in flow.request.path:
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
