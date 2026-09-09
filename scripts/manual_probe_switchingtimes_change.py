# Change log:
# - 2026-09-09: v3. Builds on the confirmed-working echo format from
#   manual_probe_switchingtimes_echo_v2.py (wire key "from" via setattr,
#   hours without leading zero). That test only proved the format
#   round-trips when nothing actually changes. This script deliberately
#   modifies exactly one slot, confirms via get2 that the gateway really
#   applied it (not just returned success:true without effect - seen
#   before elsewhere in this project, e.g. the Standby roomstatus bug),
#   and offers to revert to the original schedule afterward.
"""Manual diagnostic: deliberately change one switching-time slot via set2,
verify it actually took effect, then optionally revert.

Run this directly in the dev container terminal:

    python3 scripts/manual_probe_switchingtimes_change.py

Workflow:
    1. Calls get2, shows all 21 slots with their index so you can pick one.
    2. You pick a slot index and a new "from"/"to" value (HH:MM, with or
       without leading zero - normalized automatically) or a new "type".
       Only that one slot's field changes; all 20 others are sent back
       unchanged (same corrected from/to/type CSV format as v2).
    3. Shows exactly what will be sent and asks for confirmation.
    4. Calls set2, then re-calls get2 and checks that the picked slot now
       shows the new value - not just that set2 returned success:true.
    5. Asks whether to revert the slot back to its original value (calls
       set2 again with the original schedule if you say yes).

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
from honeywell_smileconnect.api.api_request import ApiRequest  # noqa: E402
from honeywell_smileconnect.api.default_params import DefaultApiParams  # noqa: E402
from honeywell_smileconnect.api.login import Login  # noqa: E402

SLOT_COUNT = 21  # 7 days x 3 slots/day, day-major order (confirmed live)
DAY_NAMES = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _strip_leading_zero_hour(time_str: str) -> str:
    """"04:30" -> "4:30", "14:50" unchanged, "06:00" -> "6:00". Confirmed
    live: set2 wants hours without a leading zero, get2 returns them
    zero-padded.
    """
    hour, _, minute = time_str.partition(":")
    if len(hour) == 2 and hour.startswith("0"):
        hour = hour[1]
    return f"{hour}:{minute}"


def _normalize_user_time(time_str: str) -> str:
    """Accepts whatever the user types (e.g. "4:30" or "04:30") and
    returns it in the wire format set2 expects (no leading zero).
    """
    time_str = time_str.strip()
    return _strip_leading_zero_hour(time_str)


def _slots_to_lists(switchingtimes: list) -> tuple[list[str], list[str], list[str]]:
    """get2's 21-element array -> three parallel lists (already in wire
    format, i.e. leading-zero stripped), empty string for null slots.
    """
    if len(switchingtimes) != SLOT_COUNT:
        raise ValueError(
            f"Expected {SLOT_COUNT} slots from get2, got {len(switchingtimes)}."
        )
    froms, tos, types = [], [], []
    for slot in switchingtimes:
        if slot is None:
            froms.append("")
            tos.append("")
            types.append("")
        else:
            froms.append(_strip_leading_zero_hour(slot["from"]))
            tos.append(_strip_leading_zero_hour(slot["to"]))
            types.append(slot["type"])
    return froms, tos, types


def _describe_slot(index: int, slot: dict | None) -> str:
    day = DAY_NAMES[index // 3]
    slot_no = index % 3 + 1
    if slot is None:
        return f"[{index:2d}] {day} Slot {slot_no}: (leer)"
    return f"[{index:2d}] {day} Slot {slot_no}: {slot['from']}-{slot['to']} ({slot['type']})"


def _call_set2(api: ApiMethods, room_name: str, room_id, froms, tos, types) -> dict:
    """Corrected set2 call: wire key "from" via setattr (params.from = ...
    is a Python syntax error), plain params.to/params.type - see
    manual_probe_switchingtimes_echo_v2.py's change log for why.
    """
    params = DefaultApiParams()
    params.roomid = room_id
    params.roomname = room_name
    setattr(params, "from", ",".join(froms))
    params.to = ",".join(tos)
    params.type = ",".join(types)
    return ApiRequest().request(
        api.base_url + "/api/room/switchingtimes/set2", api.credentials, params
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

    print("Calling get2 (BEFORE) ...\n")
    before = api.get_switching_times(room_name, room_id)
    if not before.get("success"):
        print("get2 itself did not report success - aborting.")
        return
    before_slots = before["switchingtimes"]

    print("Aktuelle Schaltzeiten:")
    for i, slot in enumerate(before_slots):
        print("  " + _describe_slot(i, slot))

    index = int(
        input(f"\nWelchen Slot-Index willst du ändern? [0-{SLOT_COUNT - 1}]: ").strip())
    if not (0 <= index < SLOT_COUNT):
        print("Ungültiger Index - Abbruch.")
        return

    original_slot = before_slots[index]
    print(f"\nAktueller Wert an Index {index}: {original_slot!r}")
    field = input("Welches Feld ändern? [from/to/type]: ").strip().lower()
    if field not in ("from", "to", "type"):
        print("Ungültiges Feld - Abbruch.")
        return
    new_value = input(
        f"Neuer Wert für '{field}' (z.B. 05:00 oder H): ").strip()
    if field in ("from", "to"):
        new_value = _normalize_user_time(new_value)

    froms, tos, types = _slots_to_lists(before_slots)
    if field == "from":
        froms[index] = new_value
    elif field == "to":
        tos[index] = new_value
    else:
        types[index] = new_value

    print("\n" + "=" * 60)
    print(f"Änderung: Index {index} ({DAY_NAMES[index // 3]} Slot {index % 3 + 1}), "
          f"Feld '{field}' -> '{new_value}'")
    print("=" * 60)
    print(f"  from = {','.join(froms)}")
    print(f"  to   = {','.join(tos)}")
    print(f"  type = {','.join(types)}")
    print("=" * 60)

    confirm = input(
        "\nDas schreibt JETZT auf dein echtes Gateway. 'yes' zum Fortfahren, "
        "sonst Abbruch: "
    ).strip().lower()
    if confirm != "yes":
        print("Abgebrochen - set2 wurde nicht aufgerufen.")
        return

    print("\nRufe set2 auf ...\n")
    set_response = _call_set2(api, room_name, room_id, froms, tos, types)
    print(json.dumps(set_response, indent=2, ensure_ascii=False))

    print("\nRufe get2 erneut auf, um die tatsächliche Wirkung zu prüfen ...\n")
    after = api.get_switching_times(room_name, room_id)
    after_slots = after["switchingtimes"]
    print("Neue Schaltzeiten:")
    for i, slot in enumerate(after_slots):
        marker = "  <-- geändert" if i == index else ""
        print("  " + _describe_slot(i, slot) + marker)

    changed_slot = after_slots[index]
    print("\n" + "=" * 60)
    if changed_slot != original_slot:
        print(f"ERGEBNIS: Slot {index} hat sich tatsächlich geändert.")
        print(f"  vorher:  {original_slot!r}")
        print(f"  nachher: {changed_slot!r}")
        print("-> set2 wirkt wie erwartet, nicht nur success:true ohne echten Effekt.")
    else:
        print(
            f"ERGEBNIS: Slot {index} zeigt weiterhin den alten Wert ({original_slot!r}).")
        print("-> set2 hat success:true zurückgegeben, aber die Änderung kam nicht an.")
        print("   Bitte zusätzlich in der Smile App nachsehen.")
    print("=" * 60)

    revert = input(
        "\nZurück auf den ursprünglichen Wert setzen? [Enter=ja, n=nein lassen]: "
    ).strip().lower()
    if revert == "n":
        print("Änderung bleibt bestehen wie getestet.")
        return

    print("\nSetze Original-Werte zurück ...\n")
    orig_froms, orig_tos, orig_types = _slots_to_lists(before_slots)
    revert_response = _call_set2(
        api, room_name, room_id, orig_froms, orig_tos, orig_types)
    print(json.dumps(revert_response, indent=2, ensure_ascii=False))

    final = api.get_switching_times(room_name, room_id)
    if final["switchingtimes"] == before_slots:
        print("\nZurückgesetzt - Zeitplan entspricht wieder dem Ausgangszustand.")
    else:
        print("\nWARNUNG: Zeitplan entspricht NICHT mehr exakt dem Ausgangszustand.")
        print("Bitte manuell in der Smile App prüfen.")
        print(f"  erwartet: {before_slots}")
        print(f"  aktuell:  {final['switchingtimes']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - deliberately broad for a diagnostic script
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
