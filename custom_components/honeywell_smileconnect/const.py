"""Constants for the Honeywell Smile Connect integration."""
# Change log:
# - 2026-08-27 (e): FINAL correction after a third, deliberately controlled
#   test run (explicit clean-Standby-baseline reset before testing EACH of
#   Leave/Holiday individually, to eliminate the scene-stacking confound
#   noted below). Result: Holiday=7, Leave=10 - this matches the very
#   first (uncontrolled) run and contradicts the second (uncontrolled) run,
#   which is now understood to likely have been affected by Standby
#   remaining stacked underneath the tested scene. 2-out-of-3 agreement
#   with a plausible explanation for the outlier is the basis for treating
#   this as confirmed. (d) below is superseded/wrong.
# - 2026-08-27 (d): (superseded by (e) - was based on a user recollection
#   that turned out itself to need re-verification) Resolved the
#   Leave/Holiday ambiguity from (c) - split ROOM_STATUS_LEAVE_OR_HOLIDAY
#   back into individual ROOM_STATUS_LEAVE/ROOM_STATUS_HOLIDAY constants.
# - 2026-08-27 (c): Replaced ALL ROOM_STATUS_* constants with values
#   confirmed live against a real gateway (see
#   scripts/manual_probe_roomstatus_via_app.py and CLAUDE.md "roomstatus
#   codes" section). The previous values (43/46/99/127/130/132 and a
#   manual/schedule tuple) were carried over from the generic HeatApp
#   reference project and turned out to be COMPLETELY WRONG for this
#   Honeywell variant - the real codes are single/double digits (3, 6, 7,
#   10, 12), not in the 40-140 range.
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

# Translation keys for sensor.py / binary_sensor.py / climate.py entities
# (has_entity_name + translation_key pattern). Keys must match
# translations/<lang>.json under "entity" -> "sensor"/"binary_sensor"/
# "climate" -> <key> -> "name" for every supported language (see
# CLAUDE.md "Localization" section: en, de, es, fr minimum).
SENSOR_TRANSLATION_KEY_OUTSIDE_TEMPERATURE = "outside_temperature"
SENSOR_TRANSLATION_KEY_OUTSIDE_TEMPERATURE_MIN = "outside_temperature_min"
SENSOR_TRANSLATION_KEY_OUTSIDE_TEMPERATURE_MAX = "outside_temperature_max"
SENSOR_TRANSLATION_KEY_RESPONSE_TIME = "response_time"
BINARY_SENSOR_TRANSLATION_KEY_CONNECTIVITY = "connectivity"
# "thermostat" was chosen over the German-specific "Regler"/"Heizungsregler"
# for the *entity* display name specifically so it reads naturally in all
# four supported languages ("Thermostat" is spelled identically or near-
# identically in de/en/fr; "Termostato" in es) - per project discussion.
# The device MODEL name ("SDC Regler", device.py) is intentionally left
# as the German-rooted product term, since that's this hardware's actual
# name on the box - only the generic entity label uses the more universal
# term.
CLIMATE_TRANSLATION_KEY_THERMOSTAT = "thermostat"


class SceneName(str, Enum):
    """Scene identifiers as used by the gateway API.

    Confirmed live against a real gateway via
    scripts/manual_check_scene_status.py: the real /api/scene/status
    response uses exactly these names (Party, Boost, Holiday, Shower,
    Leave, Standby, Towel) - the generic HeatApp reference project's scene
    names were correct here, unlike its roomstatus codes (see below).

    Note: the Smile App itself LABELS the "Leave" scene as "Economy" in
    its UI (confirmed by the user against their real app) - this is a
    cosmetic, vendor-app-only display label, not a different API scene
    name. We deliberately keep the internal identifier "Leave" (matching
    the actual API string) rather than renaming to "Economy", to avoid
    a naming mismatch with what the gateway itself expects on the wire.
    """

    PARTY = "Party"
    BOOST = "Boost"
    HOLIDAY = "Holiday"
    LEAVE = "Leave"  # labeled "Economy" in the Smile App's own UI
    STANDBY = "Standby"
    # Present in the protocol but unused/untested on single-zone "no hot
    # water" installations. Kept for completeness / future hardware.
    SHOWER = "Shower"
    TOWEL = "Towel"


# Room status codes -> scene membership (see docs/protocol.md and
# CLAUDE.md "Core Finding" for the full verification story). Confirmed
# live via scripts/manual_probe_roomstatus_via_app.py by setting each mode
# through the Smile App itself (bypassing our own scene_manager.py write
# path entirely) and reading the resulting roomstatus + /api/scene/status
# isActive flags.
#
# CONFIRMED. Leave vs. Holiday needed three attempts to settle (see
# CLAUDE.md / docs/protocol.md for the full back-and-forth): the first,
# uncontrolled run (where Standby was left stacked underneath the tested
# scene) gave Leave=10/Holiday=7; a second uncontrolled run gave the
# opposite; a final, deliberately controlled run - explicitly resetting to
# a clean Standby-only baseline before testing EACH of Leave and Holiday,
# specifically to eliminate the stacking confound - reproduced the FIRST
# run's values (Leave=10/Holiday=7) with only a single active scene
# reported each time. That 2-out-of-3 agreement, with the third run's
# deviation plausibly explained by the uncontrolled-stacking confound, is
# the basis for treating this as confirmed:
ROOM_STATUS_STANDBY = 12
ROOM_STATUS_BOOST = 6
ROOM_STATUS_PARTY = 3
ROOM_STATUS_HOLIDAY = 7
ROOM_STATUS_LEAVE = 10

# Also observed: Boost and Party can be simultaneously active alongside
# Standby (the gateway reports isActive=true for both at once) - they
# appear to be temporary overrides layered on top of a Standby baseline,
# whereas activating Leave/Holiday appears to actually replace Standby
# (Standby disappears from the active list). roomstatus itself already
# resolves this to a single, priority-appropriate value, so this doesn't
# require any change to how climate.py determines a single preset_mode -
# noted here for context only.
