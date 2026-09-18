"""Number platform for Honeywell Smile Connect."""
# Change log:
# - 2026-09-18: Initial implementation. Three slider entities per room
#   (Comfort Hi = desiredTempDay, Comfort Lo = desiredTempDay2, Night =
#   desiredTempNight), EntityCategory.CONFIG, attached to the room's Regler
#   device (same device as its climate entity). Writes go through
#   ApiMethods.set_desired_temperature() (docs/protocol.md §4g), which
#   rounds to the gateway's 0.5 degC step and validates the per-type range.
#   Design decisions:
#   - min/max come from api_methods.DESIRED_TEMP_APP_LIMITS (single source
#     of truth, not duplicated here); step is DESIRED_TEMP_STEP.
#   - Rooms are looked up by id in coordinator.data (like sensor.py), not by
#     list index like climate.py, so a changed room order cannot make a
#     slider read or write another room's value.
#   - After a write we call coordinator.async_refresh(), NOT
#     async_request_refresh(): the latter goes through HA's Debouncer
#     (10 s cooldown), so a second slider change inside the cooldown would
#     skip the poll and leave the slider showing a stale value.
#   - No optimistic state: if the gateway ignores a write (unknown whether
#     that can happen for change_mode 1/2/3, e.g. under Standby), the
#     slider simply snaps back to the real stored value after the refresh.
#   - No ValueError wrapping: slider min/max/step and HA's own
#     number.set_value range check make invalid input unreachable, and
#     network errors must propagate unwrapped.
from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import device
from .api.api_methods import (
    DESIRED_TEMP_APP_LIMITS,
    DESIRED_TEMP_STEP,
    DESIRED_TEMP_TARGETS,
)
from .const import (
    DOMAIN,
    NUMBER_TRANSLATION_KEY_COMFORT_HI,
    NUMBER_TRANSLATION_KEY_COMFORT_LO,
    NUMBER_TRANSLATION_KEY_NIGHT,
)
from .coordinator import SmileConnectCoordinator

_TRANSLATION_KEYS = {
    "H": NUMBER_TRANSLATION_KEY_COMFORT_HI,
    "L": NUMBER_TRANSLATION_KEY_COMFORT_LO,
    "N": NUMBER_TRANSLATION_KEY_NIGHT,
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][config_entry.entry_id]
    entities = [
        SmileConnectDesiredTemperatureNumber(
            data.coordinator, data.unique_id, room["data"]["id"], room["name"], target
        )
        for room in data.coordinator.data["rooms"]
        for target in DESIRED_TEMP_TARGETS
    ]
    async_add_entities(entities)


class SmileConnectDesiredTemperatureNumber(CoordinatorEntity, NumberEntity):
    """One of a room's three fixed schedule temperatures as a slider."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_step = DESIRED_TEMP_STEP

    def __init__(
        self,
        coordinator: SmileConnectCoordinator,
        gateway_unique_id: str,
        room_id,
        room_name: str,
        target: str,
    ) -> None:
        super().__init__(coordinator)
        self._gateway_unique_id = gateway_unique_id
        self._room_id = room_id
        self._room_name = room_name
        self._target = target
        self._attr_translation_key = _TRANSLATION_KEYS[target]
        self._attr_native_min_value = DESIRED_TEMP_APP_LIMITS[target]["min"]
        self._attr_native_max_value = DESIRED_TEMP_APP_LIMITS[target]["max"]
        self._attr_unique_id = f"{DOMAIN}_room_{room_id}_desired_temp_{target.lower()}"

    @property
    def device_info(self):
        return device.regler_device_info(self._gateway_unique_id, self._room_id, self._room_name)

    @property
    def native_value(self) -> float | None:
        for room in (self.coordinator.data or {}).get("rooms", []):
            if room["data"].get("id") == self._room_id:
                return room["data"].get(DESIRED_TEMP_TARGETS[self._target]["field"])
        return None

    async def async_set_native_value(self, value: float) -> None:
        await self.hass.async_add_executor_job(
            self.coordinator.api.set_desired_temperature,
            value,
            self._room_id,
            self._target,
        )
        await self.coordinator.async_refresh()
