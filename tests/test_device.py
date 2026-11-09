# Change log:
# - 2026-11-09 (b): TestEntityUniqueIds - entity unique IDs are scoped to
#   the gateway, and migrated_unique_id() maps every pre-1.0.6 format the
#   platforms produced (and leaves already-migrated IDs alone).
# - 2026-11-09: regler_device_info() gained a gateway_device_id argument and
#   links via `via_device_id` on HA >= 2026.9 (deprecated `via_device`
#   only on older HA). Both branches are tested by patching
#   device._SUPPORTS_VIA_DEVICE_ID, so the result doesn't depend on which
#   HA version the test environment happens to have installed.
# - 2026-08-27: Initial version, regression-tests the exact bug this
#   module was created to fix: weather sensors and climate entities must
#   resolve to the correct hub/sub-device structure, not two unrelated
#   top-level devices (see project discussion "Jetzt sind zwei Devices
#   entstanden").
"""Tests for honeywell_smileconnect.device - the shared device_info builders."""
from __future__ import annotations

import pytest

from custom_components.honeywell_smileconnect import device
from custom_components.honeywell_smileconnect.const import DOMAIN

GATEWAY_DEVICE_ID = "0123456789abcdef0123456789abcdef"


def _regler(room_id=1, room_name="Haus"):
    return device.regler_device_info(
        "abc123", room_id=room_id, room_name=room_name, gateway_device_id=GATEWAY_DEVICE_ID
    )


@pytest.fixture
def new_ha(monkeypatch):
    """HA >= 2026.9: via_device_id is supported."""
    monkeypatch.setattr(device, "_SUPPORTS_VIA_DEVICE_ID", True)


@pytest.fixture
def old_ha(monkeypatch):
    """HA < 2026.9: only the deprecated via_device exists."""
    monkeypatch.setattr(device, "_SUPPORTS_VIA_DEVICE_ID", False)


class TestGatewayDeviceInfo:
    def test_identifier_uses_given_unique_id(self):
        info = device.gateway_device_info("abc123")
        assert info["identifiers"] == {(DOMAIN, "abc123")}

    def test_has_no_via_device(self):
        # The gateway is the top-level hub, not a sub-device of anything.
        info = device.gateway_device_info("abc123")
        assert "via_device" not in info
        assert "via_device_id" not in info

    def test_model_is_gateway_model_not_regler_model(self):
        info = device.gateway_device_info("abc123")
        assert "SDC Regler" not in info["model"]


class TestReglerDeviceInfo:
    def test_identifier_is_scoped_to_gateway_and_room(self):
        assert _regler()["identifiers"] == {(DOMAIN, "abc123_room_1")}

    def test_two_different_rooms_get_different_identifiers(self):
        info_a = _regler(room_id=1, room_name="Haus")
        info_b = _regler(room_id=2, room_name="Keller")
        assert info_a["identifiers"] != info_b["identifiers"]

    def test_model_is_regler_model(self):
        assert _regler()["model"] == "SDC Regler"

    def test_suggested_area_matches_room_name(self):
        assert _regler(room_name="Wohnzimmer")["suggested_area"] == "Wohnzimmer"

    def test_device_name_matches_room_name(self):
        assert _regler(room_name="Wohnzimmer")["name"] == "Wohnzimmer"


class TestReglerLinkNewHa:
    """HA >= 2026.9: link by device-registry id, never the deprecated key."""

    def test_links_to_gateway_via_device_id(self, new_ha):
        assert _regler()["via_device_id"] == GATEWAY_DEVICE_ID

    def test_never_sends_deprecated_via_device(self, new_ha):
        # HA logs a deprecation for via_device, and raises if both are set.
        assert "via_device" not in _regler()


class TestReglerLinkOldHa:
    """HA < 2026.9: via_device_id is unknown there, keep via_device."""

    def test_has_no_via_device_id(self, old_ha):
        assert "via_device_id" not in _regler()

    def test_via_device_matches_gateway_devices_own_identifier(self, old_ha):
        # The two builders must actually agree with each other - this is
        # the crux of the original bug: independently-constructed
        # identifiers that looked similar but didn't actually match.
        (gateway_identifier,) = device.gateway_device_info("abc123")["identifiers"]
        assert _regler()["via_device"] == gateway_identifier


def test_version_switch_matches_running_ha():
    from homeassistant.const import MAJOR_VERSION, MINOR_VERSION

    assert device._SUPPORTS_VIA_DEVICE_ID == ((MAJOR_VERSION, MINOR_VERSION) >= (2026, 9))


class TestEntityUniqueIds:
    """Entity unique IDs carry the gateway, and old ones migrate cleanly."""

    def test_scoped_to_gateway(self):
        assert device.entity_unique_id("abc123", "room_1") == "abc123_room_1"

    def test_two_gateways_do_not_collide(self):
        assert device.entity_unique_id("gw1", "connectivity") != device.entity_unique_id(
            "gw2", "connectivity"
        )

    @pytest.mark.parametrize(
        "suffix",
        [
            "connectivity",
            "ping_response_time",
            "outside_temperature",
            "outside_temperature_min",
            "outside_temperature_max",
            "room_1",
            "room_1_boost_duration",
            "room_1_schedule",
            "room_1_desired_temp_h",
        ],
    )
    def test_legacy_id_migrates_to_what_the_platform_now_builds(self, suffix):
        legacy = f"{DOMAIN}_{suffix}"
        assert device.migrated_unique_id(legacy, "abc123") == device.entity_unique_id(
            "abc123", suffix
        )

    def test_already_migrated_id_is_left_alone(self):
        assert device.migrated_unique_id("abc123_room_1", "abc123") is None

    def test_migration_is_idempotent(self):
        once = device.migrated_unique_id(f"{DOMAIN}_room_1", "abc123")
        assert device.migrated_unique_id(once, "abc123") is None
