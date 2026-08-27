"""Constants for the Honeywell Smile Connect integration."""
from enum import Enum

DOMAIN = "honeywell_smileconnect"

CONF_HOST = "host"
CONF_USER = "username"
CONF_PASSWORD = "password"
CONF_INTERVAL = "interval"

DEFAULT_INTERVAL = 30  # seconds

# Fixed protocol constants observed on the Honeywell Smile Connect gateway.
# These differ from the standard HeatApp protocol - see docs/protocol.md.
FIXED_UDID = "web"
DEVICE_NAME = "Computer"


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
