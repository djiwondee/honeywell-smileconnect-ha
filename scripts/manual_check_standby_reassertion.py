# Change log:
# - 2026-09-01: Initial version. Follow-up to manual_probe_roomstatus_
#   compound.py, which showed that when Standby and a preset (Leave/Boost/
#   Party) are BOTH active, roomstatus reports only the preset's code -
#   Standby's own isActive flag stays True in the background the entire
#   time, completely invisible via roomstatus alone. scene_manager.py's
#   remove_member_from_scene() (called by climate.py's
#   async_set_preset_mode() whenever a preset is switched away from or
#   turned off) never touches Standby at all - it only deactivates the
#   preset scene being removed. Hypothesis this script tests: does the
#   room silently fall back to roomstatus=12 (Standby) once the preset is
#   removed, because Standby's flag was quietly left active the whole
#   time and nothing ever explicitly cleared it? If so, that's a
#   previously undiscovered bug independent of the Holiday-specific
#   firmware quirk and the duration-encoding bug - it would affect
#   Leave/Boost/Party too, any time a user activates a preset while
#   Standby happens to already be on.
#
#   Deliberately uses the REAL SceneManager class for both the Standby
#   activation step (safe - ApiMethods.set_scene() hardcodes duration=1
#   for Standby regardless of the duration-encoding bug affecting
#   presets) and the preset REMOVAL step (also safe - deactivation calls
#   have not shown any duration-sensitivity in testing so far, unlike
#   activation). Only the preset ACTIVATION step bypasses SceneManager
#   (mirroring manual_check_preset_nudge.py's approach), since
#   SceneManager.add_member_to_scene() would hit the known duration=0
#   bug for an inactive preset - this lets the test reach a realistic
#   "Standby + preset both active" starting state without needing that
#   bug fixed first.
"""Manual diagnostic: does a room silently fall back to roomstatus=12
(Standby) after a preset is removed via the REAL production
SceneManager.remove_member_from_scene() path, because Standby's isActive
flag was left quietly active in the background the whole time?

Run directly in the dev container terminal:

    python3 scripts/manual_check_standby_reassertion.py

Steps:
  1. Activate Standby for the chosen room via the real SceneManager.
  2. Activate a preset (you choose; Leave recommended - already verified
     clean) with an explicit, correct duration value (bypassing
     SceneManager for this step only - see change log).
  3. Confirm roomstatus reflects the preset, and that Standby.isActive is
     still True in the background (matching manual_probe_roomstatus_
     compound.py's finding).
  4. Remove the preset via the REAL SceneManager.remove_member_from_
     scene() - the exact call climate.py's async_set_preset_mode() makes.
  5. Poll roomstatus for up to 60s afterward: does it fall back to 12
     (Standby reasserting itself) or resolve to 11 (plain schedule-
     following baseline)?

Never stores credentials.
"""
from __future__ import annotations

import getpass
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "custom_components"))

from honeywell_smileconnect.api.api_methods import ApiMethods  # noqa: E402
from honeywell_smileconnect.api.login import Login  # noqa: E402
from honeywell_smileconnect.api.scene_manager import SceneManager  # noqa: E402
from honeywell_smileconnect.const import (  # noqa: E402
    ROOM_STATUS_BOOST,
    ROOM_STATUS_LEAVE,
    ROOM_STATUS_PARTY,
    ROOM_STATUS_STANDBY,
)

POLL_INTERVAL_SECONDS = 1.0
POLL_DURATION_SECONDS = 60

# Deliberately excludes Holiday - already confirmed separately broken
# (roomstatus never resolves to it while Standby is simultaneously
# active, a distinct gateway firmware quirk under investigation via
# manual_check_preset_nudge.py's Standby-pre-deactivation option). This
# script is scoped to the presets that DO activate cleanly, to isolate
# the removal-path question from that already-understood issue.
TESTABLE_PRESETS: dict[str, dict] = {
    "Leave": {"unit": "Stunden", "send_value": 2.0, "roomstatus": ROOM_STATUS_LEAVE},  # sends real 6h (x3 factor)
    "Boost": {"unit": "Minuten", "send_value": 60.0, "roomstatus": ROOM_STATUS_BOOST},  # factor unknown - raw guess
    "Party": {"unit": "Stunden", "send_value": 6.0, "roomstatus": ROOM_STATUS_PARTY},  # factor unknown - raw guess
}

ALL_SCENE_NAMES = ["Standby", "Leave", "Holiday", "Party", "Boost"]


def _prompt_nonempty(prompt: str) -> str:
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("  (leere Eingabe - bitte erneut versuchen)")


def snapshot_all_scenes(api: ApiMethods) -> dict[str, bool]:
    return {name: api.get_specific_scene(name)["isActive"] for name in ALL_SCENE_NAMES}


def print_scene_snapshot(label: str, snapshot: dict[str, bool]) -> None:
    parts = "  ".join(f"{name}={active!s}" for name, active in snapshot.items())
    print(f"  [{label}] {parts}")


def poll_roomstatus(api: ApiMethods, room_id, label: str, duration: float) -> tuple[int | None, dict[int, float]]:
    """Poll roomstatus, printing each reading. Returns (final_status,
    {status_value: first_seen_at_seconds}) so callers can check when a
    particular value (e.g. ROOM_STATUS_STANDBY) first appeared.
    """
    t0 = time.monotonic()
    last_status = None
    first_seen: dict[int, float] = {}
    while time.monotonic() - t0 < duration:
        elapsed = time.monotonic() - t0
        room = api.get_specific_room(room_id)
        status = room["data"].get("roomstatus") if room else None
        marker = "  <-- ÄNDERUNG" if status != last_status else ""
        if status is not None and status not in first_seen:
            first_seen[status] = elapsed
        print(f"  [{label}] t={elapsed:6.2f}s  roomstatus={status!s:>4}{marker}")
        last_status = status
        time.sleep(POLL_INTERVAL_SECONDS)
    return last_status, first_seen


def main() -> None:
    host = _prompt_nonempty("Gateway-IP (z.B. 192.168.1.132): ")
    host = host.removeprefix("http://").removeprefix("https://").rstrip("/")
    username = _prompt_nonempty("Benutzername: ")
    password = getpass.getpass("Passwort: ")
    if not password:
        print("Leeres Passwort - Abbruch.")
        return

    print(f"\nVerbinde zu http://{host} ...")
    login_manager = Login("http://" + host)
    credentials = login_manager.authorize(username, password)
    api = ApiMethods(credentials, "http://" + host)
    scene_manager = SceneManager(api)
    print("Login erfolgreich.")

    rooms = api.get_rooms_list()
    print("\nVerfügbare Räume:")
    for room in rooms:
        print(f"  id={room['data']['id']}  name={room['name']}  roomstatus={room['data'].get('roomstatus')}")
    room_id = int(_prompt_nonempty("\nRaum-ID wählen: "))

    print("\nWelches Preset soll getestet werden?")
    print("  (Holiday ist absichtlich ausgeschlossen - separat bekannter Firmware-Quirk)")
    print("  EMPFEHLUNG: 'Leave' - einziges Preset mit bereits bestätigtem duration-Faktor")
    print("  (x3). Für Boost/Party ist der Faktor noch UNBEKANNT - die hier gesendeten")
    print("  Rohwerte könnten real länger laufen als angezeigt (siehe TESTABLE_PRESETS).")
    for i, name in enumerate(TESTABLE_PRESETS):
        info = TESTABLE_PRESETS[name]
        print(f"  [{i}] {name}  (sendet duration={info['send_value']} {info['unit']})")
    idx = int(_prompt_nonempty(f"Auswahl [0-{len(TESTABLE_PRESETS) - 1}]: "))
    scene_name = list(TESTABLE_PRESETS)[idx]
    info = TESTABLE_PRESETS[scene_name]

    print(f"\n{'=' * 70}")
    print("SCHRITT 1: Standby aktivieren (über die echte SceneManager-Methode)")
    print(f"{'=' * 70}")
    input("Drücke Enter um fortzufahren ...")
    scene_manager.add_member_to_scene(room_id, "Standby")
    room = api.get_specific_room(room_id)
    print(f"roomstatus nach Standby-Aktivierung: {room['data'].get('roomstatus')}")
    print_scene_snapshot("Snapshot nach Standby-Aktivierung", snapshot_all_scenes(api))

    print(f"\n{'=' * 70}")
    print(f"SCHRITT 2: '{scene_name}' aktivieren (direkt per ApiMethods, mit explizitem duration-Wert)")
    print(f"{'=' * 70}")
    rooms_in_scene = api.get_scene_rooms(scene_name)
    if room_id not in rooms_in_scene:
        rooms_in_scene.append(room_id)
        api.set_scene_rooms(scene_name, rooms_in_scene)
    input(f"Drücke Enter: aktiviere '{scene_name}' mit duration={info['send_value']} ...")
    response = api.set_scene(scene_name, active=True, duration=info["send_value"])
    print(f"Antwort: {response}")

    print(f"\nBeobachte roomstatus bis zum erwarteten Wert ({info['roomstatus']}), max. 30s:")
    final_status, first_seen = poll_roomstatus(api, room_id, "nach Aktivierung", 30)
    print_scene_snapshot("Snapshot nach Preset-Aktivierung", snapshot_all_scenes(api))
    if final_status != info["roomstatus"]:
        print(
            f"\nWARNUNG: roomstatus hat den erwarteten Wert ({info['roomstatus']}) nicht "
            f"erreicht (zuletzt: {final_status}) - Abbruch, Schritt 3/4 würden auf einer "
            "falschen Ausgangsbasis testen."
        )
        return

    print(f"\n{'=' * 70}")
    print(f"SCHRITT 3 (DER KERNTEST): '{scene_name}' entfernen über die ECHTE")
    print("scene_manager.remove_member_from_scene() - exakt der Aufruf, den")
    print("climate.py's async_set_preset_mode() beim Abwählen eines Presets macht.")
    print(f"{'=' * 70}")
    input("Drücke Enter um fortzufahren ...")
    scene_manager.remove_member_from_scene(room_id, scene_name)
    room = api.get_specific_room(room_id)
    print(f"roomstatus sofort nach remove_member_from_scene(): {room['data'].get('roomstatus')}")
    print_scene_snapshot("Snapshot sofort nach Entfernen", snapshot_all_scenes(api))

    print(f"\nBeobachte roomstatus für {POLL_DURATION_SECONDS}s nach dem Entfernen:\n")
    final_status, first_seen = poll_roomstatus(api, room_id, "nach Entfernen", POLL_DURATION_SECONDS)
    print_scene_snapshot("Snapshot am Ende der Beobachtung", snapshot_all_scenes(api))

    print("\n" + "=" * 70)
    print("ERGEBNIS")
    print("=" * 70)
    if final_status == ROOM_STATUS_STANDBY:
        print(
            f"roomstatus ist auf {ROOM_STATUS_STANDBY} (Standby) zurückgefallen, "
            f"obwohl Standby nie explizit erneut angefordert wurde - "
            "BESTÄTIGT: Standby reasserted sich im Hintergrund, sobald das Preset "
            "entfernt wird. Das ist ein eigenständiger Bug in remove_member_from_scene() "
            "(bzw. im Zusammenspiel mit climate.py), unabhängig vom Holiday-Firmware-Quirk."
        )
    elif final_status == 11:
        print(
            "roomstatus ist auf 11 (reines Schaltzeit-Following) gefallen - Standby "
            "wurde NICHT reasserted, obwohl sein isActive-Flag laut Snapshot ggf. noch "
            "True war. Kein Bug bestätigt - roomstatus scheint sich unabhängig von "
            "Standbys Flag korrekt aufzulösen."
        )
    else:
        print(f"Unerwarteter Endzustand: roomstatus={final_status}. Bitte manuell einordnen.")
    print("=" * 70)

    print(
        f"\nHinweis: Raum {room_id} wurde von diesem Skript verändert (Standby + "
        f"'{scene_name}' aktiviert, dann '{scene_name}' wieder entfernt) - bitte den "
        "Endzustand bei Bedarf über die App/HA auf den gewünschten Normalzustand setzen."
    )
    print("\nBitte die komplette Ausgabe dieses Laufs zurückspiegeln.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    except Exception as exc:  # noqa: BLE001
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
