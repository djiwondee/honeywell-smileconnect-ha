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
device, each regler is a sub-device linked via `via_device`.
"""
# Change log:
# - 2026-08-27: Initial version, extracted from climate.py/sensor.py to fix
#   the two-devices-instead-of-hub/sub-device bug and add via_device +
#   suggested_area support.
from __future__ import annotations

from .const import DOMAIN

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


def regler_device_info(gateway_unique_id: str, room_id, room_name: str) -> dict:
    """Device info for one physical SDC Regler (one per room/zone).

    Linked to the gateway device via `via_device` so Home Assistant shows
    the correct hub -> sub-device hierarchy instead of two unrelated
    top-level devices. `suggested_area` uses the room's own name from
    /api/room/list - if an Area with that name already exists in this HA
    instance, this device is offered as a member of it on first creation
    (HA's standard suggested_area behavior); this is only applied once,
    not re-applied on every poll, so manual area changes afterwards are
    never overwritten.
    """
    return {
        "identifiers": {(DOMAIN, f"{gateway_unique_id}_room_{room_id}")},
        "name": room_name,
        "manufacturer": MANUFACTURER,
        "model": REGLER_MODEL,
        "via_device": (DOMAIN, gateway_unique_id),
        "suggested_area": room_name,
    }
