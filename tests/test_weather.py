# Change log:
# - 2026-08-27: Initial version. Uses the real /api/weather response
#   captured manually via scripts/manual_check_weather.py (see CLAUDE.md
#   "Test Suite" section for the capture-then-test pattern this follows).
"""Tests for honeywell_smileconnect.api.apiMethods.get_weather(), using a
real gateway response fixture rather than an unverified assumption.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from .conftest import load_fixture
from custom_components.honeywell_smileconnect.api.apiMethods import ApiMethods


class TestGetWeatherWithRealFixture:
    def test_returns_real_gateway_fields(self):
        fixture = load_fixture("weather_response.json")
        api = ApiMethods(credentials=MagicMock(), base_url="http://192.168.1.132")
        api._request = MagicMock()
        api._request.request.return_value = fixture

        weather = api.get_weather()

        assert weather["success"] is True
        assert weather["temperature"] == 29.5
        assert weather["min"] == 19.5
        assert weather["max"] == 29.5

    def test_temperature_values_are_floats_not_ints(self):
        # Confirms this endpoint returns decimal values on real hardware -
        # relevant context for the still-open "decimal temperature values"
        # question in CLAUDE.md regarding /api/room/settemperature.
        fixture = load_fixture("weather_response.json")
        assert isinstance(fixture["temperature"], float)
        assert isinstance(fixture["min"], float)
        assert isinstance(fixture["max"], float)
