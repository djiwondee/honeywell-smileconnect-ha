"""Unauthenticated /api/ping endpoint - gateway reachability check.

Deliberately independent from login.py/credentials.py/apiRequest.py: the
whole point of this endpoint is to report reachability even when
authentication itself is broken, so it must not depend on any authenticated
session state, device token, or signing logic. See CLAUDE.md "Known API
Endpoints" - /api/ping is documented as requiring no authentication.
"""
# Change log:
# - 2026-08-27: Initial version, for the new independent ping_coordinator.py
#   / binary_sensor.py connectivity feature.
from __future__ import annotations

import json
import logging

import requests

_LOGGER = logging.getLogger(__name__)

HEADERS = {
    "Accept": "application/json, application/xml, text/plain, text/html, *.*",
}

TIMEOUT_SECONDS = 10


def ping(base_url: str) -> dict:
    """GET /api/ping and return the parsed JSON response.

    Raises on failure (requests.RequestException, json.JSONDecodeError) -
    callers (SmileConnectPingCoordinator) are responsible for translating
    that into "not reachable" entity state via UpdateFailed, rather than
    this function silently swallowing errors.
    """
    uri = base_url + "/api/ping"
    response = requests.get(uri, headers=HEADERS, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = json.loads(response.content)
    _LOGGER.debug("ping response: %s", payload)
    return payload
