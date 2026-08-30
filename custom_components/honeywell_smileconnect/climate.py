"""Climate platform for Honeywell Smile Connect."""
# Change log:
# - 2026-08-27 (e): Decoupled hvac_mode from preset_mode entirely, based on
#   the user's explanation of how "Standby" actually behaves on this
#   hardware: frost protection is always active at the regler itself and
#   is NOT controllable via the gateway at all - "Standby" ON means the
#   room's configured schedule (Schaltzeiten, set per-room in the Smile
#   App's time profile) is ignored and heating stays off; "Standby" OFF
#   means the regler follows that schedule, heating to the programmed
#   setpoint at the programmed times. This is a schedule-following mode,
#   not a fixed manual setpoint - HVACMode.AUTO is the correct HA concept
#   for it, not HVACMode.HEAT (which was removed entirely; hvac_modes is
#   now just [AUTO, OFF]). hvac_mode is now driven EXCLUSIVELY by whether
#   the Standby scene is active, independent of Boost/Party/Leave/Holiday.
#   preset_mode is now driven EXCLUSIVELY by Boost/Party/Leave/Holiday
#   (Standby removed from preset_modes entirely - it's not a "preset"
#   anymore, it's the hvac_mode toggle). There is deliberately no "none"
#   entry in preset_modes; when no scene is active, preset_mode returns
#   Python None (not a string) - HA renders this natively as "no preset
#   selected" without needing an explicit list entry for it.
# - 2026-08-27 (d): FINAL roomstatus values confirmed (Holiday=7, Leave=10
#   - see const.py's own change log for the full disambiguation story,
#   which took three attempts to settle).
# - 2026-08-27 (c): (superseded) Leave/Holiday temporarily treated as an
#   ambiguous pair while disambiguation was in progress.
# - 2026-08-27 (b): Fixed device_info to use device.regler_device_info()
#   (correct "SDC Regler" model, linked to the gateway device via
#   via_device, suggested_area from the room name) instead of an
#   independent, gateway-shaped device_info dict - this was the root cause
#   of the "two unrelated devices instead of hub/sub-device" bug (see
#   project discussion). Reads coordinator/unique_id from the new
#   SmileConnectData wrapper in hass.data instead of the coordinator
#   directly (see __init__.py). Switched to has_entity_name + name=None so
#   the entity's display name simply follows the device name.
# - 2026-08-27 (a): Adjusted for coordinator.data restructuring - rooms now
#   live under coordinator.data["rooms"] instead of coordinator.data itself,
#   since the coordinator now also fetches weather data for sensor.py in
#   the same poll cycle. See coordinator.py's own change log.
from __future__ import annotations

import logging

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import device
from .api.scene_manager import SceneManager
from .const import (
    DOMAIN,
    ROOM_STATUS_BOOST,
    ROOM_STATUS_HOLIDAY,
    ROOM_STATUS_LEAVE,
    ROOM_STATUS_PARTY,
    ROOM_STATUS_STANDBY,
    SceneName,
)
from .coordinator import SmileConnectCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data.coordinator
    scene_manager = SceneManager(coordinator.api)

    async_add_entities(
        SmileConnectClimate(coordinator, scene_manager, idx, data.unique_id)
        for idx in range(len(coordinator.data["rooms"]))
    )


class SmileConnectClimate(CoordinatorEntity, ClimateEntity):
    """One climate entity per room/SDC Regler reported by the gateway.

    hvac_mode (AUTO/OFF) and preset_mode (Boost/Party/Leave/Holiday) are
    deliberately independent of each other - see module change log for the
    "Standby" behavior this reflects.
    """

    _attr_has_entity_name = True
    _attr_name = None  # entity display name = its device's name (the regler)
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    )
    # No HVACMode.HEAT: there is no "manually hold a fixed setpoint,
    # ignore the schedule" mode on this hardware - only "follow the
    # per-room schedule" (AUTO, Standby scene inactive) or "ignore the
    # schedule and stay off" (OFF, Standby scene active). Frost protection
    # is always enforced by the regler itself regardless of either state
    # and is not something the gateway/this integration can control.
    _attr_hvac_modes = [HVACMode.AUTO, HVACMode.OFF]
    # No "none"/PRESET_NONE entry here on purpose - see class docstring.
    _attr_preset_modes = [
        SceneName.BOOST.value,
        SceneName.HOLIDAY.value,
        SceneName.LEAVE.value,
        SceneName.PARTY.value,
    ]

    def __init__(
        self,
        coordinator: SmileConnectCoordinator,
        scene_manager: SceneManager,
        idx: int,
        gateway_unique_id: str,
    ) -> None:
        super().__init__(coordinator)
        self.idx = idx
        self._scene_manager = scene_manager
        self._active_preset: str | None = None
        self._gateway_unique_id = gateway_unique_id

    @property
    def _room(self) -> dict:
        return self.coordinator.data["rooms"][self.idx]

    @property
    def _room_id(self):
        return self._room["data"]["id"]

    @property
    def unique_id(self) -> str:
        return f"{DOMAIN}_room_{self._room_id}"

    @property
    def device_info(self):
        return device.regler_device_info(self._gateway_unique_id, self._room_id, self._room["name"])

    @property
    def current_temperature(self):
        # This gateway's room data does not always include actualTemperature
        # (observed on a single-zone "Regler MK1" installation with no
        # dedicated room sensor) - fall back to None (HA renders as unknown)
        # rather than crashing entity setup.
        return self._room["data"].get("actualTemperature")

    @property
    def target_temperature(self):
        return self._room["data"].get("desiredTemperature")

    @property
    def min_temp(self):
        return self._room["data"].get("minTemperature", super().min_temp)

    @property
    def max_temp(self):
        return self._room["data"].get("maxTemperature", super().max_temp)

    @property
    def hvac_mode(self) -> HVACMode:
        # Deliberately checks roomstatus directly, NOT self._active_preset:
        # hvac_mode is governed exclusively by the Standby scene, entirely
        # independent of whether Boost/Party/Leave/Holiday also happen to
        # be active at the same time.
        status = self._room["data"].get("roomstatus")
        return HVACMode.OFF if status == ROOM_STATUS_STANDBY else HVACMode.AUTO

    @property
    def preset_mode(self) -> str | None:
        self._update_active_preset()
        return self._active_preset

    def _update_active_preset(self) -> None:
        status = self._room["data"].get("roomstatus")
        if status == ROOM_STATUS_PARTY:
            self._active_preset = SceneName.PARTY.value
        elif status == ROOM_STATUS_BOOST:
            self._active_preset = SceneName.BOOST.value
        elif status == ROOM_STATUS_HOLIDAY:
            self._active_preset = SceneName.HOLIDAY.value
        elif status == ROOM_STATUS_LEAVE:
            self._active_preset = SceneName.LEAVE.value
        else:
            # Covers both "Standby is active" and "plain schedule-
            # following, no scene active" - neither is a selectable preset
            # on this entity, so there is nothing meaningful to report.
            self._active_preset = None

    async def async_set_temperature(self, **kwargs) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self.hass.async_add_executor_job(
            self.coordinator.api.set_temperature, temperature, self._room_id
        )
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        previous = self._active_preset
        if previous is not None and previous != preset_mode:
            await self.hass.async_add_executor_job(
                self._scene_manager.remove_member_from_scene, self._room_id, previous
            )
        await self.hass.async_add_executor_job(
            self._scene_manager.add_member_to_scene, self._room_id, preset_mode
        )
        self._active_preset = preset_mode
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        # Standby is no longer routed through the preset-mode machinery -
        # it's toggled directly here, independent of Boost/Party/Leave/
        # Holiday, matching how it actually behaves on this hardware.
        if hvac_mode == HVACMode.OFF:
            await self.hass.async_add_executor_job(
                self._scene_manager.add_member_to_scene, self._room_id, SceneName.STANDBY.value
            )
        else:  # HVACMode.AUTO
            await self.hass.async_add_executor_job(
                self._scene_manager.remove_member_from_scene, self._room_id, SceneName.STANDBY.value
            )
        await self.coordinator.async_request_refresh()
