# Change log:
# - 2026-08-27: Initial test using the real captured /api/room/list
#   response (tests/fixtures/room_list_response.json). This is the exact
#   kind of test that would have caught the missing-actualTemperature
#   KeyError regression before it reached a live Home Assistant instance -
#   see CLAUDE.md "Still untested / open" -> now resolved for this field.
"""Tests for honeywell_smileconnect.api.apiMethods, using a real gateway
response fixture rather than the generic (and, as it turned out,
inaccurate) HeatApp reference fixtures this project was bootstrapped from.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from conftest import load_fixture
from honeywell_smileconnect.api.apiMethods import ApiMethods


def _make_api_methods_with_mocked_request(fixture_payload: dict) -> ApiMethods:
    api = ApiMethods(credentials=MagicMock(), base_url="http://192.168.1.132")
    api._request = MagicMock()
    api._request.request.return_value = fixture_payload
    return api


class TestGetRoomsListWithRealFixture:
    def test_returns_one_room_matching_real_gateway_data(self):
        fixture = load_fixture("room_list_response.json")
        api = _make_api_methods_with_mocked_request(fixture)

        rooms = api.get_rooms_list()

        assert len(rooms) == 1
        assert rooms[0]["name"] == "Haus"
        assert rooms[0]["data"]["id"] == 1
        assert rooms[0]["data"]["originalName"] == "Regler MK1"

    def test_real_room_has_no_actualTemperature_field(self):
        # Documents and locks in the real-world discrepancy vs. the generic
        # HeatApp reference fixtures: this field is genuinely absent here,
        # not just null. Any code reading this field MUST use .get(), not
        # direct indexing - see climate.py.
        fixture = load_fixture("room_list_response.json")
        api = _make_api_methods_with_mocked_request(fixture)

        rooms = api.get_rooms_list()

        assert "actualTemperature" not in rooms[0]["data"]

    def test_get_specific_room_finds_by_id(self):
        fixture = load_fixture("room_list_response.json")
        api = _make_api_methods_with_mocked_request(fixture)

        room = api.get_specific_room(1)

        assert room is not None
        assert room["name"] == "Haus"

    def test_get_specific_room_returns_none_for_unknown_id(self):
        fixture = load_fixture("room_list_response.json")
        api = _make_api_methods_with_mocked_request(fixture)

        assert api.get_specific_room(999) is None
