"""Follow-up diagnostic to manual_check_standby_deactivation_timing.py.

That script showed roomstatus getting transiently stuck at 12 (Standby)
for 55+ seconds after Standby was confirmed deactivated via
/api/scene/status - a genuine gateway-side staleness, not a timing issue
a longer poll-timeout would fix.

This script tests the "nudge" hypothesis: does re-sending the room's
CURRENT desiredTemperature (unchanged - a logical no-op) force the
gateway to recompute roomstatus away from 12? And does that write even
get accepted, given roomstatus itself still says "Standby" at that point
(independent of what /api/scene/status says)?

Never touches the actual desired setpoint - reads it first, sends the
exact same value back.
"""
from __future__ import annotations

import getpass
import sys
import time

sys.path.insert(0, "custom_components/honeywell_smileconnect")

from api.api_methods import ApiMethods  # noqa: E402
from api.login import Login  # noqa: E402

POLL_INTERVAL_SECONDS = 1.0
POLL_DURATION_SECONDS = 30


def _prompt_nonempty(prompt: str) -> str:
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("  (leere Eingabe - bitte erneut versuchen)")


def _poll_roomstatus(api: ApiMethods, room_id: int, label: str, duration: float) -> tuple[int | None, float | None]:
    """Poll roomstatus for `duration` seconds, printing each reading.
    Returns (final_status, seconds_until_it_first_left_12) - the second
    value is None if it never left 12.
    """
    t0 = time.monotonic()
    last_status = None
    left_standby_at = None
    while time.monotonic() - t0 < duration:
        elapsed = time.monotonic() - t0
        room = api.get_specific_room(room_id)
        status = room["data"].get("roomstatus") if room else None
        marker = "  <-- ÄNDERUNG" if status != last_status else ""
        print(f"  [{label}] t={elapsed:6.2f}s  roomstatus={status!s:>4}{marker}")
        if status != 12 and left_standby_at is None:
            left_standby_at = elapsed
        last_status = status
        time.sleep(POLL_INTERVAL_SECONDS)
    return last_status, left_standby_at


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
    print("Login erfolgreich.\n")

    rooms = api.get_rooms_list()
    print("Verfügbare Räume:")
    for room in rooms:
        print(
            f"  id={room['data']['id']}  name={room['name']}  roomstatus={room['data'].get('roomstatus')}")
    room_id = int(_prompt_nonempty("\nRaum-ID für den Test wählen: "))

    scene = api.get_specific_scene("Standby")
    if not scene["isActive"]:
        print("\nStandby ist bereits inaktiv - bitte vorher wieder aktivieren")
        print("(über HA oder die App), damit wir den Übergang erneut sauber")
        print("beobachten können. Abbruch.")
        return

    room = api.get_specific_room(room_id)
    current_temp = room["data"].get("desiredTemperature")
    print(f"\nAktuelle desiredTemperature: {current_temp}")
    if current_temp is None:
        print("desiredTemperature nicht verfügbar - Nudge-Test nicht möglich. Abbruch.")
        return

    print(
        "\nWICHTIG: der letzte Test mit UNVERÄNDERTEM Wert hat nichts bewirkt "
        "- die Nudge-Antwort selbst zeigte bereits roomstatus:12, das Gateway "
        "hat also gar keine Neuberechnung angestoßen (vermutlich weil es den "
        "Schreibzugriff als No-Op erkannt hat, da der Wert identisch war)."
    )
    print(
        "Dieser Test sendet daher bewusst einen ANDEREN Wert - das ändert "
        "wirklich kurzzeitig die Solltemperatur des echten Heizsystems."
    )
    test_temp = float(
        _prompt_nonempty(
            f"\nTest-Temperatur eingeben (nicht {current_temp}, z.B. eine "
            "normale Wohlfühltemperatur wie 20): "
        )
    )
    if test_temp == current_temp:
        print("Das ist derselbe Wert wie aktuell - Abbruch, das würde nichts testen.")
        return

    input(
        f"\nDrücke Enter: Standby wird deaktiviert, wir warten kurz, dann "
        f"senden wir {test_temp} (statt {current_temp}) als Nudge und "
        "beobachten roomstatus danach ..."
    )

    duration = api.get_scene_duration("Standby")
    api.set_scene("Standby", active=False, duration=duration)
    print("\nStandby deaktiviert. Beobachte roomstatus VOR dem Nudge (10s)...")
    _, _ = _poll_roomstatus(api, room_id, "vor Nudge", duration=10)

    room = api.get_specific_room(room_id)
    status_before_nudge = room["data"].get("roomstatus")
    print(f"\nroomstatus unmittelbar vor dem Nudge: {status_before_nudge}")

    print(
        f"\nSende Nudge: set_temperature({test_temp}, room_id={room_id}) ...")
    nudge_response = api.set_temperature(test_temp, room_id)
    print(f"Antwort auf den Nudge-Call: {nudge_response}")

    print(
        f"\nBeobachte roomstatus NACH dem Nudge ({POLL_DURATION_SECONDS}s)...")
    final_status, left_at = _poll_roomstatus(
        api, room_id, "nach Nudge", duration=POLL_DURATION_SECONDS)

    print("\n" + "=" * 60)
    print(f"roomstatus vor dem Nudge:  {status_before_nudge}")
    print(f"roomstatus am Ende:        {final_status}")
    if left_at is not None:
        print(
            f"ERGEBNIS: Nudge wirkt - roomstatus verließ 12 nach {left_at:.1f}s.")
    else:
        print("ERGEBNIS: Nudge hat NICHT geholfen - roomstatus blieb bei 12.")
        print("Prüfe die Nudge-Antwort oben: falls sie auf einen Fehler/")
        print("Ablehnung hindeutet, wurde der Schreibzugriff selbst verworfen")
        print("(z.B. weil roomstatus zu dem Zeitpunkt noch Standby zeigte).")
    print("=" * 60)
    print("\nBitte diese komplette Ausgabe zurückspiegeln.")


if __name__ == "__main__":
    main()
