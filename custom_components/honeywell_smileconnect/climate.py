"""Climate platform for Honeywell Smile Connect."""
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

PRESET_NONE = "none"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmileConnectCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    scene_manager = SceneManager(coordinator.api)

    async_add_entities(
        SmileConnectClimate(coordinator, scene_manager, idx)
        for idx in range(len(coordinator.data))
    )


class SmileConnectClimate(CoordinatorEntity, ClimateEntity):
    """One climate entity per room reported by the gateway."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    )
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF, HVACMode.AUTO]
    _attr_preset_modes = [
        PRESET_NONE,
        SceneName.BOOST.value,
        SceneName.HOLIDAY.value,
        SceneName.LEAVE.value,
        SceneName.PARTY.value,
        SceneName.STANDBY.value,
    ]

    def __init__(
        self,
        coordinator: SmileConnectCoordinator,
        scene_manager: SceneManager,
        idx: int,
    ) -> None:
        super().__init__(coordinator)
        self.idx = idx
        self._scene_manager = scene_manager
        self._active_preset = PRESET_NONE

    @property
    def _room(self) -> dict:
        return self.coordinator.data[self.idx]

    @property
    def _room_id(self):
        return self._room["data"]["id"]

    @property
    def unique_id(self) -> str:
        return f"{DOMAIN}_room_{self._room_id}"

    @property
    def name(self) -> str:
        return self._room["name"]

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, str(self._room_id))},
            "name": self._room["name"],
            "manufacturer": "Honeywell",
            "model": "Smile Connect (SCN-10)",
        }

    @property
    def current_temperature(self):
        return self._room["data"]["actualTemperature"]

    @property
    def target_temperature(self):
        return self._room["data"]["desiredTemperature"]

    @property
    def min_temp(self):
        return self._room["data"]["minTemperature"]

    @property
    def max_temp(self):
        return self._room["data"]["maxTemperature"]

    @property
    def hvac_mode(self) -> HVACMode:
        self._update_active_preset()
        if self._active_preset in (SceneName.HOLIDAY.value, SceneName.STANDBY.value):
            return HVACMode.OFF
        return HVACMode.HEAT

    @property
    def preset_mode(self) -> str:
        self._update_active_preset()
        return self._active_preset

    def _update_active_preset(self) -> None:
        status = self._room["data"]["roomstatus"]
        if status == ROOM_STATUS_PARTY:
            self._active_preset = SceneName.PARTY.value
        elif status == ROOM_STATUS_BOOST:
            self._active_preset = SceneName.BOOST.value
        elif status == ROOM_STATUS_HOLIDAY:
            self._active_preset = SceneName.HOLIDAY.value
        elif status == ROOM_STATUS_LEAVE:
            self._active_preset = SceneName.LEAVE.value
        elif status == ROOM_STATUS_STANDBY:
            self._active_preset = SceneName.STANDBY.value
        else:
            self._active_preset = PRESET_NONE

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
        if previous != PRESET_NONE and previous != preset_mode:
            await self.hass.async_add_executor_job(
                self._scene_manager.remove_member_from_scene, self._room_id, previous
            )
        if preset_mode != PRESET_NONE:
            await self.hass.async_add_executor_job(
                self._scene_manager.add_member_to_scene, self._room_id, preset_mode
            )
        self._active_preset = preset_mode
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.async_set_preset_mode(SceneName.STANDBY.value)
        else:
            await self.async_set_preset_mode(PRESET_NONE)
