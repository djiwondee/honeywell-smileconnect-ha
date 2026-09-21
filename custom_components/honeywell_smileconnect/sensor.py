"""Sensor platform for Honeywell Smile Connect (weather + ping diagnostics)."""
# Change log:
# - 2026-09-21: Added SmileConnectRoomScheduleSensor - one per room, fed by
#   the new schedule_coordinator.py, carrying that room's whole weekly
#   switching-time plan in its attributes. This is the data contract the
#   bundled Lovelace schedule card reads; it exists so the card has
#   something reactive to bind to (previously schedules were reachable only
#   through a response-only Action call, so an edit made in the Smile App
#   was invisible to Home Assistant).
#   The STATE is deliberately just the slot count: a state is capped at 255
#   characters and is written to the recorder on every change, so the
#   schedule itself belongs in attributes, behind a value that only moves
#   when the schedule really does. `fingerprint` covers the case where a
#   change leaves the count identical (a slot merely moved).
#   `editable` is computed HERE, not in JavaScript: an "N" (Night) slot
#   survives a read but makes a full-week write fail
#   (switching_times.collect_unsupported_types() has the full explanation),
#   so the card must fail closed rather than offer an edit it cannot
#   commit. Keeping the H/L-only policy in one place means JS never
#   duplicates it.
# - 2026-09-15: Moved the 3 weather sensors (outside temperature/min/max)
#   from the gateway device to the sole Regler's device, ON SINGLE-ROOM
#   INSTALLATIONS ONLY. The physical outside-temperature sensor is wired
#   directly to the Regler ("Smile Controller") for its own local
#   weather-compensated control logic - the gateway only relays the
#   already-measured value via /api/weather, which is why these were
#   originally (wrongly) modeled as gateway-owned. Confirmed correct for
#   this project's actual hardware, which has exactly one room ("Alle")
#   and therefore exactly one Regler.
#   Left attached to the gateway device whenever 2+ rooms are reported
#   (i.e. an SRC-10 add-on module is present) - NOT a considered design
#   choice, just leaving an unverified case unchanged rather than
#   guessing. Per project discussion: the SRC-10 module adds *additional*
#   room controllers on top of the SCN-10's always-present base Regler,
#   rather than replacing it, which makes it plausible (but NOT confirmed
#   - no SRC-10 hardware available to test) that the base Regler stays
#   the first room reported by /api/room/list even with an SRC-10
#   installed. /api/weather also takes no room parameter, so there is no
#   protocol-level way to confirm which physical Regler a reading
#   actually came from once more than one exists. Revisit this once real
#   SRC-10 hardware is available to test against, rather than guessing
#   now - see CLAUDE.md for the full discussion.
# - 2026-09-11: Added SmileConnectPresetDurationSensor - one sensor per
#   room x TIMED_PRESET_SCENE_NAMES scene (Boost/Party/Leave/Holiday),
#   reporting that scene's remaining duration in ITS OWN native unit
#   (minutes/hours/hours/days) from the new
#   coordinator.data["scene_duration_native"] field, or None ("unknown")
#   when the room isn't currently a member of that scene. Deliberately
#   four independent sensors per room rather than one dynamic "whichever
#   preset is active" sensor - project decision: the Smile App lets
#   scenes be combined arbitrarily (e.g. Boost AND Party simultaneously
#   active on the same room), which climate.py's single-value
#   preset_mode structurally cannot represent (it picks one winner via a
#   fixed priority order - see _update_active_preset()'s own docstring).
#   Four independent sensors sidestep that entirely: each reads
#   coordinator.data directly with no "pick a winner" logic and no
#   dependency on climate.py at all, so they can honestly show a genuine
#   compound state. Attached to device.regler_device_info() (the same
#   device as that room's climate entity) since these are framed as a
#   companion to the room's preset control, not a gateway-wide reading
#   like the weather sensors. async_setup_entry() gained a room loop
#   (mirrors climate.py's existing room iteration) crossed with
#   TIMED_PRESET_SCENE_NAMES.
# - 2026-08-27 (b): Fixed weather sensors' device_info to use the shared
#   device.gateway_device_info() builder (previously each entity built its
#   own ad-hoc gateway dict, which is what originally caused a stray
#   duplicate-looking device - see project discussion / device.py's own
#   change log). Added SmileConnectPingResponseTimeSensor, fed by the new
#   independent ping_coordinator.py rather than the main coordinator, as a
#   diagnostic entity (entity_category=DIAGNOSTIC).
# - 2026-08-27 (a): Initial version. Three sensors derived from
#   /api/weather: outside temperature, and its daily min/max, sourced from
#   the shared coordinator's "weather" data. Real gateway response verified
#   manually first - see tests/fixtures/weather_response.json.
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import device
from .const import (
    DOMAIN,
    SCHEDULE_FORMAT_VERSION,
    SENSOR_TRANSLATION_KEY_BOOST_DURATION,
    SENSOR_TRANSLATION_KEY_HOLIDAY_DURATION,
    SENSOR_TRANSLATION_KEY_LEAVE_DURATION,
    SENSOR_TRANSLATION_KEY_OUTSIDE_TEMPERATURE,
    SENSOR_TRANSLATION_KEY_OUTSIDE_TEMPERATURE_MAX,
    SENSOR_TRANSLATION_KEY_OUTSIDE_TEMPERATURE_MIN,
    SENSOR_TRANSLATION_KEY_PARTY_DURATION,
    SENSOR_TRANSLATION_KEY_RESPONSE_TIME,
    SENSOR_TRANSLATION_KEY_ROOM_SCHEDULE,
    TIMED_PRESET_SCENE_NAMES,
    SceneName,
)
from .coordinator import SmileConnectCoordinator
from .ping_coordinator import SmileConnectPingCoordinator
from .schedule_coordinator import SmileConnectScheduleCoordinator
from .switching_times import (
    VALID_TYPES,
    collect_unsupported_types,
    count_defined_slots,
    schedule_fingerprint,
)

_LOGGER = logging.getLogger(__name__)

# Each TIMED_PRESET_SCENE_NAMES scene's own real-world unit/translation key
# for SmileConnectPresetDurationSensor - see that class's docstring.
_TIMED_PRESET_SENSOR_UNITS: dict[SceneName, str] = {
    SceneName.BOOST: UnitOfTime.MINUTES,
    SceneName.PARTY: UnitOfTime.HOURS,
    SceneName.LEAVE: UnitOfTime.HOURS,
    SceneName.HOLIDAY: UnitOfTime.DAYS,
}
_TIMED_PRESET_SENSOR_TRANSLATION_KEYS: dict[SceneName, str] = {
    SceneName.BOOST: SENSOR_TRANSLATION_KEY_BOOST_DURATION,
    SceneName.PARTY: SENSOR_TRANSLATION_KEY_PARTY_DURATION,
    SceneName.LEAVE: SENSOR_TRANSLATION_KEY_LEAVE_DURATION,
    SceneName.HOLIDAY: SENSOR_TRANSLATION_KEY_HOLIDAY_DURATION,
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][config_entry.entry_id]
    rooms = data.coordinator.data["rooms"]

    # Weather sensors belong on the sole Regler's device on a single-room
    # installation (the physical sensor is Regler-side - see this module's
    # change log); left on the gateway device for 2+ rooms (SRC-10
    # present), an untested case that is deliberately left unchanged
    # rather than guessed at.
    weather_room_id: object | None = None
    weather_room_name: str | None = None
    if len(rooms) == 1:
        weather_room_id = rooms[0]["data"]["id"]
        weather_room_name = rooms[0]["name"]

    entities = [
        SmileConnectWeatherSensor(
            data.coordinator,
            data.unique_id,
            weather_key="temperature",
            translation_key=SENSOR_TRANSLATION_KEY_OUTSIDE_TEMPERATURE,
            unique_id_suffix="outside_temperature",
            room_id=weather_room_id,
            room_name=weather_room_name,
        ),
        SmileConnectWeatherSensor(
            data.coordinator,
            data.unique_id,
            weather_key="min",
            translation_key=SENSOR_TRANSLATION_KEY_OUTSIDE_TEMPERATURE_MIN,
            unique_id_suffix="outside_temperature_min",
            room_id=weather_room_id,
            room_name=weather_room_name,
        ),
        SmileConnectWeatherSensor(
            data.coordinator,
            data.unique_id,
            weather_key="max",
            translation_key=SENSOR_TRANSLATION_KEY_OUTSIDE_TEMPERATURE_MAX,
            unique_id_suffix="outside_temperature_max",
            room_id=weather_room_id,
            room_name=weather_room_name,
        ),
        SmileConnectPingResponseTimeSensor(data.ping_coordinator, data.unique_id),
    ]

    for room in rooms:
        room_id = room["data"]["id"]
        room_name = room["name"]
        for scene in TIMED_PRESET_SCENE_NAMES:
            entities.append(
                SmileConnectPresetDurationSensor(
                    data.coordinator, data.unique_id, room_id, room_name, scene
                )
            )
        entities.append(
            SmileConnectRoomScheduleSensor(
                data.schedule_coordinator, data.unique_id, room_id, room_name
            )
        )

    async_add_entities(entities)


class SmileConnectWeatherSensor(CoordinatorEntity, SensorEntity):
    """A single numeric value read from the gateway's /api/weather response.

    Used for outside temperature, and its daily min/max - all three share
    the same shape (a plain float under a known key in coordinator.data
    ["weather"]), so one parameterized class covers all of them rather than
    three near-duplicate classes.

    The physical sensor behind this reading is wired to the Regler, not the
    gateway (see module change log) - attached to that Regler's device when
    `room_id`/`room_name` are given (the single-room case), else left on
    the gateway device (2+ rooms / SRC-10 present, untested - see change
    log for why this is deliberately left unresolved rather than guessed).
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: SmileConnectCoordinator,
        gateway_unique_id: str,
        weather_key: str,
        translation_key: str,
        unique_id_suffix: str,
        room_id: object | None = None,
        room_name: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._weather_key = weather_key
        self._gateway_unique_id = gateway_unique_id
        self._room_id = room_id
        self._room_name = room_name
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{DOMAIN}_{unique_id_suffix}"

    @property
    def device_info(self):
        if self._room_id is not None:
            return device.regler_device_info(self._gateway_unique_id, self._room_id, self._room_name)
        return device.gateway_device_info(self._gateway_unique_id)

    @property
    def native_value(self):
        # .get() deliberately, not direct indexing - see climate.py's own
        # precedent (actualTemperature missing on this hardware) for why
        # defensive field access is this project's standing convention.
        weather = self.coordinator.data.get("weather") or {}
        return weather.get(self._weather_key)


class SmileConnectPingResponseTimeSensor(CoordinatorEntity, SensorEntity):
    """Gateway-reported response time from the unauthenticated /api/ping
    endpoint's "performance" field - a diagnostic value, not a primary
    feature of the integration, hence entity_category=DIAGNOSTIC.

    Deliberately fed by SmileConnectPingCoordinator (not the main,
    authenticated coordinator) so this keeps reporting even if login is
    broken - see ping_coordinator.py's own module docstring.
    """

    _attr_has_entity_name = True
    _attr_translation_key = SENSOR_TRANSLATION_KEY_RESPONSE_TIME
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    def __init__(self, ping_coordinator: SmileConnectPingCoordinator, gateway_unique_id: str) -> None:
        super().__init__(ping_coordinator)
        self._gateway_unique_id = gateway_unique_id
        self._attr_unique_id = f"{DOMAIN}_ping_response_time"

    @property
    def device_info(self):
        return device.gateway_device_info(self._gateway_unique_id)

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        return data.get("performance")


class SmileConnectPresetDurationSensor(CoordinatorEntity, SensorEntity):
    """Remaining duration for one room's membership in one timed preset
    scene (Boost/Party/Leave/Holiday), in that scene's own native unit -
    see module change log for why this is four independent sensors per
    room rather than one dynamic "whichever preset is active" sensor.

    Reads coordinator.data directly (scene_active_rooms + scene_duration_
    native) - deliberately has no dependency on climate.py's preset_mode
    logic, so it can represent a genuinely compound preset state that
    climate.py's single-value preset_mode cannot.
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: SmileConnectCoordinator,
        gateway_unique_id: str,
        room_id,
        room_name: str,
        scene: SceneName,
    ) -> None:
        super().__init__(coordinator)
        self._gateway_unique_id = gateway_unique_id
        self._room_id = room_id
        self._room_name = room_name
        self._scene = scene
        self._attr_translation_key = _TIMED_PRESET_SENSOR_TRANSLATION_KEYS[scene]
        self._attr_native_unit_of_measurement = _TIMED_PRESET_SENSOR_UNITS[scene]
        self._attr_unique_id = f"{DOMAIN}_room_{room_id}_{scene.value.lower()}_duration"

    @property
    def device_info(self):
        # Same device as this room's climate entity (device.regler_device_
        # info()), not the gateway - these are framed as a companion to
        # the room's preset control, unlike the gateway-wide weather
        # sensors above.
        return device.regler_device_info(self._gateway_unique_id, self._room_id, self._room_name)

    @property
    def native_value(self):
        active_rooms = self.coordinator.data.get("scene_active_rooms", {}).get(
            self._scene.value, set()
        )
        if self._room_id not in active_rooms:
            return None
        return self.coordinator.data.get("scene_duration_native", {}).get(self._scene.value)


class SmileConnectRoomScheduleSensor(CoordinatorEntity, SensorEntity):
    """One room's weekly switching-time plan, exposed for the schedule card.

    State is the number of defined slots in the week; the plan itself and
    everything an editor needs to know about it live in the attributes -
    see this module's change log for why round that way.

    Looks its room up BY ID rather than by list index (the pattern number.py
    established), so a room disappearing from /api/room/list cannot make
    this entity silently start reporting a different room's schedule.
    """

    _attr_has_entity_name = True
    _attr_translation_key = SENSOR_TRANSLATION_KEY_ROOM_SCHEDULE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:calendar-clock"
    # No state_class: this is a configuration count, not a measurement -
    # feeding it into long-term statistics would be noise.

    def __init__(
        self,
        coordinator: SmileConnectScheduleCoordinator,
        gateway_unique_id: str,
        room_id,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._gateway_unique_id = gateway_unique_id
        self._room_id = room_id
        self._room_name = room_name
        self._attr_unique_id = f"{DOMAIN}_room_{room_id}_schedule"

    @property
    def device_info(self):
        # The room's own Regler, same device as its climate entity and its
        # temperature sliders - a schedule is a property of the room, not
        # of the gateway.
        return device.regler_device_info(self._gateway_unique_id, self._room_id, self._room_name)

    @property
    def _room_data(self) -> dict | None:
        """This room's entry in the schedule coordinator's data, or None."""
        return (self.coordinator.data or {}).get(str(self._room_id))

    @property
    def available(self) -> bool:
        return super().available and self._room_data is not None

    @property
    def native_value(self):
        room_data = self._room_data
        if room_data is None:
            return None
        return count_defined_slots(room_data["schedule"])

    @property
    def _climate_entity_id(self) -> str | None:
        """entity_id of this room's climate entity, resolved via the registry.

        The card has to target the climate entity for the write Action, and
        the frontend has no way to turn a unique_id into an entity_id on
        its own. The unique_id format is climate.py's
        f"{DOMAIN}_room_{room_id}" - if that ever changes, this breaks
        quietly, which is why it is asserted in tests/test_device.py's
        spirit and called out here.

        Returns None if the climate entity is disabled or not registered
        (yet); the card then goes read-only rather than guessing.
        """
        registry = er.async_get(self.hass)
        return registry.async_get_entity_id("climate", DOMAIN, f"{DOMAIN}_room_{self._room_id}")

    @property
    def extra_state_attributes(self) -> dict:
        room_data = self._room_data
        if room_data is None:
            return {}

        schedule = room_data["schedule"]
        unsupported_types = collect_unsupported_types(schedule)
        climate_entity_id = self._climate_entity_id
        return {
            # Schema marker - the card refuses to render an unknown one
            # rather than risk misreading a plan and writing that back.
            "schedule_format": SCHEDULE_FORMAT_VERSION,
            "schedule": schedule,
            # This room's real gateway-side array width. A write whose
            # width differs is rejected outright (docs/switching-times-api.md
            # point 5), so this - never MAX_SLOTS_PER_DAY - is the ceiling
            # an editor must enforce.
            "slots_per_day": room_data["slots_per_day"],
            "valid_types": list(VALID_TYPES),
            "unsupported_types": unsupported_types,
            # Fail closed: a type we cannot write, or no climate entity to
            # write through, means no editing at all.
            "editable": not unsupported_types and climate_entity_id is not None,
            "climate_entity_id": climate_entity_id,
            "room_id": self._room_id,
            "room_name": self._room_name,
            "fingerprint": schedule_fingerprint(schedule),
        }
