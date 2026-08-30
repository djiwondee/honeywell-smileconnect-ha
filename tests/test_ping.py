# Change log:
# - 2026-08-27: Initial version. Note the fixture used here has a different
#   provenance than most others - see ping_response.json's own _comment
#   field for details (transcribed from pre-existing user documentation,
#   not live-captured in a chat session).
"""Tests for honeywell_smileconnect.api.ping.

Verifies the unauthenticated GET request is built correctly and the
response is parsed as expected - deliberately does NOT touch Login,
Credentials, or any signing logic, mirroring the module's own design
constraint (must work independently of authentication state).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from .conftest import load_fixture
from custom_components.honeywell_smileconnect.api import ping as ping_api


class _FakeResponse:
    def __init__(self, payload: dict):
        self.content = json.dumps(payload).encode("utf-8")

    def raise_for_status(self):
        pass


class TestPing:
    def test_parses_real_documented_response(self):
        fixture = load_fixture("ping_response.json")

        with patch("requests.get", return_value=_FakeResponse(fixture)) as mock_get:
            result = ping_api.ping("http://192.168.1.132")

        assert result["success"] is True
        assert result["uniqueid"] == "[0134f0]"
        mock_get.assert_called_once()

    def test_calls_correct_endpoint_path(self):
        fixture = load_fixture("ping_response.json")

        with patch("requests.get", return_value=_FakeResponse(fixture)) as mock_get:
            ping_api.ping("http://192.168.1.132")

        called_url = mock_get.call_args[0][0]
        assert called_url == "http://192.168.1.132/api/ping"

    def test_does_not_send_any_authentication_headers_or_body(self):
        # This endpoint must remain reachable even when auth is fully
        # broken - so the call must not carry a signature, devicetoken, or
        # any request body at all.
        fixture = load_fixture("ping_response.json")

        with patch("requests.get", return_value=_FakeResponse(fixture)) as mock_get:
            ping_api.ping("http://192.168.1.132")

        _, kwargs = mock_get.call_args
        assert "data" not in kwargs
        assert "auth" not in kwargs

    def test_raises_when_response_status_indicates_failure(self):
        fake_response = MagicMock()
        fake_response.raise_for_status.side_effect = Exception("HTTP 500")

        with patch("requests.get", return_value=fake_response):
            try:
                ping_api.ping("http://192.168.1.132")
                assert False, "expected an exception to propagate"
            except Exception as exc:  # noqa: BLE001
                assert "HTTP 500" in str(exc)
