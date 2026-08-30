"""Constants for the Honeywell Smile Connect integration."""
# Change log:
# - 2026-08-27 (b): Added CONF_PING_INTERVAL/DEFAULT_PING_INTERVAL for the
#   new independent ping coordinator, and translation key constants for
#   the connectivity binary_sensor and response_time sensor (diagnostics).
# - 2026-08-27 (a): Added SENSOR_TRANSLATION_KEY_* constants for the new
#   outside-temperature/min/max sensors (sensor.py). These match the keys
#   used under "entity" -> "sensor" in translations/<lang>.json.
from enum import Enum

DOMAIN = "honeywell_smileconnect"

CONF_HOST = "host"
CONF_USER = "username"
CONF_PASSWORD = "password"
CONF_INTERVAL = "interval"
CONF_PING_INTERVAL = "ping_interval"

DEFAULT_INTERVAL = 30  # seconds - main room/weather poll cycle
# The gateway's own internet-facing ping cadence is documented as ~90s;
# 15s default here is deliberately much shorter since this is a *local*,
# unauthenticated, lightweight call meant for responsive diagnostics - not
# the same use case as the gateway's own outbound heartbeat. Configurable
# via the options flow (config_flow.py) regardless.
DEFAULT_PING_INTERVAL = 15  # seconds

# Fixed protocol constants observed on the Honeywell Smile Connect gateway.
# These differ from the standard HeatApp protocol - see docs/protocol.md.
FIXED_UDID = "web"
DEVICE_NAME = "Computer"

# Translation keys for sensor.py / binary_sensor.py entities (has_entity_name
# + translation_key pattern). Keys must match translations/<lang>.json under
# "entity" -> "sensor" or "entity" -> "binary_sensor" -> <key> -> "name" for
# every supported language (see CLAUDE.md "Localization" section: en, de,
# es, fr minimum).
SENSOR_TRANSLATION_KEY_OUTSIDE_TEMPERATURE = "outside_temperature"
SENSOR_TRANSLATION_KEY_OUTSIDE_TEMPERATURE_MIN = "outside_temperature_min"
SENSOR_TRANSLATION_KEY_OUTSIDE_TEMPERATURE_MAX = "outside_temperature_max"
SENSOR_TRANSLATION_KEY_RESPONSE_TIME = "response_time"
BINARY_SENSOR_TRANSLATION_KEY_CONNECTIVITY = "connectivity"


class SceneName(str, Enum):
    """Scene identifiers as used by the gateway API."""

    PARTY = "Party"
    BOOST = "Boost"
    HOLIDAY = "Holiday"
    LEAVE = "Leave"
    STANDBY = "Standby"
    # Present in the protocol but unused/untested on single-zone "no hot
    # water" installations. Kept for completeness / future hardware.
    SHOWER = "Shower"
    TOWEL = "Towel"


# Known room status codes -> scene membership (see docs/protocol.md §4).
# NOTE: some codes' exact meaning is not fully confirmed yet.
ROOM_STATUS_PARTY = 43
ROOM_STATUS_BOOST = 46
ROOM_STATUS_ERROR = 99
ROOM_STATUS_HOLIDAY = 127
ROOM_STATUS_LEAVE = 130
ROOM_STATUS_STANDBY = 132
ROOM_STATUS_MANUAL_OR_SCHEDULE = (122, 51, 41, 131, 54, 137)
