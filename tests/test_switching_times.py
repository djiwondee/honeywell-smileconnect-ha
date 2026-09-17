# Change log:
# - 2026-09-17: Initial version. Locks in the conversion/validation rules
#   for the new get_schedule_room/set_schedule_room/
#   set_schedule_room_weekday HA Actions (climate.py) - in particular, that
#   a slot with no/invalid `type` is a hard error rather than silently
#   defaulting to "L" or being treated as "Night" (see switching_times.py's
#   own change log for the product-decision background), and that
#   replace_weekday_slots() never touches any day other than the one given.
"""Tests for honeywell_smileconnect.switching_times.

Pure-Python module, no HA/gateway dependency - see the module's own
docstring.
"""
from __future__ import annotations

import pytest

from custom_components.honeywell_smileconnect.switching_times import (
    MAX_SLOTS_PER_DAY,
    WEEKDAYS,
    ScheduleValidationError,
    replace_weekday_slots,
    switching_times_to_weekday_dict,
    weekday_dict_to_switching_times,
)


def _flat_all_none(slots_per_day: int = 3) -> list[dict | None]:
    return [None] * (7 * slots_per_day)


class TestSwitchingTimesToWeekdayDict:
    def test_all_empty(self):
        result = switching_times_to_weekday_dict(_flat_all_none())
        assert result == {day: [] for day in WEEKDAYS}

    def test_single_slot_per_day(self):
        flat = _flat_all_none()
        flat[0] = {"from": "04:30", "to": "07:30", "type": "H"}  # monday, slot 0
        flat[3] = {"from": "06:00", "to": "09:00", "type": "L"}  # tuesday, slot 0
        result = switching_times_to_weekday_dict(flat)
        assert result["monday"] == [{"from": "04:30", "to": "07:30", "type": "H"}]
        assert result["tuesday"] == [{"from": "06:00", "to": "09:00", "type": "L"}]
        assert result["wednesday"] == []

    def test_multiple_slots_same_day(self):
        flat = _flat_all_none()
        flat[0] = {"from": "04:30", "to": "07:30", "type": "H"}
        flat[1] = {"from": "16:00", "to": "22:00", "type": "L"}
        result = switching_times_to_weekday_dict(flat)
        assert result["monday"] == [
            {"from": "04:30", "to": "07:30", "type": "H"},
            {"from": "16:00", "to": "22:00", "type": "L"},
        ]

    def test_invalid_length_raises(self):
        with pytest.raises(ScheduleValidationError):
            switching_times_to_weekday_dict([None] * 10)  # not a multiple of 7


class TestWeekdayDictToSwitchingTimes:
    def test_empty_schedule_produces_well_formed_list(self):
        result = weekday_dict_to_switching_times({})
        assert len(result) == 7
        assert all(slot is None for slot in result)

    def test_missing_weekday_key_means_no_slots(self):
        result = weekday_dict_to_switching_times({"monday": []})
        assert len(result) == 7
        assert all(slot is None for slot in result)

    def test_unknown_weekday_key_raises(self):
        with pytest.raises(ScheduleValidationError, match="Unknown weekday"):
            weekday_dict_to_switching_times({"someday": []})

    def test_single_slot_round_trips(self):
        schedule = {"monday": [{"from": "04:30", "to": "07:30", "type": "H"}]}
        flat = weekday_dict_to_switching_times(schedule)
        assert len(flat) == 7  # slots_per_day derived as 1 (busiest day has 1 slot)
        back = switching_times_to_weekday_dict(flat)
        assert back["monday"] == schedule["monday"]
        assert back["tuesday"] == []

    def test_more_than_max_slots_raises(self):
        schedule = {
            "monday": [
                {"from": "01:00", "to": "02:00", "type": "H"},
                {"from": "03:00", "to": "04:00", "type": "H"},
                {"from": "05:00", "to": "06:00", "type": "H"},
                {"from": "07:00", "to": "08:00", "type": "H"},
            ]
        }
        with pytest.raises(ScheduleValidationError, match="only supports"):
            weekday_dict_to_switching_times(schedule)

    def test_exactly_max_slots_is_allowed(self):
        schedule = {
            "monday": [
                {"from": "01:00", "to": "02:00", "type": "H"},
                {"from": "03:00", "to": "04:00", "type": "H"},
                {"from": "05:00", "to": "06:00", "type": "H"},
            ]
        }
        assert len(schedule["monday"]) == MAX_SLOTS_PER_DAY
        flat = weekday_dict_to_switching_times(schedule)
        assert len(flat) == 7 * MAX_SLOTS_PER_DAY

    def test_missing_type_raises(self):
        schedule = {"monday": [{"from": "04:30", "to": "07:30"}]}
        with pytest.raises(ScheduleValidationError, match="missing required field"):
            weekday_dict_to_switching_times(schedule)

    def test_invalid_type_raises(self):
        schedule = {"monday": [{"from": "04:30", "to": "07:30", "type": "X"}]}
        with pytest.raises(ScheduleValidationError, match="invalid type"):
            weekday_dict_to_switching_times(schedule)

    def test_night_type_rejected_with_specific_message(self):
        """"N" is a real, independent switching type - but requires the
        SRC-10 hardware extension this project cannot verify against, and
        must NEVER be silently treated as "no type given". See module
        change log.
        """
        schedule = {"monday": [{"from": "22:00", "to": "23:00", "type": "N"}]}
        with pytest.raises(ScheduleValidationError, match="SRC-10"):
            weekday_dict_to_switching_times(schedule)

    def test_from_after_to_raises(self):
        schedule = {"monday": [{"from": "10:00", "to": "09:00", "type": "H"}]}
        with pytest.raises(ScheduleValidationError, match="must be before"):
            weekday_dict_to_switching_times(schedule)

    def test_overlapping_slots_raise(self):
        schedule = {
            "monday": [
                {"from": "04:00", "to": "10:00", "type": "H"},
                {"from": "08:00", "to": "12:00", "type": "L"},
            ]
        }
        with pytest.raises(ScheduleValidationError, match="overlapping"):
            weekday_dict_to_switching_times(schedule)

    def test_explicit_slots_per_day_pads_to_that_width(self):
        """Regression test for the live-verified gateway rejection:
        sending a 2-slots/day (14-element) array to a room whose gateway-
        side shape is fixed at 3 slots/day (21 elements) failed with
        "The input format is invalid: 14". set_schedule_room must fix the
        output width to the room's own current slots_per_day, not derive
        it from the busiest day in the given schedule.
        """
        schedule = {
            "monday": [
                {"from": "04:30", "to": "08:30", "type": "H"},
                {"from": "14:30", "to": "16:30", "type": "L"},
            ],
            "tuesday": [{"from": "04:30", "to": "07:30", "type": "H"}],
        }
        flat = weekday_dict_to_switching_times(schedule, slots_per_day=3)
        assert len(flat) == 21  # 7 * 3, NOT 7 * 2
        back = switching_times_to_weekday_dict(flat)
        assert back["monday"] == schedule["monday"]
        assert back["tuesday"] == schedule["tuesday"]
        assert back["wednesday"] == []

    def test_explicit_slots_per_day_too_few_raises_with_specific_message(self):
        schedule = {
            "monday": [
                {"from": "01:00", "to": "02:00", "type": "H"},
                {"from": "03:00", "to": "04:00", "type": "H"},
            ]
        }
        with pytest.raises(ScheduleValidationError, match="current schedule only has room for"):
            weekday_dict_to_switching_times(schedule, slots_per_day=1)

    def test_full_week_round_trip(self):
        schedule = {
            "monday": [{"from": "04:30", "to": "07:30", "type": "H"}],
            "tuesday": [],
            "wednesday": [
                {"from": "06:00", "to": "09:00", "type": "H"},
                {"from": "16:00", "to": "22:00", "type": "L"},
            ],
            "thursday": [],
            "friday": [],
            "saturday": [],
            "sunday": [{"from": "08:00", "to": "20:00", "type": "L"}],
        }
        flat = weekday_dict_to_switching_times(schedule)
        back = switching_times_to_weekday_dict(flat)
        assert back == schedule


class TestReplaceWeekdaySlots:
    def test_replaces_only_target_day(self):
        flat = weekday_dict_to_switching_times(
            {
                "monday": [{"from": "04:30", "to": "07:30", "type": "H"}],
                "tuesday": [{"from": "05:00", "to": "08:00", "type": "L"}],
            },
            max_slots_per_day=3,
        )
        new_slots = [{"from": "06:00", "to": "09:00", "type": "L"}]
        result = replace_weekday_slots(flat, "monday", new_slots)

        back = switching_times_to_weekday_dict(result)
        assert back["monday"] == new_slots
        assert back["tuesday"] == [{"from": "05:00", "to": "08:00", "type": "L"}]

    def test_clearing_a_day_with_empty_slots(self):
        flat = weekday_dict_to_switching_times(
            {"monday": [{"from": "04:30", "to": "07:30", "type": "H"}]}
        )
        result = replace_weekday_slots(flat, "monday", [])
        back = switching_times_to_weekday_dict(result)
        assert back["monday"] == []

    def test_too_many_slots_for_current_shape_raises(self):
        # Current schedule has slots_per_day=1 (derived from its own length).
        flat = weekday_dict_to_switching_times(
            {"monday": [{"from": "04:30", "to": "07:30", "type": "H"}]}
        )
        assert len(flat) == 7  # slots_per_day == 1
        too_many = [
            {"from": "01:00", "to": "02:00", "type": "H"},
            {"from": "03:00", "to": "04:00", "type": "H"},
        ]
        with pytest.raises(ScheduleValidationError, match="only has room for"):
            replace_weekday_slots(flat, "monday", too_many)

    def test_unknown_weekday_raises(self):
        flat = _flat_all_none()
        with pytest.raises(ScheduleValidationError, match="Unknown weekday"):
            replace_weekday_slots(flat, "someday", [])

    def test_overlapping_new_slots_raise(self):
        # Seed a schedule whose slots_per_day is 3 (via a 3-slot Tuesday),
        # so Monday has room for the 2 slots being tested below.
        flat = weekday_dict_to_switching_times(
            {
                "tuesday": [
                    {"from": "01:00", "to": "02:00", "type": "H"},
                    {"from": "03:00", "to": "04:00", "type": "H"},
                    {"from": "05:00", "to": "06:00", "type": "H"},
                ]
            }
        )
        overlapping = [
            {"from": "04:00", "to": "10:00", "type": "H"},
            {"from": "08:00", "to": "12:00", "type": "L"},
        ]
        with pytest.raises(ScheduleValidationError, match="overlapping"):
            replace_weekday_slots(flat, "monday", overlapping)
