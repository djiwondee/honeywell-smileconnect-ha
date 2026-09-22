"""Climate platform for Honeywell Smile Connect."""
# Change log:
# - 2026-09-21 (b): Every gateway call in this file now goes through
#   SmileConnectCoordinator.async_api_call() / async_api_session() instead
#   of hass.async_add_executor_job() directly, so it is serialised against
#   the poll cycles and against other entity services - see coordinator.py's
#   change log for the reqcount race this closes.
#   The two switching-times write Actions additionally hold the lock across
#   their whole read-modify-write via async_api_session(): both read the
#   current schedule, edit it, and write the result back, and the gateway
#   has no partial-update endpoint, so without this two concurrent writes
#   could both read the same base and the second would silently discard the
#   first's change.
#   Both write Actions now also refresh the schedule coordinator
#   (async_refresh(), NOT async_request_refresh(): the Debouncer would skip
#   a second write inside its cooldown and leave the schedule sensor - and
#   therefore the Lovelace card - showing stale data).
# - 2026-09-21 (a): Look the room up BY ID instead of by list index. This was
#   the only index-based lookup left in the integration - number.py and
#   sensor.py both switched to id years-of-bugs ago, and number.py's own
#   change log states the reason: "a changed room order cannot make a
#   slider read or write another room's value." climate.py had exactly
#   that exposure, and worse: with a SHORTENED room list it did not even
#   raise, it silently read AND WROTE a different room.
#   It also raised IndexError on an EMPTY room list, which is how a dead
#   gateway session surfaced in production - a traceback every 30 seconds
#   for hours, including from unique_id and device_info, which Home
#   Assistant calls during entity-registry and state-machine work rather
#   than just for display. That root cause is fixed in api_request.py /
#   coordinator.py; this is the second line of defence, so a room that
#   genuinely disappears makes the entity unavailable instead of noisy.
#   _room_id and _room_name are now stored at construction rather than
#   derived from the room dict, precisely so entity IDENTITY keeps working
#   when the lookup does not.
#   Also added _translate_gateway_errors: the new SmileConnectApiError is
#   deliberately not a ValueError (see api/exceptions.py), so it would
#   otherwise reach the UI as a bare traceback. Translated to
#   HomeAssistantError so the gateway's own message is what the user sees.
# - 2026-09-18 (b): The set_desired_temperature Action's `type` values are
#   now comfort_hi/comfort_lo/night instead of H/L/N. hassfest rejects
#   select-option translation keys that are not [a-z0-9-_]+ (the option
#   values double as translation keys), so uppercase letters cannot be used
#   there. The API layer (api_methods.DESIRED_TEMP_TARGETS) still uses H/L/N;
#   this module maps between the two. Also matches the response keys.
# - 2026-09-18: Added the set_desired_temperature entity Action (new
#   async_set_desired_temperature handler): writes one of the room's three
#   fixed schedule temperatures - H=desiredTempDay ("Comfort Hi"),
#   L=desiredTempDay2 ("Comfort Lo"), N=desiredTempNight - via the new
#   ApiMethods.set_desired_temperature() (docs/protocol.md §4g). Design
#   decisions: (1) the type selector is its own 3-way enum built from
#   api_methods.DESIRED_TEMP_TARGETS, NOT switching_times.VALID_TYPES (that
#   one excludes "N" for schedule SLOT types, unrelated to this); (2) the
#   per-type range check (Smile App limits) runs on the ROUNDED value via
#   validate_desired_temperature() and only that pure validation is wrapped
#   into ServiceValidationError - the network call is deliberately not
#   wrapped in `except ValueError`, since json.JSONDecodeError is a
#   ValueError too and would be mis-reported as a user input error; (3) the
#   returned response comes from a direct api.get_specific_room() re-read,
#   not coordinator.async_request_refresh(): the coordinator's Debouncer
#   can skip the poll for a second write inside its cooldown, which would
#   make us report stale data - exactly what the re-read exists to catch.
#   A mismatch is reported as verified=False plus a warning, not raised.
#   The response's desired_temperatures dict uses comfort_hi/comfort_lo/
#   night as keys (DESIRED_TEMP_TARGETS[...]["response_key"]) instead of
#   H/L/N: found live that HA's YAML view renders a bare "N" key quoted,
#   since YAML 1.1 reads N as a boolean.
# - 2026-09-17 (b): Fixed set_schedule_room_weekday erroring on a call
#   where only slot_1 is filled in via the HA GUI. Root cause: the
#   frontend's form for an untouched, optional field inside a collapsed
#   section (slot_2/slot_3) submits an empty string `""` rather than
#   omitting the key - vol.Inclusive's "all or none" grouping trivially
#   passed (all three keys ARE present, just empty), and cv.time("")/
#   vol.In(...)("")  then failed with a confusing schema error for a slot
#   the user never touched. Fixed with a new _drop_empty_slot_fields()
#   preprocessing step (vol.All(_drop_empty_slot_fields,
#   cv.make_entity_service_schema(...))) that strips empty-string
#   slot_N_* keys before the real schema runs - confirmed live via HA's
#   own is_entity_service_schema()/make_entity_service_schema() that this
#   composition is supported. Found during this session's live
#   verification against a real HA instance (see CLAUDE.md).
# - 2026-09-17 (a): Added get_schedule_room/set_schedule_room/
#   set_schedule_room_weekday HA Actions - the first phase of exposing
#   room switching-time schedules (see docs/switching-times-api.md and
#   CLAUDE.md for the protocol background and the product decisions this
#   encodes). Deliberately scoped to just these three read/write Actions
#   for now - a native schedule.* helper UI and automatic gateway sync are
#   a later phase, not part of this change.
#   Conversion/validation lives in the new, HA-independent
#   switching_times.py module (unit-tested in tests/test_switching_times.py)
#   rather than inline here, matching this file's existing pattern of
#   keeping ValueError-raising validation out of the entity layer and just
#   translating it to ServiceValidationError.
#   set_schedule_room takes one `schedule` field (a nested per-weekday
#   object/YAML value) - a whole week (up to 7x3x3 = 63 leaf values) is too
#   large for a sane HA form UI, and HA's Developer Tools/Actions already
#   has a built-in YAML editor for any action call, so nothing extra is
#   needed to support that. set_schedule_room_weekday instead uses 9 FLAT
#   fields (slot_1_from/_to/_type, slot_2_*, slot_3_*) grouped into
#   vol.Inclusive("slot_N") triples - confirmed by reading ha-core's own
#   services.yaml "collapsed: true / fields:" pattern (e.g.
#   kitchen_sink/habitica) that such nested UI groups are COSMETIC ONLY and
#   never produce a nested dict in the actual service-call data (still flat
#   top-level keys) - so real per-slot Time/Select pickers require flat
#   field names, not a services.yaml-nested "slot_1: {from,to,type}" object.
#   vol.Inclusive enforces "all three or none" per slot at the schema
#   level (a slot_N_from without slot_N_to/_type is a schema error, not a
#   ValueError deep in our own code) - see SET_SCHEDULE_ROOM_WEEKDAY_SCHEMA.
#   Only 3 slot groups exist at all (no slot_4_*), which is how the
#   "max 3 slots per weekday" hardware limit is enforced structurally for
#   this Action, on top of switching_times.py's own runtime check for
#   set_schedule_room's free-form `schedule` input.
#   Both set_* Actions use supports_response=OPTIONAL and return the
#   freshly re-read schedule after writing - matches this project's
#   long-standing principle of never trusting a bare `success:true` from
#   the gateway (see api_methods.py's own change log for prior incidents)
#   by giving the caller/automation an immediate, real confirmation without
#   a second manual call.
# - 2026-09-11 (c): Added the set_hvac_mode_and_temperature HA Action.
#   Root cause: the standard climate.set_temperature service CAN carry an
#   hvac_mode alongside temperature (HA core calls async_set_hvac_mode()
#   then async_set_temperature() internally for that combined call), but
#   live testing this session found it does not reliably apply either
#   value when both are given together on this entity - no exception, no
#   log warning, simply no effect - while each service works reliably on
#   its own (climate.set_hvac_mode alone; climate.set_temperature alone
#   while already in 'auto'). The exact mechanism inside HA core's
#   handling was not pinned down further; instead of chasing that, this
#   adds a dedicated Action that sequences the two writes itself,
#   deliberately validation-free (project decision: no extra checks
#   beyond what the two existing standard services already do - if a
#   caller passes off+temperature, temperature is just ignored, matching
#   the already-documented gateway behavior that temperature writes are
#   silently rejected while Standby is active anyway). async_set_hvac_mode()
#   is now a thin wrapper around a new shared _async_apply_hvac_mode()
#   helper (mirrors the async_set_preset_mode()/_async_apply_preset()
#   pattern from 2026-09-10) so the Action and the standard service share
#   one code path. When a target temperature is supplied alongside
#   hvac_mode=auto, the helper sets it directly instead of going through
#   _nudge_temperature_after_leaving_standby()'s jump-to-max-then-drift-
#   back workaround - a genuine target value already satisfies that
#   nudge's own "must be a real change" requirement, so the extra
#   round-trip is redundant here (and was a plausible contributor to the
#   standard service's combined-call failure: two genuine writes in quick
#   succession instead of one). See services.yaml/strings.json/
#   translations for the schema/labels, and CLAUDE.md for the
#   climate.set_temperature limitation this Action works around.
# - 2026-09-11 (b): Removed the set_preset_mode_with_duration Action's
#   `duration` field ("Raw duration" in the UI) - it existed only as a
#   workaround for Leave, which used to have its `target=` path blocked
#   (see the 2026-09-11 (a) entry below). Now that Leave's formula is
#   confirmed identical to Party/Boost, `target` ("Duration") covers all
#   four presets uniformly and there is no remaining reason for a
#   user-facing raw-wire-value field. SET_PRESET_MODE_WITH_DURATION_SCHEMA
#   dropped `duration` and the now-pointless vol.Exclusive grouping (a
#   single optional field needs no exclusivity group);
#   async_set_preset_mode_with_duration()/_async_apply_preset() dropped
#   their `duration` parameters accordingly. The underlying Python API
#   (ApiMethods.set_scene(duration=...), SceneManager.add_member_to_scene(
#   duration=...)) is UNCHANGED - this only simplifies the HA-facing
#   Action surface. See services.yaml/strings.json/translations for the
#   matching schema/label changes, and CLAUDE.md for the full rationale.
# - 2026-09-11 (a): Leave's target= block was removed at the API layer (see
#   api_methods.py's change log) - Leave now uses the same fraction-of-
#   scene_max formula as Party/Boost, confirmed live via a mitmproxy
#   capture of the real Smile App. Updated the stale "e.g. Leave's
#   target= block" comment below accordingly; no behavior change needed
#   here otherwise, since the NotImplementedError/ValueError handling was
#   already generic.
# - 2026-09-10: Added the set_preset_mode_with_duration HA Action - the
#   first custom Action for this integration (start of the 0.1.x beta
#   line, see CLAUDE.md "Versioning & Branching Strategy"). hvac_mode,
#   preset_mode, and temperature were already fully controllable via the
#   standard climate.set_hvac_mode/set_preset_mode/set_temperature HA
#   services (this file's existing ClimateEntity overrides back those
#   directly) - the one genuinely new capability was a per-activation
#   custom preset duration, which ApiMethods.set_scene() already supports
#   (target=/duration=) but which SceneManager.add_member_to_scene() had
#   no way to receive from a caller. async_set_preset_mode()'s body is now
#   a thin wrapper around a new shared _async_apply_preset() helper (used
#   identically by both the standard path and the new Action, so the
#   existing HA UI/climate.set_preset_mode() behavior is unchanged) that
#   forwards optional target/duration through to scene_manager.py. The new
#   async_set_preset_mode_with_duration() entity method backs the Action,
#   registered via entity_platform.async_register_entity_service() in
#   async_setup_entry() (see services.yaml/strings.json/translations for
#   the schema/labels). ValueError/NotImplementedError from the API layer
#   (SCENE_APP_LIMITS violations, Leave's target= block) are translated to
#   ServiceValidationError so they surface as a clean message in the HA UI
#   instead of a raw traceback.
# - 2026-09-01 (b): Added PRESET_NONE ("none") as a selectable preset_mode
#   entry - supersedes the 2026-08-27 (e) entry below, which deliberately
#   left it out on the assumption that Python `None` alone was sufficient
#   ("HA renders this natively as 'no preset selected'"). Live testing
#   after the (a) fix below (presets actually working now) surfaced the
#   gap that assumption missed: HA's preset dropdown only ever offers
#   entries present in preset_modes, so without a "none" entry there was
#   no way to clear an active preset from the UI without picking a
#   different one. preset_mode's property now returns PRESET_NONE instead
#   of Python None when nothing is active (so the dropdown highlights
#   "None" correctly); _update_active_preset()'s internal
#   self._active_preset representation is unchanged (still str | None).
#   async_set_preset_mode() treats preset_mode == PRESET_NONE as "remove
#   the current preset, activate nothing" instead of trying to activate a
#   nonexistent "none" gateway scene.
# - 2026-09-01 (a): hvac_mode and _update_active_preset() (feeding preset_mode)
#   no longer read roomstatus at all - both now read
#   coordinator.data["scene_active_rooms"] (new field, see coordinator.py's
#   own change log), the ground-truth per-scene room membership fetched
#   directly from /api/scene/status + /api/scene/getrooms. Root cause:
#   roomstatus cannot be trusted for compound states - a still-active
#   Standby can be masked by a preset (its own isActive flag stays True in
#   the background, invisible via roomstatus alone), and Holiday
#   specifically never resolves via roomstatus at all while Standby is
#   simultaneously active (a confirmed gateway firmware quirk - docs/
#   protocol.md §4e/§4f). An earlier fix attempt forcibly deactivated
#   Standby whenever Holiday was selected, as a workaround - rejected
#   during planning: Leave/Boost/Holiday/Party must all stay genuinely
#   independent of Standby, and a fix must work uniformly for all four,
#   not special-case Holiday by mutating Standby's state as a side effect.
#   Reading ground-truth scene membership directly (instead of roomstatus)
#   fixes the masking problem for all four presets uniformly and never
#   touches Standby. async_set_hvac_mode(), async_set_preset_mode(), and
#   _nudge_temperature_after_leaving_standby() are UNCHANGED - this is a
#   read-path-only fix, per an explicit constraint that the already-
#   working Standby on/off toggle (see the 2026-08-31 entry below) must
#   not regress.
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

import functools
import logging
from typing import ClassVar

import voluptuous as vol
from homeassistant.components.climate import (
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import device
from .api.api_methods import DESIRED_TEMP_TARGETS, validate_desired_temperature
from .api.exceptions import SmileConnectApiError
from .api.scene_manager import SceneManager
from .const import (
    CLIMATE_TRANSLATION_KEY_THERMOSTAT,
    DOMAIN,
    SceneName,
)
from .coordinator import SmileConnectCoordinator
from .schedule_coordinator import SmileConnectScheduleCoordinator
from .switching_times import (
    VALID_TYPES,
    WEEKDAYS,
    ScheduleValidationError,
    replace_weekday_slots,
    switching_times_to_weekday_dict,
    weekday_dict_to_switching_times,
)

_LOGGER = logging.getLogger(__name__)


def _translate_gateway_errors(func):
    """Surface a gateway failure as a readable error instead of a traceback.

    SmileConnectApiError is deliberately NOT a ValueError (see
    api/exceptions.py for why), so nothing in this file catches it by
    accident - which also means an Action would otherwise show the user a
    raw traceback. The gateway's own message is usually the most useful
    thing available ("The input format is invalid: 14"), so it is passed
    through verbatim.

    functools.wraps matters here: entity services are registered by METHOD
    NAME via async_register_entity_service(), and Home Assistant
    introspects the bound method.
    """

    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        try:
            return await func(self, *args, **kwargs)
        except SmileConnectApiError as err:
            raise HomeAssistantError(str(err)) from err

    return wrapper

SERVICE_SET_PRESET_MODE_WITH_DURATION = "set_preset_mode_with_duration"
SERVICE_SET_HVAC_MODE_AND_TEMPERATURE = "set_hvac_mode_and_temperature"
SERVICE_GET_SCHEDULE_ROOM = "get_schedule_room"
SERVICE_SET_SCHEDULE_ROOM = "set_schedule_room"
SERVICE_SET_SCHEDULE_ROOM_WEEKDAY = "set_schedule_room_weekday"
SERVICE_SET_DESIRED_TEMPERATURE = "set_desired_temperature"

# Mirrors _attr_preset_modes below - kept as a separate module-level list
# (rather than importing the class attribute) since voluptuous schemas are
# built at module import time, before the class body runs.
_PRESET_MODE_WITH_DURATION_VALUES = [
    PRESET_NONE,
    SceneName.BOOST.value,
    SceneName.HOLIDAY.value,
    SceneName.LEAVE.value,
    SceneName.PARTY.value,
]

# `target` is a real-world duration in the preset's own unit (minutes for
# Boost, hours for Party/Leave, days for Holiday) - see SCENE_APP_LIMITS
# in api_methods.py for the exact per-preset range, and strings.json's
# field description for the user-facing breakdown. The raw wire-value
# `duration` parameter ApiMethods.set_scene() also supports is
# deliberately NOT exposed here (see change log) - it remains available
# to direct Python callers (scripts, tests), just not through this Action.
SET_PRESET_MODE_WITH_DURATION_SCHEMA = {
    vol.Required("preset_mode"): vol.In(_PRESET_MODE_WITH_DURATION_VALUES),
    vol.Optional("target"): vol.Coerce(float),
}

# Deliberately no validation beyond type coercion (project decision,
# 2026-09-11) - e.g. hvac_mode="off" with a temperature given is accepted
# by the schema and simply ignored downstream, matching how the standard
# climate.set_temperature service already behaves when the gateway
# silently rejects a temperature write while Standby is active.
SET_HVAC_MODE_AND_TEMPERATURE_SCHEMA = {
    vol.Required("hvac_mode"): vol.In([HVACMode.AUTO.value, HVACMode.OFF.value]),
    vol.Optional("temperature"): vol.Coerce(float),
}

GET_SCHEDULE_ROOM_SCHEMA: dict = {}

# Own three-way selector built from api_methods.DESIRED_TEMP_TARGETS - NOT
# switching_times.VALID_TYPES, which excludes "N" for schedule slot types.
# The Action's `type` uses the lowercase response_key (comfort_hi/
# comfort_lo/night) because select-option values double as translation keys
# and hassfest requires [a-z0-9-_]+; it is mapped back to H/L/N below.
_DESIRED_TEMP_KEY_TO_TARGET = {
    target["response_key"]: letter for letter, target in DESIRED_TEMP_TARGETS.items()
}
# The exact per-type temperature range is enforced in the handler (it needs
# the rounded value), not in the schema.
SET_DESIRED_TEMPERATURE_SCHEMA = {
    vol.Required("type"): vol.In(list(_DESIRED_TEMP_KEY_TO_TARGET)),
    vol.Required("temperature"): vol.Coerce(float),
}

# `schedule` is deliberately a single free-form field (a nested per-weekday
# object), not broken into per-day/per-slot fields - see this module's
# change log for why (up to 63 leaf values, HA's own YAML action editor
# already covers this). Deep validation happens in
# switching_times.weekday_dict_to_switching_times(), not here - `dict` only
# rejects a non-dict value outright.
SET_SCHEDULE_ROOM_SCHEMA = {
    vol.Required("schedule"): dict,
}


def _slot_group_schema(prefix: str) -> dict:
    """3 flat, vol.Inclusive-grouped fields for one schedule slot.

    vol.Inclusive(..., prefix) means: if ANY of this trio is given, ALL
    three must be given - enforced by voluptuous itself, before the call
    ever reaches our own code. See this module's change log for why these
    are flat fields (slot_N_from/_to/_type) rather than a nested
    `slot_N: {from, to, type}` object.
    """
    return {
        vol.Inclusive(f"{prefix}_from", prefix): cv.time,
        vol.Inclusive(f"{prefix}_to", prefix): cv.time,
        vol.Inclusive(f"{prefix}_type", prefix): vol.In(VALID_TYPES),
    }


def _drop_empty_slot_fields(value: dict) -> dict:
    """Treat an empty-string slot_N_* field as though it were omitted.

    Live testing found the HA frontend's form for an untouched, optional
    field inside a collapsed section (slot_2/slot_3 here) can submit an
    empty string `""` rather than leaving the key out entirely. Without
    this, vol.Inclusive's "all or none" check for that slot's group
    trivially passes (all three keys ARE present, just empty), and THEN
    cv.time("")/vol.In(...)("") fails with a confusing schema error for a
    slot the user never touched. Runs as a vol.All() preprocessing step
    before the real entity-service schema, so this is the only place
    "" needs special-casing.
    """
    return {k: v for k, v in value.items() if not (k.startswith("slot_") and v == "")}


# Exactly 3 slot groups (slot_1/2/3) - no slot_4_* fields exist, which is
# how the "max 3 slots per weekday" hardware limit is enforced structurally
# for this Action (see module change log). Wrapped in vol.All() with
# _drop_empty_slot_fields (see its own docstring) - cv.make_entity_service_schema()
# is called explicitly here (rather than passing a plain dict, as the other
# schemas in this file do) so it can be composed with that preprocessing
# step; HA's own is_entity_service_schema() check explicitly supports a
# vol.All()-wrapped entity-service schema like this.
SET_SCHEDULE_ROOM_WEEKDAY_SCHEMA = vol.All(
    _drop_empty_slot_fields,
    cv.make_entity_service_schema(
        {
            vol.Required("weekday"): vol.In(WEEKDAYS),
            **_slot_group_schema("slot_1"),
            **_slot_group_schema("slot_2"),
            **_slot_group_schema("slot_3"),
        }
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data.coordinator
    scene_manager = SceneManager(coordinator.api)

    async_add_entities(
        SmileConnectClimate(
            coordinator,
            data.schedule_coordinator,
            scene_manager,
            room["data"]["id"],
            room["name"],
            data.unique_id,
        )
        for room in coordinator.data["rooms"]
    )

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SET_PRESET_MODE_WITH_DURATION,
        SET_PRESET_MODE_WITH_DURATION_SCHEMA,
        "async_set_preset_mode_with_duration",
    )
    platform.async_register_entity_service(
        SERVICE_SET_HVAC_MODE_AND_TEMPERATURE,
        SET_HVAC_MODE_AND_TEMPERATURE_SCHEMA,
        "async_set_hvac_mode_and_temperature",
    )
    platform.async_register_entity_service(
        SERVICE_GET_SCHEDULE_ROOM,
        GET_SCHEDULE_ROOM_SCHEMA,
        "async_get_schedule_room",
        supports_response=SupportsResponse.ONLY,
    )
    platform.async_register_entity_service(
        SERVICE_SET_SCHEDULE_ROOM,
        SET_SCHEDULE_ROOM_SCHEMA,
        "async_set_schedule_room",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_SET_SCHEDULE_ROOM_WEEKDAY,
        SET_SCHEDULE_ROOM_WEEKDAY_SCHEMA,
        "async_set_schedule_room_weekday",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_SET_DESIRED_TEMPERATURE,
        SET_DESIRED_TEMPERATURE_SCHEMA,
        "async_set_desired_temperature",
        supports_response=SupportsResponse.OPTIONAL,
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
    # PRESET_NONE ("none") IS included, as of 2026-09-01 (b) - without it,
    # nothing in this list represents "no preset", so HA's UI had no way
    # to clear an active preset without picking a different one. See this
    # module's change log.
    _attr_preset_modes: ClassVar[list[str]] = [
        PRESET_NONE,
        SceneName.BOOST.value,
        SceneName.HOLIDAY.value,
        SceneName.LEAVE.value,
        SceneName.PARTY.value,
    ]

    def __init__(
        self,
        coordinator: SmileConnectCoordinator,
        schedule_coordinator: SmileConnectScheduleCoordinator,
        scene_manager: SceneManager,
        room_id,
        room_name: str,
        gateway_unique_id: str,
    ) -> None:
        super().__init__(coordinator)
        # Stored, not derived from the room dict: unique_id and
        # device_info must keep working even when the room is temporarily
        # missing from the coordinator's data, because Home Assistant
        # calls them during entity-registry and state-machine work.
        self._room_id = room_id
        self._fallback_room_name = room_name
        # Not a CoordinatorEntity relationship - this entity does not read
        # from the schedule coordinator, it only pokes it after a write so
        # the schedule sensor (and the Lovelace card reading it) converges
        # immediately instead of up to one poll interval later.
        self._schedule_coordinator = schedule_coordinator
        self._scene_manager = scene_manager
        self._active_preset: str | None = None
        self._gateway_unique_id = gateway_unique_id

    @property
    def _room(self) -> dict | None:
        """This room's entry in the coordinator's data, or None.

        By id, not by list index - see this module's change log.
        """
        for room in (self.coordinator.data or {}).get("rooms", []):
            if room["data"].get("id") == self._room_id:
                return room
        return None

    @property
    def _room_data(self) -> dict:
        """The room's raw gateway dict, or an empty one when it is missing.

        Lets the read-only properties below stay simple: every one of them
        already uses .get() with a sensible fallback, and `available`
        reports the truth separately.
        """
        room = self._room
        return room["data"] if room is not None else {}

    @property
    def _room_name(self) -> str:
        room = self._room
        return room["name"] if room is not None else self._fallback_room_name

    @property
    def available(self) -> bool:
        return super().available and self._room is not None

    @property
    def unique_id(self) -> str:
        return f"{DOMAIN}_room_{self._room_id}"

    @property
    def device_info(self):
        return device.regler_device_info(
            self._gateway_unique_id, self._room_id, self._room_name
        )

    @property
    def current_temperature(self):
        # This gateway's room data does not always include actualTemperature
        # (observed on a single-zone "Regler MK1" installation with no
        # dedicated room sensor) - fall back to None (HA renders as unknown)
        # rather than crashing entity setup.
        return self._room_data.get("actualTemperature")

    @property
    def target_temperature(self):
        return self._room_data.get("desiredTemperature")

    @property
    def min_temp(self):
        # scheduleTempMin/scheduleTempMax are the confirmed-correct bounds
        # (verified against the real Smile App: 12-25 on this
        # installation) - minTemperature/maxTemperature were observed
        # identical to each other (12/12) and are not meaningful here. See
        # CLAUDE.md "Still untested / open" (now resolved) for the full
        # story. Falls back to minTemperature, then HA's own default, in
        # case scheduleTempMin is ever absent on some other installation.
        # Must still return a number when the room is missing: HA reads
        # min_temp/max_temp while writing state, not only for display.
        data = self._room_data
        return data.get("scheduleTempMin", data.get("minTemperature", super().min_temp))

    @property
    def max_temp(self):
        data = self._room_data
        return data.get("scheduleTempMax", data.get("maxTemperature", super().max_temp))

    @property
    def hvac_mode(self) -> HVACMode:
        # Reads Standby's ground-truth room membership from the
        # coordinator's dedicated scene-status poll (docs/protocol.md
        # §4e), NOT roomstatus - roomstatus can mask a still-active
        # Standby underneath any preset (its isActive flag stays True in
        # the background, invisible via roomstatus alone). Independent of
        # whether Boost/Party/Leave/Holiday also happen to be active at
        # the same time - see class docstring.
        standby_rooms = self.coordinator.data.get("scene_active_rooms", {}).get(
            SceneName.STANDBY.value, set()
        )
        return HVACMode.OFF if self._room_id in standby_rooms else HVACMode.AUTO

    @property
    def preset_mode(self) -> str:
        # Returns PRESET_NONE (not Python None) when nothing is active, so
        # HA's dropdown correctly highlights "None" as the selected entry
        # - self._active_preset itself stays str | None internally (see
        # _update_active_preset() below), only this property's return
        # value is translated.
        self._update_active_preset()
        return self._active_preset or PRESET_NONE

    def _update_active_preset(self) -> None:
        # Reads each preset's ground-truth room membership directly (docs/
        # protocol.md §4e/§4f) instead of roomstatus, which cannot be
        # trusted for compound states - this is what makes Holiday work
        # correctly even while Standby is simultaneously active (a
        # confirmed gateway firmware quirk in roomstatus itself, not
        # something fixable by changing what we send), without ever
        # touching Standby. Checked in this fixed order only for the
        # never-confirmed edge case of two presets somehow being active
        # for the same room at once (HA itself never requests more than
        # one at a time via async_set_preset_mode) - arbitrary but
        # deterministic, no worse than roomstatus's own opaque priority
        # resolution.
        active_rooms = self.coordinator.data.get("scene_active_rooms", {})
        for scene in (SceneName.PARTY, SceneName.BOOST, SceneName.HOLIDAY, SceneName.LEAVE):
            if self._room_id in active_rooms.get(scene.value, set()):
                self._active_preset = scene.value
                return
        # Covers both "Standby is active" and "plain schedule-following,
        # no scene active" - neither is a selectable preset on this
        # entity, so there is nothing meaningful to report.
        self._active_preset = None

    @_translate_gateway_errors
    async def async_set_temperature(self, **kwargs) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self.coordinator.async_api_call(
            self.coordinator.api.set_temperature, temperature, self._room_id
        )
        await self.coordinator.async_request_refresh()

    @_translate_gateway_errors
    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self._async_apply_preset(preset_mode)

    @_translate_gateway_errors
    async def async_set_preset_mode_with_duration(
        self,
        preset_mode: str,
        target: float | None = None,
    ) -> None:
        """Back the set_preset_mode_with_duration HA Action.

        Same as async_set_preset_mode(), but lets the caller override the
        activation duration for this specific call instead of always
        getting the fixed vendor default - see
        SceneManager.add_member_to_scene()/ApiMethods.set_scene() for the
        target= semantics this just forwards.
        """
        if preset_mode == PRESET_NONE and target is not None:
            raise ServiceValidationError(
                "target only applies when activating a preset - preset_mode "
                "'none' only clears the current preset, there is nothing to "
                "apply a duration to."
            )
        try:
            await self._async_apply_preset(preset_mode, target=target)
        except (NotImplementedError, ValueError) as err:
            # e.g. a SCENE_APP_LIMITS violation, raised by
            # ApiMethods.set_scene(). Re-raised as ServiceValidationError
            # so the HA UI shows the actual message instead of a raw
            # traceback. NotImplementedError is still caught here even
            # though no current scene raises it, in case a future one does.
            raise ServiceValidationError(str(err)) from err

    async def _async_apply_preset(
        self,
        preset_mode: str,
        target: float | None = None,
    ) -> None:
        previous = self._active_preset
        if previous is not None and previous != preset_mode:
            await self.coordinator.async_api_call(
                self._scene_manager.remove_member_from_scene, self._room_id, previous
            )
        if preset_mode != PRESET_NONE:
            # PRESET_NONE isn't a real gateway scene - selecting it only
            # clears whatever preset was active (handled above); there is
            # nothing to activate.
            await self.coordinator.async_api_call(
                self._scene_manager.add_member_to_scene,
                self._room_id,
                preset_mode,
                target,
            )
        self._active_preset = None if preset_mode == PRESET_NONE else preset_mode
        await self.coordinator.async_request_refresh()

    @_translate_gateway_errors
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self._async_apply_hvac_mode(hvac_mode)

    @_translate_gateway_errors
    async def async_set_hvac_mode_and_temperature(
        self,
        hvac_mode: str,
        temperature: float | None = None,
    ) -> None:
        """Back the set_hvac_mode_and_temperature HA Action.

        Exists because the standard climate.set_temperature service,
        while technically able to carry hvac_mode alongside temperature,
        does not reliably apply either when both are given together on
        this entity (see module change log) - this Action sequences the
        two writes itself instead. No extra validation beyond the schema
        (project decision, 2026-09-11): hvac_mode='off' with a
        temperature given is accepted and the temperature is simply
        ignored, same as the gateway already silently ignoring a
        temperature write while Standby is active.
        """
        await self._async_apply_hvac_mode(HVACMode(hvac_mode), temperature)

    async def _async_apply_hvac_mode(
        self,
        hvac_mode: HVACMode,
        temperature: float | None = None,
    ) -> None:
        # Standby is no longer routed through the preset-mode machinery -
        # it's toggled directly here, independent of Boost/Party/Leave/
        # Holiday, matching how it actually behaves on this hardware.
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_api_call(
                self._scene_manager.add_member_to_scene, self._room_id, SceneName.STANDBY.value
            )
        else:  # HVACMode.AUTO
            await self.coordinator.async_api_call(
                self._scene_manager.remove_member_from_scene, self._room_id, SceneName.STANDBY.value
            )
            if temperature is not None:
                # A genuine target temperature write already satisfies
                # the "must be a real change" requirement
                # _nudge_temperature_after_leaving_standby() exists for
                # (see its own docstring) - go straight to the real
                # target instead of jumping to max_temp first and then
                # immediately overwriting it with a second write.
                await self.coordinator.async_api_call(
                    self.coordinator.api.set_temperature, temperature, self._room_id
                )
            else:
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
            await self.coordinator.async_api_call(
                self.coordinator.api.set_temperature, self.max_temp, self._room_id
            )
        except Exception:  # noqa: BLE001 - deliberate, see method docstring
            _LOGGER.warning(
                "Failed to nudge temperature after leaving Standby for room %s; "
                "hvac_mode display may stay stale until the next change.",
                self._room_id,
            )

    async def _async_read_switching_times(self) -> list[dict | None]:
        """Read this room's raw switchingtimes array (takes the lock)."""
        response = await self.coordinator.async_api_call(
            self.coordinator.api.get_switching_times, self._room_name, self._room_id
        )
        return response["switchingtimes"]

    async def _read_switching_times_locked(self, api) -> list[dict | None]:
        """Same read, for use INSIDE an async_api_session() block.

        The lock is not re-entrant, so a read-modify-write sequence cannot
        call _async_read_switching_times() - it would deadlock on the
        second acquire. See coordinator.async_api_call()'s docstring.
        """
        response = await self.hass.async_add_executor_job(
            api.get_switching_times, self._room_name, self._room_id
        )
        return response["switchingtimes"]

    @_translate_gateway_errors
    async def async_get_schedule_room(self) -> dict:
        """Back the get_schedule_room HA Action.

        Returns {"monday": [...], ..., "sunday": [...]} - deliberately the
        same shape set_schedule_room's `schedule` field expects, so the
        response can be read, edited, and passed straight back.
        """
        switchingtimes = await self._async_read_switching_times()
        return switching_times_to_weekday_dict(switchingtimes)

    @_translate_gateway_errors
    async def async_set_schedule_room(self, schedule: dict) -> dict:
        """Back the set_schedule_room HA Action - writes a full week.

        Always a full-schedule replacement (the gateway has no partial-
        update endpoint - see docs/switching-times-api.md). Returns the
        freshly re-read schedule after writing, rather than trusting the
        gateway's bare success:true (see module change log).

        Reads the room's CURRENT switchingtimes first purely to learn its
        slots-per-day width, and passes that to
        weekday_dict_to_switching_times() as a fixed target - live-verified
        (2026-09-17) that the gateway rejects a switchingtimes array whose
        length doesn't match the room's own currently-configured
        slots-per-day, even though it's still a valid multiple of 7 (see
        that function's docstring for the exact error). Without this, a
        schedule needing fewer slots/day than the room's actual shape
        silently produced a too-short array and the write was rejected.
        """
        # Read and write under ONE lock acquisition: every write is a
        # full-week replacement, so another writer slipping in between
        # would be silently overwritten - see module change log.
        async with self.coordinator.async_api_session() as api:
            current = await self._read_switching_times_locked(api)
            current_slots_per_day = len(current) // 7
            try:
                switchingtimes = weekday_dict_to_switching_times(
                    schedule, slots_per_day=current_slots_per_day
                )
            except ScheduleValidationError as err:
                raise ServiceValidationError(str(err)) from err
            await self.hass.async_add_executor_job(
                api.set_switching_times,
                self._room_name,
                self._room_id,
                switchingtimes,
            )
        return await self._async_schedule_written()

    @_translate_gateway_errors
    async def async_set_schedule_room_weekday(
        self,
        weekday: str,
        slot_1_from=None,
        slot_1_to=None,
        slot_1_type=None,
        slot_2_from=None,
        slot_2_to=None,
        slot_2_type=None,
        slot_3_from=None,
        slot_3_to=None,
        slot_3_type=None,
    ) -> dict:
        """Back the set_schedule_room_weekday HA Action - writes one day.

        Read-modify-write: reads the CURRENT full schedule, replaces only
        `weekday`'s slots, leaves every other day untouched (the gateway
        has no partial-update endpoint). vol.Inclusive in
        SET_SCHEDULE_ROOM_WEEKDAY_SCHEMA already guarantees each given
        slot_N_* trio is complete (all three or none) before this runs.
        Returns the freshly re-read schedule after writing.
        """
        slots = [
            {"from": slot_from.strftime("%H:%M"), "to": slot_to.strftime("%H:%M"), "type": slot_type}
            for slot_from, slot_to, slot_type in (
                (slot_1_from, slot_1_to, slot_1_type),
                (slot_2_from, slot_2_to, slot_2_type),
                (slot_3_from, slot_3_to, slot_3_type),
            )
            if slot_from is not None
        ]
        async with self.coordinator.async_api_session() as api:
            try:
                current = await self._read_switching_times_locked(api)
                updated = replace_weekday_slots(current, weekday, slots)
            except ScheduleValidationError as err:
                raise ServiceValidationError(str(err)) from err
            await self.hass.async_add_executor_job(
                api.set_switching_times,
                self._room_name,
                self._room_id,
                updated,
            )
        return await self._async_schedule_written()

    async def _async_schedule_written(self) -> dict:
        """Re-read the schedule after a write and publish it everywhere.

        Returns the gateway's actual post-write state rather than the bare
        success:true it reports, which has repeatedly been shown to lie
        (docs/switching-times-api.md). The same re-read also feeds the
        schedule coordinator, so the per-room schedule sensor - and the
        Lovelace card bound to it - updates immediately instead of at the
        next poll.

        async_refresh(), not async_request_refresh(): the latter goes
        through HA's Debouncer and would skip the refresh entirely for a
        second write inside its cooldown, leaving the sensor stale.

        The coordinator's own re-read IS the verification read - taking
        its result rather than issuing a separate get_switching_times()
        saves a gateway round trip per write, and guarantees the caller
        and the sensor cannot disagree about what was just written. Falls
        back to a direct read only if that refresh failed, in which case
        the coordinator still holds pre-write data that must not be
        reported as verified.
        """
        await self._schedule_coordinator.async_refresh()
        if self._schedule_coordinator.last_update_success:
            room_data = (self._schedule_coordinator.data or {}).get(str(self._room_id))
            if room_data is not None:
                return room_data["schedule"]
        return await self.async_get_schedule_room()

    @_translate_gateway_errors
    async def async_set_desired_temperature(self, type: str, temperature: float) -> dict:
        """Back the set_desired_temperature HA Action.

        Writes one of the room's three fixed schedule temperatures
        (comfort_hi=desiredTempDay, comfort_lo=desiredTempDay2,
        night=desiredTempNight), then
        re-reads the room straight from the gateway and returns what is
        actually stored instead of trusting the bare success:true. The
        re-read is a direct API call, not coordinator.async_request_refresh():
        that goes through HA's Debouncer, so a second write inside its
        cooldown would return WITHOUT polling and we would report stale data.
        """
        target = _DESIRED_TEMP_KEY_TO_TARGET[type]
        try:
            sent = validate_desired_temperature(target, temperature)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

        # Deliberately not wrapped in `except ValueError`: json decode errors
        # are ValueErrors too and would be mis-reported as user input errors.
        # Range/type were already validated above; the api layer re-checks.
        await self.coordinator.async_api_call(
            self.coordinator.api.set_desired_temperature,
            temperature,
            self._room_id,
            target,
        )
        room = await self.coordinator.async_api_call(
            self.coordinator.api.get_specific_room, self._room_id
        )
        if room is None:
            raise HomeAssistantError(
                f"Room {self._room_id} was not found in the gateway's room list "
                "after writing."
            )
        room_data = room["data"]
        stored = room_data.get(DESIRED_TEMP_TARGETS[target]["field"])
        verified = stored == sent
        if not verified:
            _LOGGER.warning(
                "Desired temperature %s for room %s: sent %s but gateway reports %s",
                type,
                self._room_id,
                sent,
                stored,
            )
        await self.coordinator.async_request_refresh()
        return {
            "type": type,
            "requested": temperature,
            "sent": sent,
            "stored": stored,
            "verified": verified,
            "desired_temperatures": {
                target["response_key"]: room_data.get(target["field"])
                for target in DESIRED_TEMP_TARGETS.values()
            },
        }
