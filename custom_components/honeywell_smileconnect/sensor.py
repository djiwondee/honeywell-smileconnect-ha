"""Sensor platform for Honeywell Smile Connect (weather + ping diagnostics)."""
# Change log:
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
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import device
from .const import (
    DOMAIN,
    SENSOR_TRANSLATION_KEY_OUTSIDE_TEMPERATURE,
    SENSOR_TRANSLATION_KEY_OUTSIDE_TEMPERATURE_MAX,
    SENSOR_TRANSLATION_KEY_OUTSIDE_TEMPERATURE_MIN,
    SENSOR_TRANSLATION_KEY_RESPONSE_TIME,
)
from .coordinator import SmileConnectCoordinator
from .ping_coordinator import SmileConnectPingCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][config_entry.entry_id]

    async_add_entities(
        [
            SmileConnectWeatherSensor(
                data.coordinator,
                data.unique_id,
                weather_key="temperature",
                translation_key=SENSOR_TRANSLATION_KEY_OUTSIDE_TEMPERATURE,
                unique_id_suffix="outside_temperature",
            ),
            SmileConnectWeatherSensor(
                data.coordinator,
                data.unique_id,
                weather_key="min",
                translation_key=SENSOR_TRANSLATION_KEY_OUTSIDE_TEMPERATURE_MIN,
                unique_id_suffix="outside_temperature_min",
            ),
            SmileConnectWeatherSensor(
                data.coordinator,
                data.unique_id,
                weather_key="max",
                translation_key=SENSOR_TRANSLATION_KEY_OUTSIDE_TEMPERATURE_MAX,
                unique_id_suffix="outside_temperature_max",
            ),
            SmileConnectPingResponseTimeSensor(data.ping_coordinator, data.unique_id),
        ]
    )


class SmileConnectWeatherSensor(CoordinatorEntity, SensorEntity):
    """A single numeric value read from the gateway's /api/weather response.

    Used for outside temperature, and its daily min/max - all three share
    the same shape (a plain float under a known key in coordinator.data
    ["weather"]), so one parameterized class covers all of them rather than
    three near-duplicate classes.
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
    ) -> None:
        super().__init__(coordinator)
        self._weather_key = weather_key
        self._gateway_unique_id = gateway_unique_id
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{DOMAIN}_{unique_id_suffix}"

    @property
    def device_info(self):
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
