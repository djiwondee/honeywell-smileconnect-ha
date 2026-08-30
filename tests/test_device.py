# Change log:
# - 2026-08-27: Initial version, regression-tests the exact bug this
#   module was created to fix: weather sensors and climate entities must
#   resolve to the correct hub/sub-device structure, not two unrelated
#   top-level devices (see project discussion "Jetzt sind zwei Devices
#   entstanden").
"""Tests for honeywell_smileconnect.device - the shared device_info builders."""
from __future__ import annotations

from custom_components.honeywell_smileconnect import device
from custom_components.honeywell_smileconnect.const import DOMAIN


class TestGatewayDeviceInfo:
    def test_identifier_uses_given_unique_id(self):
        info = device.gateway_device_info("abc123")
        assert info["identifiers"] == {(DOMAIN, "abc123")}

    def test_has_no_via_device(self):
        # The gateway is the top-level hub, not a sub-device of anything.
        info = device.gateway_device_info("abc123")
        assert "via_device" not in info

    def test_model_is_gateway_model_not_regler_model(self):
        info = device.gateway_device_info("abc123")
        assert "SDC Regler" not in info["model"]


class TestReglerDeviceInfo:
    def test_identifier_is_scoped_to_gateway_and_room(self):
        info = device.regler_device_info("abc123", room_id=1, room_name="Haus")
        assert info["identifiers"] == {(DOMAIN, "abc123_room_1")}

    def test_two_different_rooms_get_different_identifiers(self):
        info_a = device.regler_device_info("abc123", room_id=1, room_name="Haus")
        info_b = device.regler_device_info("abc123", room_id=2, room_name="Keller")
        assert info_a["identifiers"] != info_b["identifiers"]

    def test_links_to_gateway_via_device(self):
        info = device.regler_device_info("abc123", room_id=1, room_name="Haus")
        assert info["via_device"] == (DOMAIN, "abc123")

    def test_via_device_matches_gateway_devices_own_identifier(self):
        # The two builders must actually agree with each other - this is
        # the crux of the original bug: independently-constructed
        # identifiers that looked similar but didn't actually match.
        gateway_info = device.gateway_device_info("abc123")
        regler_info = device.regler_device_info("abc123", room_id=1, room_name="Haus")

        (gateway_identifier,) = gateway_info["identifiers"]
        assert regler_info["via_device"] == gateway_identifier

    def test_model_is_regler_model(self):
        info = device.regler_device_info("abc123", room_id=1, room_name="Haus")
        assert info["model"] == "SDC Regler"

    def test_suggested_area_matches_room_name(self):
        info = device.regler_device_info("abc123", room_id=1, room_name="Wohnzimmer")
        assert info["suggested_area"] == "Wohnzimmer"

    def test_device_name_matches_room_name(self):
        info = device.regler_device_info("abc123", room_id=1, room_name="Wohnzimmer")
        assert info["name"] == "Wohnzimmer"
