"""Signed request execution against the Smile Connect gateway.

Signing differs from standard HeatApp: parameters are joined with `|`
(pipe) rather than `&`, with a trailing pipe, before hashing - see
docs/protocol.md §2.
"""
from __future__ import annotations

import json
import logging
import urllib.parse

import requests
from Crypto.Hash import MD5

from .credentials import Credentials

_LOGGER = logging.getLogger(__name__)

HEADERS = {
    "Accept": "application/json, application/xml, text/plain, text/html, *.*",
    "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
}


class ApiRequest:
    """Builds, signs, and executes a single authenticated API request."""

    def request(self, uri: str, credentials: Credentials, data_object) -> dict:
        params = dict(vars(data_object))
        params["udid"] = credentials.udid
        params["userid"] = credentials.user_id
        params["reqcount"] = credentials.next_reqcount()

        sorted_items = sorted(params.items(), key=lambda kv: kv[0])

        data_string = self._build_pipe_signature_string(sorted_items)
        signature_input = data_string + credentials.authorization_token
        signature = MD5.new(signature_input.encode("utf-8")).hexdigest()

        body_items = sorted_items + [("request_signature", signature)]
        encoded_body = urllib.parse.urlencode(body_items, encoding="utf-8")

        response = requests.post(uri, headers=HEADERS, data=encoded_body, timeout=10)
        _LOGGER.debug("request sent to: %s", uri)
        _LOGGER.debug("response: %s", response.content)

        return json.loads(response.content)

    @staticmethod
    def _build_pipe_signature_string(sorted_items) -> str:
        """Build the `key=value|key=value|...|` string used for signing.

        Arrays are rendered as "[a,b,c]"; a single-element list is rendered
        as the bare value (mirrors the reference JS behaviour).
        """
        parts = []
        for key, value in sorted_items:
            if isinstance(value, (list, tuple)):
                if len(value) == 0:
                    rendered = ""
                elif len(value) == 1:
                    rendered = str(value[0])
                else:
                    rendered = "[" + ",".join(str(v) for v in value) + "]"
            else:
                rendered = str(value)
            parts.append(f"{key}={rendered}")
        return "|".join(parts) + "|"
