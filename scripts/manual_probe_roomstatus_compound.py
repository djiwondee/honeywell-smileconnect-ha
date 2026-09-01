# Change log:
# - 2026-09-01 (b): Fixed a bug in read_state(): duration was read from
#   /api/scene/status's per-scene dict (scene.get("duration")), which
#   does not carry that field at all - always printed None regardless of
#   what was actually configured in the App, even for scenes with a
#   clearly non-default duration set by the user. Duration is only
#   available via the separate /api/scene/duration endpoint
#   (ApiMethods.get_scene_duration()), same as manual_check_preset_
#   nudge.py already uses correctly. Fixed by calling get_scene_duration()
#   per scene name instead of trusting the scene/status response to carry it.
# - 2026-09-01 (a): Initial version. Follow-up to manual_probe_roomstatus_
#   via_app.py (2026-08-27), which established the currently-used
#   ROOM_STATUS_* constants (Standby=12, Boost=6, Party=3, Holiday=7,
#   Leave=10) - but only ever tested each scene ALONE via the Smile App,
#   never Standby ON simultaneously WITH a preset ON (the exact
#   combination our own write-path testing in manual_check_preset_nudge.py
#   just hit: Holiday+Standby both active leaves roomstatus stuck at 12,
#   while Leave+Standby both active correctly resolves to 10 - an
#   asymmetry with no explanation yet).
#
#   User hypothesis this script is built to test: is roomstatus actually
#   a BITFIELD (Standby on/off as one bit, which-preset-if-any encoded in
#   other bits) rather than a flat per-state integer? The known codes
#   don't decompose into a clean single-bit-per-mode pattern at face
#   value (12=1100, 6=0110, 3=0011, 7=0111, 10=1010, 11=1011), but that
#   was never actually tested against the one compound case that would
#   prove or disprove it: what does roomstatus read as when Standby AND a
#   preset are BOTH on at the same time, set via the trusted Smile App
#   (bypassing our own write path and its recently-discovered duration
#   bugs entirely, for a clean signal)?
#
#   100% READ-ONLY on our side - only calls get_rooms_list() and
#   get_scene_status() (both GET-shaped). All scene changes are done BY
#   YOU via the Smile App for the room in question.
"""Manual diagnostic: probe roomstatus for compound scene states (Standby
ON simultaneously with each preset ON), set via the Smile App itself - not
via our own write path - to test whether roomstatus is a flat per-state
code or a bitfield encoding Standby + active preset independently.

Run directly in the dev container terminal:

    python3 scripts/manual_probe_roomstatus_compound.py

For each scenario in SCENARIOS, you set that exact combination via the
Smile App for the chosen room, press Enter, and the script reads/logs:
  - roomstatus (decimal AND binary, for visual bit-pattern inspection)
  - scene/status isActive for ALL FIVE scenes (Standby + 4 presets)
  - scene/status duration for all five scenes (info only, not the focus
    here - see manual_check_preset_nudge.py for the duration-factor
    investigation)

Ends with a summary table across all logged scenarios.
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "custom_components"))

from honeywell_smileconnect.api.api_methods import ApiMethods  # noqa: E402
from honeywell_smileconnect.api.login import Login  # noqa: E402

ALL_SCENE_NAMES = ["Standby", "Leave", "Holiday", "Party", "Boost"]

# Each entry: (label, instructions for what to set via the App).
# Deliberately includes both the "alone" cases (reconfirms the existing
# ROOM_STATUS_* constants on this same run, for a consistent baseline)
# AND the never-before-tested-via-App compound cases (Standby + a preset
# together) - the latter is the actual point of this script.
SCENARIOS: list[tuple[str, str]] = [
    ("Baseline (alles aus)", "Standby AUS, kein Preset aktiv (reines Schaltzeit-Following)"),
    ("Standby allein", "NUR Standby AN, alles andere aus"),
    ("Leave allein", "NUR Leave AN, Standby AUS, kein anderes Preset"),
    ("Holiday allein", "NUR Holiday AN, Standby AUS, kein anderes Preset"),
    ("Boost allein", "NUR Boost AN, Standby AUS, kein anderes Preset"),
    ("Party allein", "NUR Party AN, Standby AUS, kein anderes Preset"),
    ("Standby + Leave", "Standby AN UND Leave AN (beide gleichzeitig)"),
    ("Standby + Holiday", "Standby AN UND Holiday AN (beide gleichzeitig)"),
    ("Standby + Boost", "Standby AN UND Boost AN (beide gleichzeitig)"),
    ("Standby + Party", "Standby AN UND Party AN (beide gleichzeitig)"),
]


def _to_binary(value: int | None, bits: int = 8) -> str:
    if value is None:
        return "?" * bits
    return format(value, f"0{bits}b")


def read_state(api: ApiMethods, room_id) -> dict:
    rooms = api.get_rooms_list()
    room = next((r for r in rooms if r["data"]["id"] == room_id), None)
    roomstatus = room["data"].get("roomstatus") if room else None

    scene_status = api.get_scene_status()
    scenes_by_name = {s["name"]: s for s in scene_status.get("scenes", [])}
    scenes: dict[str, dict] = {}
    for name in ALL_SCENE_NAMES:
        scene = scenes_by_name.get(name, {})
        scenes[name] = {
            "isActive": scene.get("isActive"),
            # duration is NOT part of /api/scene/status's scene objects -
            # it requires the separate /api/scene/duration endpoint (see
            # change log 2026-09-01 (b)).
            "duration": api.get_scene_duration(name),
        }

    return {"roomstatus": roomstatus, "scenes": scenes}


def print_state(state: dict) -> None:
    roomstatus = state["roomstatus"]
    print(f"  roomstatus = {roomstatus!s:>4}  (binär: {_to_binary(roomstatus)})")
    for name, info in state["scenes"].items():
        print(f"    {name:8s} isActive={info['isActive']!s:5s}  duration={info['duration']!s}")


def main() -> None:
    host = input("Gateway host/IP [192.168.1.132]: ").strip() or "192.168.1.132"
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
        print("No rooms found - nothing to probe.")
        return

    print("Rooms found:")
    for i, room in enumerate(rooms):
        print(f"  [{i}] {room['name']} (id={room['data']['id']})")
    room_idx = 0 if len(rooms) == 1 else int(input(f"Which room will you be changing modes for? [0-{len(rooms) - 1}]: ").strip())
    room_id = rooms[room_idx]["data"]["id"]
    room_name = rooms[room_idx]["name"]
    print(f"\nProbing against room: {room_name} (id={room_id})\n")

    results: dict[str, dict] = {}

    for label, instructions in SCENARIOS:
        print(f"\n{'=' * 70}")
        print(f"Szenario: {label}")
        print(f"Bitte JETZT in der Smile App für Raum '{room_name}' einstellen:")
        print(f"  {instructions}")
        print(f"{'=' * 70}")
        answer = input("Enter drücken sobald eingestellt, oder 's' zum Überspringen: ").strip().lower()
        if answer == "s":
            print(f"Übersprungen: {label}")
            results[label] = {"roomstatus": "SKIPPED", "scenes": {}}
            continue

        state = read_state(api, room_id)
        print_state(state)
        results[label] = state

    print(f"\n{'=' * 70}")
    print("ZUSAMMENFASSUNG")
    print(f"{'=' * 70}")
    print(f"  {'Szenario':22s} {'roomstatus':>10s}  {'binär':>10s}")
    for label, _ in SCENARIOS:
        state = results.get(label, {"roomstatus": "NOT TESTED"})
        rs = state["roomstatus"]
        binary = _to_binary(rs) if isinstance(rs, int) else "-"
        print(f"  {label:22s} {rs!s:>10s}  {binary:>10s}")
    print(f"{'=' * 70}")
    print(
        "\nAuswertungshinweis: falls roomstatus ein Bitfeld ist, sollte z.B. "
        "'Standby + Holiday' ein anderes, gemeinsames Bitmuster zeigen als "
        "'Standby allein' UND 'Holiday allein' einzeln - und die Standby-Bits "
        "sollten in JEDER 'Standby + X'-Zeile konsistent gesetzt sein. Falls "
        "stattdessen 'Standby + X' immer identisch zu 'Standby allein' aussieht, "
        "gewinnt Standby einfach als Priorität und es ist kein Bitfeld."
    )
    print("\nDieses Skript hat nichts verändert - bitte den Raum bei Bedarf über")
    print("die Smile App wieder auf den gewünschten Normalzustand zurücksetzen.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    except Exception as exc:  # noqa: BLE001
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
