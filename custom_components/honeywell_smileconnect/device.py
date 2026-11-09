"""Shared Home Assistant device_info builders for this integration.

Centralized here so climate.py, sensor.py, and binary_sensor.py all agree
on the exact same device identifiers - previously each platform built its
own device_info dict independently, which caused sensor.py's weather
entities to end up on a *different* device than intended (see project
discussion: "Jetzt sind zwei Devices entstanden" - two devices appeared
where only the gateway/regler split was intended, because of ad-hoc,
inconsistent identifiers). Both builders below take the gateway's stable
unique_id (from the config entry, captured via /api/ping's "uniqueid"
during setup - see config_flow.py) as their anchor.

Physical model this reflects (see CLAUDE.md "Project Goal" / architecture
discussion): the Smile Connect Gateway is the single physical hub; each SDC
Regler is a separate physical thermostat/controller talking to the gateway
over the Smile Bus. HA device hierarchy: gateway is the top-level hub
device, each regler is a sub-device linked to it (`via_device_id`, or
`via_device` on HA < 2026.9).
"""
# Change log:
# - 2026-11-09: regler_device_info() takes the gateway's device-registry id
#   and links to it via `via_device_id` on HA >= 2026.9. HA deprecated the
#   identifier-tuple `via_device` (removal announced for
#   2027.8.0). Older HA versions do not know `via_device_id` and reject
#   unknown device_info keys, so they keep getting `via_device` - the
#   switch is _SUPPORTS_VIA_DEVICE_ID. Never send both: HA raises if it
#   gets both keys. Drop the switch once hacs.json's floor is >= 2026.9.
# - 2026-08-27: Initial version, extracted from climate.py/sensor.py to fix
#   the two-devices-instead-of-hub/sub-device bug and add via_device +
#   suggested_area support.
from __future__ import annotations

from homeassistant.const import MAJOR_VERSION, MINOR_VERSION

from .const import DOMAIN

# `via_device_id` was added to DeviceInfo in HA 2026.9.0 (checked against
# the home-assistant/core tags: absent in 2026.8.0, present in 2026.9.0).
_SUPPORTS_VIA_DEVICE_ID = (MAJOR_VERSION, MINOR_VERSION) >= (2026, 9)

GATEWAY_NAME = "Smile Connect Gateway"
GATEWAY_MODEL = "Smile Connect (SCN-10)"
REGLER_MODEL = "SDC Regler"
MANUFACTURER = "Honeywell"


def gateway_device_info(gateway_unique_id: str) -> dict:
    """Device info for the single physical Smile Connect gateway.

    All entities that are not tied to one specific room/regler (weather
    sensors, connectivity/response-time diagnostics) belong here.
    """
    return {
        "identifiers": {(DOMAIN, gateway_unique_id)},
        "name": GATEWAY_NAME,
        "manufacturer": MANUFACTURER,
        "model": GATEWAY_MODEL,
    }


def regler_device_info(
    gateway_unique_id: str, room_id, room_name: str, gateway_device_id: str
) -> dict:
    """Device info for one physical SDC Regler (one per room/zone).

    Linked to the gateway device so Home Assistant shows the correct hub ->
    sub-device hierarchy instead of two unrelated top-level devices. On HA
    >= 2026.9 the link is `via_device_id` (the gateway's device-registry
    id, `gateway_device_id`, created in __init__.py before any platform
    runs); on older versions it is the deprecated `via_device` identifier
    tuple, which matches gateway_device_info()'s own identifier.
    `suggested_area` uses the room's own name from /api/room/list - if an
    Area with that name already exists in this HA instance, this device is
    offered as a member of it on first creation (HA's standard
    suggested_area behavior); this is only applied once, not re-applied on
    every poll, so manual area changes afterwards are never overwritten.
    """
    info = {
        "identifiers": {(DOMAIN, f"{gateway_unique_id}_room_{room_id}")},
        "name": room_name,
        "manufacturer": MANUFACTURER,
        "model": REGLER_MODEL,
        "suggested_area": room_name,
    }
    if _SUPPORTS_VIA_DEVICE_ID:
        info["via_device_id"] = gateway_device_id
    else:
        info["via_device"] = (DOMAIN, gateway_unique_id)
    return info
