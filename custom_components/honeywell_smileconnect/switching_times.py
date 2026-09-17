"""Conversion/validation helpers for room switching-time schedules.

Translates between the gateway's flat, day-major `switchingtimes` wire
format (see docs/switching-times-api.md - a list of length `7 *
slots_per_day`, each entry `None` or `{"from","to","type"}`) and a
per-weekday dict shape (`{"monday": [...], ..., "sunday": [...]}`) that is
natural to expose as HA Action parameters/responses.

Deliberately has no `homeassistant.*` import - kept HA-independent so it is
fully unit-testable without a gateway or an HA test harness (see
tests/test_switching_times.py). Consumed by climate.py's
get_schedule_room/set_schedule_room/set_schedule_room_weekday HA Actions.
"""
# Change log:
# - 2026-09-17: Initial implementation, backing the new get_schedule_room/
#   set_schedule_room/set_schedule_room_weekday HA Actions (see climate.py's
#   own change log). Encodes two product decisions made during planning
#   (see CLAUDE.md and docs/switching-times-api.md):
#   1. There is NO implicit default type for a slot - "type" is required
#      whenever a slot is defined at all. Earlier reasoning had considered
#      defaulting a missing type to "L" ("Comfort Lo"), on the assumption
#      that time not covered by an explicit slot is implicitly "Night".
#      The user corrected this: "Night" (wire code "N") is its own
#      independent switching type, not a fallback for "no type given" -
#      conflating the two would be guessing, not verifying. "N" itself is
#      deliberately NOT accepted as a settable value here since it requires
#      the Honeywell Room Connect SRC-10 hardware extension to be
#      meaningful, which is not available for testing on this project's
#      hardware (see VALID_TYPES).
#   2. MAX_SLOTS_PER_DAY=3 is this hardware's empirically confirmed ceiling
#      (docs/switching-times-api.md), used as a default - but
#      replace_weekday_slots() (used by set_schedule_room_weekday) derives
#      the ACTUAL ceiling from the current live schedule's own length
#      rather than trusting the constant, since a single-weekday write must
#      not change the slot-count shape of every other day.
from __future__ import annotations

from itertools import pairwise

WEEKDAYS: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

# Empirically confirmed ceiling for this hardware (docs/switching-times-api.md).
# Used as the default when no live schedule is available yet to derive the
# real ceiling from (see weekday_dict_to_switching_times's max_slots_per_day
# parameter, and replace_weekday_slots which always prefers the live value).
MAX_SLOTS_PER_DAY = 3

# Only "H" (Comfort Hi) and "L" (Comfort Lo) are accepted. "N" ("Night") is
# a real, independent switching type in the protocol - not a fallback value
# for "no type given" - but requires the Honeywell Room Connect SRC-10
# hardware extension to be meaningful, which this project cannot verify
# against real hardware. There is deliberately NO default type: every
# defined slot must specify one explicitly.
VALID_TYPES = ("H", "L")


class ScheduleValidationError(ValueError):
    """A switching-time schedule failed validation.

    Plain ValueError subclass (not a new exception hierarchy) - callers in
    climate.py already catch ValueError from other API-layer validation
    (see async_set_preset_mode_with_duration) and re-raise as
    ServiceValidationError; this keeps that pattern working unchanged while
    still letting tests/callers distinguish "our own validation" from
    other ValueErrors if they ever need to.
    """


def switching_times_to_weekday_dict(switchingtimes: list[dict | None]) -> dict[str, list[dict]]:
    """Flat gateway list -> {"monday": [...], ..., "sunday": [...]}.

    `slots_per_day` is derived from `len(switchingtimes) // 7`, never
    hardcoded - matches ApiMethods._validate_switching_times()'s own rule.
    `None` slots are dropped entirely: a day with no active window is
    simply an empty list, not a list padded with placeholders.
    """
    if len(switchingtimes) % 7 != 0:
        raise ScheduleValidationError(
            f"switchingtimes length ({len(switchingtimes)}) is not a "
            "multiple of 7 - expected a day-major list (7 days x N slots/day)."
        )
    slots_per_day = len(switchingtimes) // 7
    result: dict[str, list[dict]] = {}
    for day_idx, day_name in enumerate(WEEKDAYS):
        day_slots = switchingtimes[day_idx * slots_per_day : (day_idx + 1) * slots_per_day]
        result[day_name] = [
            {"from": slot["from"], "to": slot["to"], "type": slot["type"]}
            for slot in day_slots
            if slot is not None
        ]
    return result


def _validate_slot_shape(day_name: str, slot_idx: int, slot: dict) -> None:
    """Check a slot dict has non-empty from/to/type and a supported type.

    Used only for the free-form `set_schedule_room` input path - the
    granular `set_schedule_room_weekday` fields already guarantee complete,
    schema-valid slots via voluptuous (vol.Inclusive groups + vol.In), so
    it only needs the lighter from<to/overlap checks in
    _check_order_and_overlap().
    """
    missing = [k for k in ("from", "to", "type") if not slot.get(k)]
    if missing:
        raise ScheduleValidationError(
            f"{day_name}, slot {slot_idx + 1}: missing required field(s) "
            f"{missing} - a defined slot needs from, to, AND type; there is "
            "no default type."
        )
    if slot["type"] not in VALID_TYPES:
        if slot["type"] == "N":
            raise ScheduleValidationError(
                f"{day_name}, slot {slot_idx + 1}: type 'N' (Night) requires "
                "the Honeywell Room Connect SRC-10 hardware extension, which "
                "this integration does not support - use 'H' or 'L'."
            )
        raise ScheduleValidationError(
            f"{day_name}, slot {slot_idx + 1}: invalid type {slot['type']!r} "
            f"- must be one of {VALID_TYPES}."
        )


def _check_order_and_overlap(day_name: str, slots: list[dict]) -> list[dict]:
    """Validate from<to per slot and no overlaps; returns slots sorted by from."""
    for slot_idx, slot in enumerate(slots):
        if slot["from"] >= slot["to"]:
            raise ScheduleValidationError(
                f"{day_name}, slot {slot_idx + 1}: 'from' ({slot['from']}) "
                f"must be before 'to' ({slot['to']})."
            )
    ordered = sorted(slots, key=lambda s: s["from"])
    for prev, cur in pairwise(ordered):
        if cur["from"] < prev["to"]:
            raise ScheduleValidationError(
                f"{day_name}: overlapping slots ({prev['from']}-{prev['to']} "
                f"and {cur['from']}-{cur['to']})."
            )
    return ordered


def weekday_dict_to_switching_times(
    schedule: dict[str, list[dict]], *, max_slots_per_day: int = MAX_SLOTS_PER_DAY
) -> list[dict | None]:
    """Reverse of switching_times_to_weekday_dict(), with full validation.

    A weekday key missing from `schedule` means "no slots that day" (empty
    list), not an error. Raises ScheduleValidationError (a ValueError
    subclass) - never silently guesses - for: an unknown weekday key, more
    than `max_slots_per_day` slots on one day, a slot missing from/to/type,
    an invalid/unsupported type, `from >= to`, or overlapping slots on the
    same day.
    """
    unknown = set(schedule) - set(WEEKDAYS)
    if unknown:
        raise ScheduleValidationError(f"Unknown weekday key(s): {sorted(unknown)}")

    per_day: dict[str, list[dict]] = {}
    for day_name in WEEKDAYS:
        day_slots = schedule.get(day_name) or []
        if len(day_slots) > max_slots_per_day:
            raise ScheduleValidationError(
                f"{day_name}: {len(day_slots)} slots given, but this gateway "
                f"only supports {max_slots_per_day} slots per day."
            )
        for slot_idx, slot in enumerate(day_slots):
            _validate_slot_shape(day_name, slot_idx, slot)
        per_day[day_name] = _check_order_and_overlap(day_name, day_slots) if day_slots else []

    # At least 1 slot/day so a fully-empty schedule ("clear everything")
    # still produces a well-formed 7-element list rather than a degenerate
    # zero-length one.
    slots_per_day = max((len(v) for v in per_day.values()), default=1) or 1

    result: list[dict | None] = []
    for day_name in WEEKDAYS:
        day_slots = per_day[day_name]
        result.extend(day_slots + [None] * (slots_per_day - len(day_slots)))
    return result


def replace_weekday_slots(
    switchingtimes: list[dict | None], weekday: str, slots: list[dict]
) -> list[dict | None]:
    """Read-modify-write helper backing set_schedule_room_weekday.

    Replaces just `weekday`'s slots within an existing full switchingtimes
    list (as returned by ApiMethods.get_switching_times()), leaving every
    other day byte-for-byte unchanged - required because the gateway has
    no partial-update endpoint (see docs/switching-times-api.md, point 4).

    `slots_per_day` is derived from the CURRENT list's own length, not
    MAX_SLOTS_PER_DAY - a single-weekday write must not change the slot-
    count shape of every other day. Raises ScheduleValidationError if
    `slots` has more entries than the current schedule supports, if any
    slot has from>=to, or if slots overlap each other.
    """
    if weekday not in WEEKDAYS:
        raise ScheduleValidationError(f"Unknown weekday {weekday!r} - must be one of {WEEKDAYS}.")
    if len(switchingtimes) % 7 != 0:
        raise ScheduleValidationError(
            f"switchingtimes length ({len(switchingtimes)}) is not a "
            "multiple of 7 - expected a day-major list (7 days x N slots/day)."
        )
    slots_per_day = len(switchingtimes) // 7
    if len(slots) > slots_per_day:
        raise ScheduleValidationError(
            f"{weekday}: {len(slots)} slots given, but the current schedule "
            f"only has room for {slots_per_day} slot(s) per day - use "
            "set_schedule_room to change the number of slots per day."
        )
    ordered = _check_order_and_overlap(weekday, slots) if slots else []
    padded = ordered + [None] * (slots_per_day - len(ordered))

    day_idx = WEEKDAYS.index(weekday)
    result = list(switchingtimes)
    result[day_idx * slots_per_day : (day_idx + 1) * slots_per_day] = padded
    return result
