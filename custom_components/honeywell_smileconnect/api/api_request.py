"""Signed request execution against the Smile Connect gateway.

Signing was confirmed by extracting the gateway's own JS
(request.getRequestSignature / request.encodeRequestSignature - see
CLAUDE.md and docs/protocol.md):

1. Sort all parameters (including udid, userid, reqcount) alphabetically by
   key. Any key whose value is None/undefined is dropped entirely - both
   from the signature input AND from the request body.
2. Build "key=value|key=value|...|" (pipe-joined, trailing pipe). A
   single-element list renders as the bare value; a multi-element list
   renders as "[a,b,c]".
3. Signature = Base64(PBKDF2-HMAC-SHA512(
       password = charcodes(data_string),
       salt     = charcodes(devicetoken_plaintext),
       iterations = 1, dkLen = 64 bytes))
   NOT MD5 - this differs from the generic HeatApp protocol, which uses
   plain MD5(data_string + devicetoken).
4. The actual HTTP body uses standard `&`-separated URL encoding (the pipe
   form is only used to compute the signature), plus request_signature
   appended as an extra field. CRITICAL: array-valued parameters in the
   body must use the EXACT SAME rendering rule as step 2 above (see
   _render_value) - see change log below for the real bug this fixes.
"""
# Change log:
# - 2026-08-30 (d): Fixed a third bug in the same function, found after
#   the empty-array skip-fix eliminated the timeout but the user reported
#   Standby still never actually deactivating: /api/scene/set's "active"
#   parameter is a Python bool, and _render_value() fell through to plain
#   str() for it - producing "True"/"False" (capitalized). The gateway's
#   own JS coerces booleans to lowercase "true"/"false" in string
#   concatenation (`"active=" + false` -> "active=false" in JS). Every
#   /api/scene/set call was very likely sending a value the gateway either
#   ignored or misinterpreted as truthy regardless of the intended
#   True/False - explaining why set_scene(active=False) always returned
#   success:true but the scene never actually deactivated (confirmed via
#   live log: scene/status polled immediately after showed Standby still
#   isActive:true, repeatedly, across multiple attempts). Fixed by adding
#   an explicit bool branch to _render_value(), checked before the list/
#   tuple and generic str() branches (bool is a subclass of int in
#   Python, but must never render as "1"/"0" or "True"/"False" here).
# - 2026-08-30 (c): Fixed empty-array rendering: was "" (empty string),
#   should be the literal string "undefined" - re-derived precisely from
#   the gateway's own extracted JS (`g.length<2 ? f+"="+g[0] : ...` -
#   for an empty array, g[0] is JavaScript's `undefined`, and string-
#   concatenating it produces the literal text "undefined", not an empty
#   string). This was wrong from the very first version of this file and
#   never surfaced until the user hit a real production timeout removing
#   the last/only room from a scene (Standby deactivation on a
#   single-room installation - exactly the empty-array case). The
#   gateway's firmware appears to hang/timeout on a genuinely empty
#   "rooms=" value rather than returning a clean error.
# - 2026-08-30 (b): Fixed a real, previously-undiscovered bug: the HTTP
#   body was built from the raw sorted_items (Python lists for array-
#   valued params like scene/setrooms's "rooms") passed straight to
#   urllib.parse.urlencode(), which stringifies non-string values with
#   Python's default str() - producing "[1]" for a single-element list
#   (should be the bare value "1" per the protocol, confirmed by the
#   original setrooms.post fixture) and "[6, 7, 8, 9]" with spaces for
#   multi-element lists (should be "[6,7,8,9]", no spaces - also confirmed
#   by that same fixture). This meant the actual request body diverged
#   from both the string the signature was computed over AND the
#   protocol's expected wire format, for every array-valued parameter -
#   i.e. every single scene/setrooms call, which is exercised on every
#   preset/hvac_mode change via scene_manager.py's add_member_to_scene/
#   remove_member_from_scene. Root-caused after the user reported climate
#   entity mode/preset changes behaving inconsistently in production HA
#   (sometimes working, sometimes silently not taking effect) - this bug
#   plausibly also explains an much earlier observation
#   (manual_check_roomstatus.py showing roomstatus stuck at 12/Standby
#   regardless of which scene was toggled), previously attributed solely
#   to Standby-stacking. Fixed by extracting the array-rendering rule into
#   a single shared _render_value() helper, used for BOTH the signature
#   string AND the actual body values now - they can no longer drift
#   apart again by construction.
# - 2026-08-30 (a): Renamed from apiRequest.py to api_request.py for PEP 8 /
#   ruff N999 compliance (module names must be snake_case). The ApiRequest
#   class name itself is unchanged - only the file/import path changed.
from __future__ import annotations

import json
import logging
import urllib.parse

import requests

from . import crypto
from .credentials import Credentials

_LOGGER = logging.getLogger(__name__)

HEADERS = {
    "Accept": "application/json, application/xml, text/plain, text/html, *.*",
    "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
}


class ApiRequest:
    """Builds, signs, and executes a single authenticated API request."""

    def request(self, uri: str, credentials: Credentials, data_object) -> dict:
        params = {k: v for k, v in vars(data_object).items() if v is not None}
        params["udid"] = credentials.udid
        params["userid"] = credentials.user_id
        params["reqcount"] = credentials.next_reqcount()

        sorted_items = sorted(params.items(), key=lambda kv: kv[0])

        data_string = self._build_pipe_signature_string(sorted_items)
        signature = crypto.pbkdf2_base64(
            crypto.string_to_charcodes(data_string),
            crypto.string_to_charcodes(credentials.authorization_token),
        )

        # Body values go through the SAME rendering rule as the signature
        # string (see _render_value) - using the raw Python values here
        # (e.g. an actual list for an array param) would let urlencode's
        # default str() produce a different, protocol-incorrect string
        # than what the signature was computed over. See module change log.
        body_items = [(key, self._render_value(value)) for key, value in sorted_items]
        body_items.append(("request_signature", signature))
        encoded_body = urllib.parse.urlencode(body_items, encoding="utf-8")

        response = requests.post(uri, headers=HEADERS, data=encoded_body, timeout=10)
        _LOGGER.debug("request sent to: %s", uri)
        _LOGGER.debug("response: %s", response.content)

        return json.loads(response.content)

    @staticmethod
    def _render_value(value) -> str:
        """Render a single parameter value exactly as the protocol expects
        on the wire: booleans as lowercase "true"/"false" (JS string-
        coercion convention - see module change log), arrays with 2+
        elements as "[a,b,c]" (no spaces), a single-element array as the
        bare value, an empty array as the literal "undefined", everything
        else via plain str(). This is the SINGLE source of truth for
        value rendering - used both for the body and for the signature
        string, so they cannot drift apart again.
        """
        if isinstance(value, bool):
            # Checked BEFORE list/tuple and the generic str() fallback -
            # bool is a subclass of int in Python, but must render as
            # "true"/"false" (matching the gateway's own JS), never
            # "1"/"0" or Python's capitalized "True"/"False". Getting this
            # wrong meant /api/scene/set(active=False) always returned
            # success:true without the gateway ever actually deactivating
            # the scene - see module change log.
            return "true" if value else "false"
        if isinstance(value, (list, tuple)):
            if len(value) < 2:
                return str(value[0]) if value else "undefined"
            return "[" + ",".join(str(v) for v in value) + "]"
        return str(value)

    @classmethod
    def _build_pipe_signature_string(cls, sorted_items) -> str:
        """Build the `key=value|key=value|...|` string used for signing.

        Uses the same _render_value() as the actual request body - see
        module change log for why that matters.
        """
        parts = [f"{key}={cls._render_value(value)}" for key, value in sorted_items]
        return "|".join(parts) + "|"
