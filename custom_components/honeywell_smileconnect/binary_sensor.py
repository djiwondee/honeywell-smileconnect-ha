"""Binary sensor platform for Honeywell Smile Connect (ping-based connectivity)."""
# Change log:
# - 2026-08-27: Initial version. Connectivity diagnostic entity fed by the
#   independent ping_coordinator.py (see its module docstring for why this
#   must not share state with the authenticated main coordinator). Raw
#   ping fields not promoted to their own entities (uniqueid, configured,
#   remoteAddress) are exposed as extra_state_attributes instead - see
#   project discussion on entity granularity for /api/ping.
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import device
from .const import BINARY_SENSOR_TRANSLATION_KEY_CONNECTIVITY, DOMAIN
from .ping_coordinator import SmileConnectPingCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([SmileConnectConnectivitySensor(data.ping_coordinator, data.unique_id)])


class SmileConnectConnectivitySensor(CoordinatorEntity, BinarySensorEntity):
    """Reports gateway reachability via the unauthenticated /api/ping
    endpoint. `is_on` = True means reachable.

    Because the coordinator itself already treats a failed ping as
    UpdateFailed (see ping_coordinator.py), CoordinatorEntity's built-in
    `available` handling means this entity correctly shows "unavailable"
    on a failed ping - is_on only needs to reflect the *successful* case.
    """

    _attr_has_entity_name = True
    _attr_translation_key = BINARY_SENSOR_TRANSLATION_KEY_CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, ping_coordinator: SmileConnectPingCoordinator, gateway_unique_id: str) -> None:
        super().__init__(ping_coordinator)
        self._gateway_unique_id = gateway_unique_id
        self._attr_unique_id = f"{DOMAIN}_connectivity"

    @property
    def device_info(self):
        return device.gateway_device_info(self._gateway_unique_id)

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data or {}
        return bool(data.get("success"))

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        return {
            "unique_id_reported_by_gateway": data.get("uniqueid"),
            "configured": data.get("configured"),
            "remote_address": data.get("remoteAddress"),
        }
