"""Climate platform for Honeywell Smile Connect."""
# Change log:
# - 2026-08-31: Fixed hvac_mode display staleness when leaving Standby
#   (Off -> Auto). Live-verified via scripts/manual_check_standby_
#   deactivation_timing.py and scripts/manual_check_standby_nudge.py: the
#   gateway does NOT recompute roomstatus on its own after a scene
#   deactivation alone - it stayed stuck at 12 (Standby) for 55+s in
#   testing, despite /api/scene/status already correctly reporting
#   isActive=False for Standby the whole time (which is what
#   SceneManager._wait_for_scene_active_state() checks - so that
#   verification alone could never have caught this; it was checking the
#   wrong field). Sending back the SAME desiredTemperature value also did
#   nothing (the gateway's own response to that call already echoed back
#   the stale roomstatus:12, confirming it treats an unchanged value as a
#   no-op). Only a genuinely CHANGED desiredTemperature write caused
#   roomstatus to move (confirmed moving to 11, "plain schedule-
#   following" baseline, within the very next poll - effectively
#   immediate). Fix: when leaving Standby, explicitly nudge
#   desiredTemperature to scheduleTempMax (via the existing max_temp
#   property) - this exactly mirrors the Smile App's own confirmed
#   behavior (user-verified daily use: the App always jumps to the
#   maximum when leaving Standby, and the room's schedule reliably
#   corrects it back down shortly after - not just a lab observation).
#   Deliberately NOT modeled as a new entity or config option: this is a
#   pure protocol workaround for a gateway-side recompute quirk, not a
#   value any user meaningfully sets themselves - see project discussion.
#   The nudge call is wrapped in a broad except (deliberate, matches the
#   existing config_flow.py::validate_input() precedent for a standalone
#   non-fatal broad catch) since a failure here must not surface as an
#   error for the hvac_mode switch itself, which already succeeded via
#   the scene deactivation above regardless of whether this secondary
#   nudge call works.
# - 2026-08-30 (b): min_temp/max_temp switched from minTemperature/
#   maxTemperature (observed identical, 12/12 - not meaningful bounds) to
#   scheduleTempMin/scheduleTempMax (12/25, confirmed against the real
#   Smile App's own slider bounds by the user). Also fixed
#   manual_check_decimal_temperature.py to actively verify Standby
#   deactivation (poll + re-fetch) instead of a blind sleep(3) - the first
#   test run's MISMATCH result turned out to be a false negative caused by
#   this (Standby likely still active, using stale desiredTemperature),
#   not a real decimal-notation problem. A follow-up manual run with
#   Standby confirmed off showed dot notation works correctly (24.5 sent,
#   24.5 read back) - see docs/protocol.md "Open Items".
# - 2026-08-30 (a): Fixed entity_id/display name - was `_attr_name = None`,
#   producing "climate.haus" (device name only, no entity name component),
#   which violates the project's own has_entity_name convention (every
#   other entity combines device name + entity name). Replaced with a real
#   `_attr_translation_key` ("thermostat"), matching the pattern used
#   throughout sensor.py/binary_sensor.py - see const.py's own comment for
#   why "thermostat" was chosen over the German-specific "Regler" for this
#   particular label (works naturally across all four supported languages).
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
from typing import ClassVar

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
    CLIMATE_TRANSLATION_KEY_THERMOSTAT,
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
    # Was previously `_attr_name = None` (entity display name = device name
    # only), which produced entity_ids like "climate.haus" - violating our
    # own has_entity_name convention (device name + entity name combined).
    # Fixed by giving the entity a real translation_key, same pattern as
    # every other entity in this project. Produces e.g. "climate.haus_
    # thermostat", displayed as "Haus Thermostat" - see const.py's own
    # comment for why "thermostat" (not the German-specific "Regler") was
    # chosen for this particular label.
    _attr_translation_key = CLIMATE_TRANSLATION_KEY_THERMOSTAT
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
    _attr_hvac_modes: ClassVar[list[HVACMode]] = [HVACMode.AUTO, HVACMode.OFF]
    # No "none"/PRESET_NONE entry here on purpose - see class docstring.
    _attr_preset_modes: ClassVar[list[str]] = [
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
        # scheduleTempMin/scheduleTempMax are the confirmed-correct bounds
        # (verified against the real Smile App: 12-25 on this
        # installation) - minTemperature/maxTemperature were observed
        # identical to each other (12/12) and are not meaningful here. See
        # CLAUDE.md "Still untested / open" (now resolved) for the full
        # story. Falls back to minTemperature, then HA's own default, in
        # case scheduleTempMin is ever absent on some other installation.
        data = self._room["data"]
        return data.get("scheduleTempMin", data.get("minTemperature", super().min_temp))

    @property
    def max_temp(self):
        data = self._room["data"]
        return data.get("scheduleTempMax", data.get("maxTemperature", super().max_temp))

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
            await self._nudge_temperature_after_leaving_standby()
        await self.coordinator.async_request_refresh()

    async def _nudge_temperature_after_leaving_standby(self) -> None:
        """Force the gateway to recompute roomstatus after leaving Standby.

        Live-verified (scripts/manual_check_standby_nudge.py): the gateway
        does not recompute roomstatus on its own after a scene
        deactivation alone, and a re-sent UNCHANGED desiredTemperature is
        treated as a no-op (no recompute either) - only a genuinely
        changed value triggers it, immediately. Mirrors the Smile App's
        own confirmed behavior of always jumping to the maximum when
        leaving Standby and relying on the schedule to correct it back
        down shortly after.

        Deliberately non-fatal: this is a secondary display-correctness
        nudge, not the primary action (the scene deactivation above
        already succeeded regardless of this call's outcome). A failure
        here should not surface as an error for the hvac_mode switch
        itself - worst case, the display just stays stale until the next
        organic temperature change or the schedule's next switching time,
        exactly like before this fix.
        """
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.api.set_temperature, self.max_temp, self._room_id
            )
        except Exception:  # noqa: BLE001 - deliberate, see method docstring
            _LOGGER.warning(
                "Failed to nudge temperature after leaving Standby for room %s; "
                "hvac_mode display may stay stale until the next change.",
                self._room_id,
            )
