"""Manual diagnostic: does /api/room/list's roomstatus self-update after
deactivating Standby, or does it stay stuck until some other room write
(e.g. a temperature change) forces the gateway to recompute it?

Deliberately does NOT touch temperature at any point - that's the one
variable we need to rule out. Only calls set_scene(active=False) directly
(bypassing SceneManager's own scene/status-based verification, since we
specifically want to observe roomstatus, not isActive) and then polls
roomstatus in a tight loop.

Never stores credentials. Prompts interactively.
"""
from __future__ import annotations

import getpass
import sys
import time

sys.path.insert(0, "custom_components/honeywell_smileconnect")

from api.api_methods import ApiMethods  # noqa: E402
from api.login import Login  # noqa: E402

POLL_INTERVAL_SECONDS = 1.0
POLL_DURATION_SECONDS = 60


def _prompt_nonempty(prompt: str) -> str:
    """input() that refuses to accept an empty/whitespace-only value -
    prevents the confusing 'No host supplied' requests.InvalidURL deep
    inside login.py that resulted from an empty host string reaching
    "http://" + host unchecked.
    """
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("  (leere Eingabe - bitte erneut versuchen)")


def main() -> None:
    host = _prompt_nonempty("Gateway-IP (z.B. 192.168.1.132): ")
    # Tolerate someone pasting a full URL out of habit (e.g. from a
    # browser address bar) instead of just the bare host/IP.
    host = host.removeprefix("http://").removeprefix("https://").rstrip("/")
    username = _prompt_nonempty("Benutzername: ")
    password = getpass.getpass("Passwort: ")
    if not password:
        print("Leeres Passwort - Abbruch.")
        return

    print(f"\nVerbinde zu http://{host} ...")
    print("Login läuft ...")
    login_manager = Login("http://" + host)
    credentials = login_manager.authorize(username, password)
    api = ApiMethods(credentials, "http://" + host)
    print("Login erfolgreich.\n")

    rooms = api.get_rooms_list()
    print("Verfügbare Räume:")
    for room in rooms:
        print(
            f"  id={room['data']['id']}  name={room['name']}  roomstatus={room['data'].get('roomstatus')}")

    room_id = int(input("\nRaum-ID für den Test wählen: ").strip())

    scene = api.get_specific_scene("Standby")
    print(f"\nStandby aktuell aktiv: {scene['isActive']}")
    if not scene["isActive"]:
        print("Standby ist bereits inaktiv - bitte vorher über die App oder")
        print("die Integration einschalten, damit wir den Deaktivierungs-")
        print("Übergang beobachten können. Abbruch.")
        return

    input(
        "\nDrücke Enter, um Standby JETZT direkt per API zu deaktivieren "
        "(set_scene, ohne Verifikations-Wartezeit) und danach roomstatus "
        f"für bis zu {POLL_DURATION_SECONDS}s zu beobachten. Temperatur "
        "wird währenddessen NICHT angefasst ..."
    )

    duration = api.get_scene_duration("Standby")
    t0 = time.monotonic()
    api.set_scene("Standby", active=False, duration=duration)
    print(f"\nset_scene(active=False) abgeschickt bei t=0.00s. Beobachte roomstatus:\n")

    last_status = None
    changed_at = None
    while time.monotonic() - t0 < POLL_DURATION_SECONDS:
        elapsed = time.monotonic() - t0
        room = api.get_specific_room(room_id)
        status = room["data"].get("roomstatus") if room else None
        scene_active = api.get_specific_scene("Standby")["isActive"]
        marker = ""
        if status != last_status:
            marker = "  <-- ÄNDERUNG"
            if last_status is not None and changed_at is None:
                changed_at = elapsed
        print(
            f"  t={elapsed:6.2f}s  roomstatus={status!s:>4}  scene/status.isActive(Standby)={scene_active!s:>5}{marker}")
        last_status = status
        time.sleep(POLL_INTERVAL_SECONDS)

    print("\n" + "=" * 60)
    if changed_at is not None:
        print(
            f"ERGEBNIS: roomstatus hat sich nach {changed_at:.1f}s von selbst")
        print("aktualisiert, OHNE dass die Temperatur angefasst wurde.")
        print("-> Reines Timing-Problem; unser Verify-Timeout muss auf")
        print(
            f"   mindestens ~{changed_at + 3:.0f}s erhöht werden (und/oder direkt")
        print("   gegen roomstatus statt scene/status verifizieren).")
    else:
        print(
            f"ERGEBNIS: roomstatus hat sich innerhalb von {POLL_DURATION_SECONDS}s NICHT")
        print("von selbst aktualisiert, obwohl scene/status.isActive bereits")
        print("korrekt False zeigt.")
        print("-> Bestätigt die Hypothese: das Gateway braucht einen expliziten")
        print("   weiteren Schreibzugriff auf den Raum (z.B. eine Temperatur-")
        print("   Aktion), um roomstatus neu zu berechnen. Kein reines Timing-")
        print("   Problem - ein längerer Timeout würde hier NICHT helfen.")
    print("=" * 60)
    print("\nBitte diese komplette Ausgabe zurückspiegeln.")


if __name__ == "__main__":
    main()
