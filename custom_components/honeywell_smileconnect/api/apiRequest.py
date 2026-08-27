=== apiRequest.py ===
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
   appended as an extra field.
"""
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

        body_items = sorted_items + [("request_signature", signature)]
        encoded_body = urllib.parse.urlencode(body_items, encoding="utf-8")

        response = requests.post(uri, headers=HEADERS, data=encoded_body, timeout=10)
        _LOGGER.debug("request sent to: %s", uri)
        _LOGGER.debug("response: %s", response.content)

        return json.loads(response.content)

    @staticmethod
    def _build_pipe_signature_string(sorted_items) -> str:
        """Build the `key=value|key=value|...|` string used for signing.

        Arrays are rendered as "[a,b,c]" when they have 2+ elements; a
        single-element list is rendered as the bare value - mirrors
        request.getRequestSignature()'s exact behaviour.
        """
        parts = []
        for key, value in sorted_items:
            if isinstance(value, (list, tuple)):
                if len(value) < 2:
                    rendered = str(value[0]) if value else ""
                else:
                    rendered = "[" + ",".join(str(v) for v in value) + "]"
            else:
                rendered = str(value)
            parts.append(f"{key}={rendered}")
        return "|".join(parts) + "|"
