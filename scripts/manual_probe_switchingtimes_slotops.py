# Change log:
# - 2026-09-09: v2. Regression check after api_methods.py was updated -
#   calls api.set_switching_times() directly instead of building the set2
#   request by hand (see manual_probe_switchingtimes_echo_v3.py's change
#   log for the same rationale). The contiguous-slot validation that used
#   to live only in this script's own logic now lives in api_methods.py's
#   _validate_switching_times() - this script no longer pre-checks
#   anything itself, it just calls set_switching_times() and reports
#   whichever ValueError comes back (if any), which is itself part of
#   what's being regression-tested here.
"""Manual diagnostic: create/edit/delete a switching-time slot via the real
ApiMethods.set_switching_times(), not a hand-built request.

Run this directly in the dev container terminal:

    python3 scripts/manual_probe_switchingtimes_slotops_v2.py

Menu-driven, same three operations as v1:
    1) Feld eines bestehenden Slots ändern
    2) Neuen Slot in einer leeren Position anlegen
    3) Bestehenden Slot löschen (auf leer zurücksetzen)

Each operation builds a modified copy of the schedule list (in get2's own
shape - list of {"from","to","type"} dicts or None) and passes it straight
to api.set_switching_times(). Any contiguous-slot violation is now caught
inside api_methods.py itself (ValueError) rather than by this script.

Never stores credentials.
"""
from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "custom_components"))

from honeywell_smileconnect.api.api_methods import ApiMethods  # noqa: E402
from honeywell_smileconnect.api.login import Login  # noqa: E402

DAY_NAMES = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _slots_per_day(switchingtimes: list) -> int:
    return len(switchingtimes) // 7


def _describe_slot(index: int, slot: dict | None, slots_per_day: int) -> str:
    day = DAY_NAMES[index // slots_per_day]
    slot_no = index % slots_per_day + 1
    if slot is None:
        return f"[{index:2d}] {day} Slot {slot_no}: (leer)"
    return f"[{index:2d}] {day} Slot {slot_no}: {slot['from']}-{slot['to']} ({slot['type']})"


def _print_schedule(slots: list, slots_per_day: int, highlight: int | None = None) -> None:
    for i, slot in enumerate(slots):
        marker = "  <-- geändert" if i == highlight else ""
        print("  " + _describe_slot(i, slot, slots_per_day) + marker)


def _apply_and_verify(
    api: ApiMethods, room_name, room_id, before_slots: list, new_slots: list, index: int,
    expect, label: str, slots_per_day: int,
) -> None:
    print("\n" + "=" * 60)
    print(f"Änderung: {label}")
    print("=" * 60)

    confirm = input(
        "\nDas schreibt JETZT über api.set_switching_times() auf dein echtes "
        "Gateway. 'yes' zum Fortfahren, sonst Abbruch: "
    ).strip().lower()
    if confirm != "yes":
        print("Abgebrochen - set_switching_times() wurde nicht aufgerufen.")
        return

    print("\nRufe api.set_switching_times() auf ...\n")
    try:
        set_response = api.set_switching_times(room_name, room_id, new_slots)
    except ValueError as exc:
        print(f"set_switching_times() hat VOR dem Senden abgelehnt: {exc}")
        print("(Das ist die eingebaute Validierung in api_methods.py - falls du das")
        print(" nicht erwartet hast, ist das selbst ein Ergebnis wert.)")
        return

    print(json.dumps(set_response, indent=2, ensure_ascii=False))

    print("\nRufe get_switching_times() erneut auf, um die Wirkung zu prüfen ...\n")
    after = api.get_switching_times(room_name, room_id)
    after_slots = after["switchingtimes"]
    _print_schedule(after_slots, slots_per_day, highlight=index)

    print("\n" + "=" * 60)
    if expect(after_slots[index]):
        print(f"ERGEBNIS: Index {index} zeigt den erwarteten Zustand.")
        print(f"  vorher:  {before_slots[index]!r}")
        print(f"  nachher: {after_slots[index]!r}")
    else:
        print(
            f"ERGEBNIS: Index {index} entspricht NICHT dem erwarteten Zustand.")
        print(f"  vorher:  {before_slots[index]!r}")
        print(f"  nachher: {after_slots[index]!r}")
    print("=" * 60)

    revert = input(
        "\nZurück auf den ursprünglichen Zustand setzen? [Enter=ja, n=nein lassen]: "
    ).strip().lower()
    if revert == "n":
        print("Änderung bleibt bestehen wie getestet.")
        return

    print("\nSetze Original-Zustand über api.set_switching_times() zurück ...\n")
    revert_response = api.set_switching_times(room_name, room_id, before_slots)
    print(json.dumps(revert_response, indent=2, ensure_ascii=False))
    final = api.get_switching_times(room_name, room_id)
    if final["switchingtimes"] == before_slots:
        print("\nZurückgesetzt - Zeitplan entspricht wieder dem Ausgangszustand.")
    else:
        print("\nWARNUNG: Zeitplan entspricht NICHT mehr exakt dem Ausgangszustand.")
        print("Bitte manuell in der Smile App prüfen.")
        print(f"  erwartet: {before_slots}")
        print(f"  aktuell:  {final['switchingtimes']}")


def _zero_pad_hour(time_str: str) -> str:
    """"4:30" -> "04:30", "14:50" unchanged - normalizes user-typed times
    to get2's own zero-padded format, for comparing against what get2
    reports back afterward (api_methods.py strips the leading zero again
    internally before sending, this is purely for the verification step).
    """
    hour, _, minute = time_str.partition(":")
    if len(hour) == 1:
        hour = "0" + hour
    return f"{hour}:{minute}"


def op_edit_field(api, room_name, room_id, before_slots, slots_per_day) -> None:
    index = int(
        input(f"Welchen Slot-Index ändern? [0-{len(before_slots) - 1}]: ").strip())
    original_slot = before_slots[index]
    print(f"Aktueller Wert an Index {index}: {original_slot!r}")
    field = input("Welches Feld ändern? [from/to/type]: ").strip().lower()
    new_value = input(f"Neuer Wert für '{field}': ").strip()

    if original_slot is None:
        print("Dieser Slot ist leer - für 'Feld ändern' bitte einen belegten Slot wählen")
        print("(zum Anlegen eines neuen Slots stattdessen Option 2 nutzen).")
        return

    new_slots = [dict(s) if s is not None else None for s in before_slots]
    new_slots[index] = dict(original_slot)
    new_slots[index][field] = new_value

    expected_value = _zero_pad_hour(
        new_value) if field in ("from", "to") else new_value

    _apply_and_verify(
        api, room_name, room_id, before_slots, new_slots, index,
        expect=lambda s: s is not None and s[field] == expected_value,
        label=f"Feld '{field}' an Index {index} -> '{new_value}'",
        slots_per_day=slots_per_day,
    )


def op_create_slot(api, room_name, room_id, before_slots, slots_per_day) -> None:
    empty_indices = [i for i, s in enumerate(before_slots) if s is None]
    print("Leere Slots:", ", ".join(str(i)
          for i in empty_indices) or "(keine)")
    index = int(input("Welchen leeren Slot-Index belegen? ").strip())
    from_val = input("from (z.B. 12:00): ").strip()
    to_val = input("to (z.B. 13:00): ").strip()
    type_val = input("type (z.B. H oder L): ").strip()

    new_slots = [dict(s) if s is not None else None for s in before_slots]
    new_slots[index] = {"from": from_val, "to": to_val, "type": type_val}

    _apply_and_verify(
        api, room_name, room_id, before_slots, new_slots, index,
        expect=lambda s: s is not None and s["type"] == type_val,
        label=f"Neuer Slot an Index {index}: {from_val}-{to_val} ({type_val})",
        slots_per_day=slots_per_day,
    )


def op_delete_slot(api, room_name, room_id, before_slots, slots_per_day) -> None:
    filled_indices = [i for i, s in enumerate(before_slots) if s is not None]
    print("Belegte Slots:", ", ".join(str(i)
          for i in filled_indices) or "(keine)")
    index = int(input("Welchen Slot-Index löschen? ").strip())
    if before_slots[index] is None:
        print(f"Index {index} ist bereits leer - nichts zu tun.")
        return

    new_slots = [dict(s) if s is not None else None for s in before_slots]
    new_slots[index] = None

    _apply_and_verify(
        api, room_name, room_id, before_slots, new_slots, index,
        expect=lambda s: s is None,
        label=f"Slot an Index {index} löschen (war: {before_slots[index]!r})",
        slots_per_day=slots_per_day,
    )


def main() -> None:
    host = input(
        "Gateway host/IP [192.168.1.132]: ").strip() or "192.168.1.132"
    username = input("Username: ").strip()
    password = getpass.getpass("Password (hidden): ")
    base_url = f"http://{host}"

    print(f"\nLogging in to {base_url} ...")
    login = Login(base_url)
    credentials = login.authorize(username, password)
    print("Login successful.\n")

    api = ApiMethods(credentials, base_url)

    rooms = api.get_rooms_list()
    if not rooms:
        print("No rooms found - nothing to test.")
        return

    print("Rooms found:")
    for i, room in enumerate(rooms):
        print(f"  [{i}] {room['name']} (id={room['data']['id']})")
    room_idx = 0 if len(rooms) == 1 else int(
        input(f"Which room to test? [0-{len(rooms) - 1}]: ").strip()
    )
    room_id = rooms[room_idx]["data"]["id"]
    room_name = rooms[room_idx]["name"]
    print(f"\nUsing room: {room_name} (id={room_id})\n")

    print("Calling get_switching_times() (BEFORE) ...\n")
    before = api.get_switching_times(room_name, room_id)
    if not before.get("success"):
        print("get2 itself did not report success - aborting.")
        return
    before_slots = before["switchingtimes"]
    slots_per_day = _slots_per_day(before_slots)

    print("Aktuelle Schaltzeiten:")
    _print_schedule(before_slots, slots_per_day)

    print(
        "\nWelche Operation testen?\n"
        "  1) Feld eines bestehenden Slots ändern\n"
        "  2) Neuen Slot in einer leeren Position anlegen\n"
        "  3) Bestehenden Slot löschen (auf leer zurücksetzen)"
    )
    choice = input("Auswahl [1/2/3]: ").strip()

    if choice == "1":
        op_edit_field(api, room_name, room_id, before_slots, slots_per_day)
    elif choice == "2":
        op_create_slot(api, room_name, room_id, before_slots, slots_per_day)
    elif choice == "3":
        op_delete_slot(api, room_name, room_id, before_slots, slots_per_day)
    else:
        print("Ungültige Auswahl - Abbruch.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - deliberately broad for a diagnostic script
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
