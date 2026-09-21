# Change log:
# - 2026-09-21: Added TestFailedResponsePropagates - the direct regression
#   test for the production incident that prompted 0.3.1. get_rooms_list()
#   does `raw.get("groups", [])`, so before api_request.py started raising,
#   a session-expiry payload became an EMPTY ROOM LIST: the coordinator
#   reported a successful update with zero rooms and every climate entity
#   raised IndexError every 30 seconds. The point of this test is that a
#   failure must never again be able to look like "no rooms".
# - 2026-09-18 (b): Added a guard test that every DESIRED_TEMP_APP_LIMITS
#   min/max is a multiple of DESIRED_TEMP_STEP (number.py's sliders use
#   these directly as native_min/max_value + native_step).
# - 2026-09-18: Added TestRoundToGatewayStep, TestSetDesiredTemperature and
#   TestSetTemperatureUnchanged, covering the new set_desired_temperature()
#   (change_mode mapping, half-up 0.5 degC rounding, per-type app ranges
#   checked on the rounded value) and guarding set_temperature()
#   (change_mode=0) against accidentally picking up any of that behavior.
# - 2026-09-11: Added TestGetSceneDurationNative, covering
#   get_scene_duration_native()'s two conversion branches (fraction x
#   SCENE_MAX for Boost/Party/Leave, raw-as-is for Holiday) - added to
#   back the new per-room "duration remaining" sensors (sensor.py).
# - 2026-08-27: Initial test using the real captured /api/room/list
#   response (tests/fixtures/room_list_response.json). This is the exact
#   kind of test that would have caught the missing-actualTemperature
#   KeyError regression before it reached a live Home Assistant instance -
#   see CLAUDE.md "Still untested / open" -> now resolved for this field.
"""Tests for honeywell_smileconnect.api.api_methods, using a real gateway
response fixture rather than the generic (and, as it turned out,
inaccurate) HeatApp reference fixtures this project was bootstrapped from.
"""
from __future__ import annotations

import math
from unittest.mock import MagicMock

import pytest

from .conftest import load_fixture
from custom_components.honeywell_smileconnect.api.api_request import ApiRequest
from custom_components.honeywell_smileconnect.api.exceptions import (
    SmileConnectSessionExpired,
)
from custom_components.honeywell_smileconnect.api.api_methods import (
    DESIRED_TEMP_APP_LIMITS,
    DESIRED_TEMP_STEP,
    DESIRED_TEMP_TARGETS,
    ApiMethods,
    round_to_gateway_step,
)


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


class TestGetSceneDurationNative:
    """get_scene_duration_native() must apply the exact conversion recipe
    documented in get_scene_duration()'s docstring: fraction x SCENE_MAX
    for Boost/Party/Leave, raw value as-is for Holiday (already in days).
    """

    def test_fraction_scene_converts_to_real_world_unit(self):
        # Boost: SCENE_MAX=120 (minutes) - a raw fraction of 0.25 is 30min.
        api = _make_api_methods_with_mocked_request({"duration": 0.25})

        assert api.get_scene_duration_native("Boost") == 30

    def test_raw_days_scene_is_returned_unconverted(self):
        # Holiday: RAW_DAYS_DURATION_SCENES - the wire value already IS
        # the real-world unit (days), no SCENE_MAX multiplication.
        api = _make_api_methods_with_mocked_request({"duration": 15})

        assert api.get_scene_duration_native("Holiday") == 15


class TestRoundToGatewayStep:
    @pytest.mark.parametrize(
        "value, expected",
        [
            (21.0, 21.0),
            (21.24, 21.0),
            (21.25, 21.5),  # tie goes UP (banker's round() would give 21.0)
            (21.3, 21.5),
            (21.75, 22.0),
            (12.2, 12.0),
            (18.74, 18.5),
            (23.94957, 24.0),
        ],
    )
    def test_rounds_half_up_to_half_degree(self, value, expected):
        assert round_to_gateway_step(value) == expected


class TestSetDesiredTemperature:
    def _sent_params(self, api):
        return api._request.request.call_args.args[2]

    @pytest.mark.parametrize("target, change_mode", [("N", 1), ("H", 2), ("L", 3)])
    def test_target_maps_to_documented_change_mode(self, target, change_mode):
        api = _make_api_methods_with_mocked_request({"success": True})

        api.set_desired_temperature(14.0 if target == "N" else 18.0, 1, target)

        url = api._request.request.call_args.args[0]
        assert url.endswith("/api/room/settemperature")
        assert self._sent_params(api).change_mode == change_mode

    def test_sends_rounded_temperature_and_room_id(self):
        api = _make_api_methods_with_mocked_request({"success": True})

        api.set_desired_temperature(21.3, 7, "H")

        params = self._sent_params(api)
        assert params.roomid == 7
        assert params.temperature == 21.5

    @pytest.mark.parametrize("bad_target", ["D", "h", ""])
    def test_unknown_target_raises_before_any_request(self, bad_target):
        api = _make_api_methods_with_mocked_request({"success": True})

        with pytest.raises(ValueError):
            api.set_desired_temperature(20.0, 1, bad_target)

        api._request.request.assert_not_called()

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_non_finite_temperature_raises_before_any_request(self, bad):
        api = _make_api_methods_with_mocked_request({"success": True})

        with pytest.raises(ValueError):
            api.set_desired_temperature(bad, 1, "H")

        api._request.request.assert_not_called()

    @pytest.mark.parametrize(
        "target, value",
        [("H", 15), ("H", 25), ("L", 13), ("L", 21), ("N", 12), ("N", 14.5)],
    )
    def test_boundary_values_are_accepted(self, target, value):
        api = _make_api_methods_with_mocked_request({"success": True})

        api.set_desired_temperature(value, 1, target)

        assert self._sent_params(api).temperature == value

    @pytest.mark.parametrize(
        "target, value",
        [
            ("H", 14.5),
            ("H", 25.5),
            ("L", 12.5),
            ("L", 21.5),
            ("N", 11.5),
            ("N", 15.0),
        ],
    )
    def test_out_of_range_raises_before_any_request(self, target, value):
        api = _make_api_methods_with_mocked_request({"success": True})

        with pytest.raises(ValueError, match="allowed range"):
            api.set_desired_temperature(value, 1, target)

        api._request.request.assert_not_called()

    @pytest.mark.parametrize(
        "target, value",
        [
            ("H", 14.8),  # rounds to 15.0 -> accepted
            ("L", 21.2),  # rounds to 21.0 -> accepted
            ("N", 14.7),  # rounds to 14.5 -> accepted
        ],
    )
    def test_range_is_checked_on_the_rounded_value(self, target, value):
        api = _make_api_methods_with_mocked_request({"success": True})

        api.set_desired_temperature(value, 1, target)

        api._request.request.assert_called_once()

    @pytest.mark.parametrize(
        "target, value",
        [
            ("H", 14.7),  # rounds to 14.5 -> rejected although > 14.5
            ("L", 21.3),  # rounds to 21.5 -> rejected although < 21.5
            ("N", 14.8),  # rounds to 15.0 -> rejected
        ],
    )
    def test_rounding_can_push_a_value_out_of_range(self, target, value):
        api = _make_api_methods_with_mocked_request({"success": True})

        with pytest.raises(ValueError):
            api.set_desired_temperature(value, 1, target)

        api._request.request.assert_not_called()

    def test_returns_gateway_response_unchanged(self):
        payload = {"success": True, "roomstatus": 12}
        api = _make_api_methods_with_mocked_request(payload)

        assert api.set_desired_temperature(20.0, 1, "H") == payload

    def test_target_table_matches_documented_fields(self):
        # docs/protocol.md §4g
        assert {
            k: (v["change_mode"], v["field"]) for k, v in DESIRED_TEMP_TARGETS.items()
        } == {
            "H": (2, "desiredTempDay"),
            "L": (3, "desiredTempDay2"),
            "N": (1, "desiredTempNight"),
        }

    def test_limits_cover_every_target(self):
        assert set(DESIRED_TEMP_APP_LIMITS) == set(DESIRED_TEMP_TARGETS)

    @pytest.mark.parametrize("target", sorted(DESIRED_TEMP_APP_LIMITS))
    def test_limits_are_on_the_step_grid(self, target):
        # number.py uses min/max/step directly for its sliders.
        limits = DESIRED_TEMP_APP_LIMITS[target]
        assert limits["min"] < limits["max"]
        assert limits["min"] % DESIRED_TEMP_STEP == 0
        assert limits["max"] % DESIRED_TEMP_STEP == 0


class TestSetTemperatureUnchanged:
    def test_still_uses_change_mode_zero_and_does_not_round(self):
        api = _make_api_methods_with_mocked_request({"success": True})

        api.set_temperature(21.3, 1)

        params = api._request.request.call_args.args[2]
        assert params.change_mode == 0
        assert params.temperature == 21.3


class TestFailedResponsePropagates:
    """A gateway failure must never be able to look like "no rooms".

    Regression test for the 2026-09-21 production incident: with the real
    ApiRequest in place, a session-expiry payload now raises inside
    request() instead of reaching get_rooms_list(), whose
    `raw.get("groups", [])` would otherwise turn it into an empty list -
    and an empty list made the coordinator report SUCCESS with zero rooms.
    """

    @staticmethod
    def _api_with_real_check(payload: dict) -> ApiMethods:
        """ApiMethods whose transport is mocked but whose response CHECK is real."""
        api = ApiMethods(credentials=MagicMock(), base_url="http://192.168.1.132")
        api._request = MagicMock()

        def _request(uri, _credentials, _params):
            ApiRequest._raise_for_payload(uri, payload)
            return payload

        api._request.request.side_effect = _request
        return api

    def test_get_rooms_list_raises_instead_of_returning_empty(self):
        api = self._api_with_real_check(load_fixture("session_expired_response.json"))
        with pytest.raises(SmileConnectSessionExpired):
            api.get_rooms_list()

    def test_get_specific_room_raises_instead_of_returning_none(self):
        # Just as bad in its own way: a None here made a desired-temperature
        # write report "Room X was not found after writing" - a real error,
        # but with a completely misleading diagnosis.
        api = self._api_with_real_check(load_fixture("session_expired_response.json"))
        with pytest.raises(SmileConnectSessionExpired):
            api.get_specific_room(1)

    def test_healthy_payload_still_parses(self):
        api = self._api_with_real_check(load_fixture("room_list_response.json"))
        assert len(api.get_rooms_list()) >= 1
