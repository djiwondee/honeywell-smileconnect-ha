# Change log:
# - 2026-09-01 (e): A fourth Holiday data point (sent 0.5 -> observed 15
#   days, exactly the intended default) REFUTED the previously-recorded
#   10x factor: 3->30 and 1.5->30 (both giving the same output for
#   different inputs) turned out to be two DIFFERENT inputs both
#   saturating a 30-day CAP, not evidence of x10 linearity - pure
#   coincidence that 3*10 also happens to equal 30. The real factor is
#   30x (0.5*30=15, exactly matching, well below the cap; 1.5*30=45 and
#   3*30=90 both exceed the 30-day cap and get clamped to 30, matching
#   those three earlier observations too). All four Holiday data points
#   now fit this single model exactly. Updated known_send_factor to 30
#   and added a "factor_confidence" field per preset (confirmed vs.
#   tentative/single-data-point) so the Phase 2 prompt is honest about
#   how much to trust each suggested value - added tentative factors for
#   Party (4x, from 3->12) and Boost (6x, from 20->120) so their
#   suggested send-values now also account for the discovered factor
#   instead of suggesting the raw (wrong) expected_default.
# - 2026-09-01 (d): Two more live runs (Leave with duration=2, Holiday
#   with duration=3 again) revealed the actual shape of the duration bug -
#   it's a per-scene MULTIPLICATIVE factor, not a random/broken value:
#     Leave:   sent 2 -> regler shows 6h;  sent 4 -> regler shows 12h.
#              Consistent x3 factor across two distinct inputs - confirmed
#              linear. To hit the real 6h default, we must SEND 2, not 6.
#     Holiday: sent 3 -> regler shows 30d (seen twice, same input both
#              times - only one distinct data point, so linearity is
#              assumed by analogy with Leave, not yet independently
#              confirmed). Tentative x10 factor recorded as
#              "known_send_factor" per PRESET_INFO entry; Party/Boost
#              still completely unknown (never tested with a non-zero
#              value) - left as None, with an explicit "factor unknown"
#              warning shown in the Phase 2 prompt instead of silently
#              guessing 1x.
#   Also: the Leave run's snapshot showed Standby=True the ENTIRE time
#   (never deactivated), yet roomstatus still correctly reached 10
#   immediately - proving Standby-stays-active-alongside-a-preset is NOT
#   inherently a problem. Holiday's snapshot shows the exact same
#   Standby=True+preset=True combination, but roomstatus never recomputes
#   - so the roomstatus-staleness bug is Holiday-specific, not a general
#   "Standby must be off first" rule. Added an explicit prompt to
#   deactivate Standby before activating the target preset, to test the
#   hypothesis that the Smile App does this internally for Holiday
#   specifically (would also explain the "Leave/Holiday replace Standby"
#   assumption in docs/protocol.md, which was based on Smile-App-driven
#   testing, not our own raw API write path). Also fixed the Phase 2
#   default suggestion to account for the confirmed x3 Leave factor
#   (previously suggested sending the raw expected_default, which would
#   have actually produced 18h instead of the intended 6h).
# - 2026-09-01 (c): Added two more diagnostic steps after live runs with
#   real (non-zero) duration values revealed TWO separate, previously
#   unknown problems:
#   1. The duration value actually configured on the physical regler does
#      NOT match what was sent: Leave (sent 4 hours) -> regler showed 12
#      hours; Holiday (sent 3 days) -> regler showed 30 days. The scale
#      factor differs per scene (x3 vs x10), so this is NOT a single
#      simple unit-conversion bug we can just "fix" by guessing a
#      multiplier - guessing wrong here is dangerous (e.g. Holiday could
#      end up configured for far longer than intended, disabling heating
#      for an unintended stretch). Added a get_scene_duration() readback
#      immediately after activation (while the scene is now genuinely
#      active) to capture the gateway's own stored raw value for
#      correlation against both the value we sent and the value read off
#      the physical regler/App - without this, we'd be modifying
#      production code based on a guess.
#   2. Unlike Leave (which reached its expected roomstatus in 1.2s),
#      Holiday's roomstatus stayed stuck at 12 (Standby) for the full test
#      + nudge, despite scene/status.isActive(Holiday) already reporting
#      True throughout. Added an all-scenes snapshot (particularly
#      Standby's own isActive) before and after activation, to check
#      whether Standby is unexpectedly remaining active in parallel
#      instead of being replaced by Holiday, as CLAUDE.md/docs/protocol.md
#      previously assumed based on Leave/Holiday testing via the Smile App
#      (never specifically re-verified via our own write path with a
#      non-zero duration until now).
# - 2026-09-01 (b): Added a manual duration override prompt in Phase 2.
#   Live run against the real gateway (Leave, room 1) showed
#   get_scene_duration() returning 0 for an inactive scene, and
#   set_scene(active=True, duration=0) was silently rejected by the
#   gateway - scene/status.isActive(Leave) stayed False from t=0.00s
#   onward, never flipping True at all, and roomstatus never left 12
#   (Standby) even after the Phase 3 nudge. This is a DIFFERENT failure
#   mode than the Standby-leaving bug (there, scene/status was correct
#   immediately and only roomstatus lagged) - here the activation itself
#   never took hold, exactly matching the Phase 1 warning. Confirmed root
#   cause: get_scene_duration() is not a usable source for "the duration
#   to activate with" while a scene is inactive (returns 0, or for
#   Holiday an implausible ~0.0124-day fractional value, not the
#   documented 15-day default) - the integration must send its own
#   sensible default instead of blindly resending that read. Before
#   touching climate.py/scene_manager.py with that fix, this script now
#   lets a real, non-zero duration be entered by hand in Phase 2, so the
#   hypothesis can be confirmed against the gateway (does
#   scene/status.isActive flip True immediately, does roomstatus reach
#   the expected code) before any production code changes.
# - 2026-09-01 (a): Initial version. Investigates whether Boost/Party/Leave/
#   Holiday presets suffer from the same gateway-side roomstatus staleness
#   bug that was found and fixed for leaving Standby (see climate.py's
#   change log, 2026-08-31) - but on the ACTIVATION path instead of the
#   deactivation path, since presets are turned ON via HA's preset_mode
#   selector, not turned off the way Standby's hvac_mode toggle is.
#   User-reported symptom in production: selecting a preset (e.g. Leave)
#   in the climate entity appears to show briefly, then reverts on the
#   next poll - the gateway apparently never actually enabled it.
#
#   Added a PRE-STEP (Phase 1) before the roomstatus-timing test, per user
#   request: each preset scene also carries a `duration` parameter
#   (Leave=hours, Holiday=days, Party=hours, Boost=minutes -
#   ApiMethods.set_scene()'s own docstring already documents this).
#   scene_manager.add_member_to_scene() already resends whatever
#   get_scene_duration() currently reports for that scene - but that value
#   has NEVER been observed live while the scene is INACTIVE. If the
#   gateway returns a stale/zero/invalid value for an inactive scene, then
#   set_scene(active=True, duration=<bad value>) could plausibly be
#   SILENTLY REJECTED by the gateway (a `success:true` response that does
#   nothing - the exact bug pattern already hit four times in
#   api_request.py, see CLAUDE.md) - which alone would fully explain the
#   observed "activates briefly, then reverts" symptom, with no need for a
#   roomstatus-recompute nudge at all. Phase 1 checks this BEFORE Phase 2/3
#   spend time on the recompute-timing hypothesis.
"""Manual diagnostic for the preset (Boost/Party/Leave/Holiday) staleness
symptom reported against the real gateway.

Run directly in the dev container terminal:

    python3 scripts/manual_check_preset_nudge.py

Three phases:

  Phase 1 - Duration precheck (read-only for scenes found inactive):
            for each of the four presets, read isActive + the
            get_scene_duration() value BEFORE any activation is attempted.
            A missing/zero/obviously-wrong value while inactive would
            point at a silently-rejected activation write, not a
            roomstatus-recompute timing issue.

  Phase 2 - Activation timing: YOU choose one preset + a room, then enter
            (or accept the documented default for) a duration value to
            activate with - Phase 1 may have shown get_scene_duration()
            returning 0/implausible values while inactive, which is not
            safe to resend. The script then activates directly via
            ApiMethods (bypassing SceneManager's own verification wait,
            so the RAW gateway timing is visible) and polls roomstatus +
            scene/status for up to 60s, watching whether roomstatus ever
            reaches the expected code on its own.

  Phase 3 - Nudge test: only offered if Phase 2 shows roomstatus stuck.
            Sends a deliberately CHANGED desiredTemperature (never the
            same value - a same-value resend was already proven to be a
            no-op for the Standby case) and re-polls, mirroring
            manual_check_standby_nudge.py.

Never stores credentials. Never touches temperature without an explicit,
separately-confirmed value from you in Phase 3.
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
from honeywell_smileconnect.const import (  # noqa: E402
    ROOM_STATUS_BOOST,
    ROOM_STATUS_HOLIDAY,
    ROOM_STATUS_LEAVE,
    ROOM_STATUS_PARTY,
)

POLL_INTERVAL_SECONDS = 1.0
ACTIVATION_POLL_DURATION_SECONDS = 60
NUDGE_POLL_DURATION_SECONDS = 30

# Order matches how the user described the defaults in project discussion
# (not the SceneName enum's declaration order) - kept this way so the
# printed table is easy to cross-check against that description.
PRESET_INFO: dict[str, dict] = {
    # known_send_factor: empirically observed real_value = sent_value *
    # factor (see change log 2026-09-01 (d)). None = never tested with a
    # non-zero value yet - do NOT assume 1x, it has been wrong for both
    # scenes tested so far (3x for Leave, tentatively 10x for Holiday).
    "Leave": {
        "unit": "Stunden", "expected_default": 6, "roomstatus": ROOM_STATUS_LEAVE,
        "known_send_factor": 3, "factor_confidence": "confirmed",  # 2->6, 4->12
    },
    "Holiday": {
        "unit": "Tage", "expected_default": 15, "roomstatus": ROOM_STATUS_HOLIDAY,
        # NOT 10x as first assumed - that was a coincidence caused by a cap at
        # 30 days (3->30 and 1.5->30 both saturated the cap). Real factor is
        # 30x, confirmed by 0.5->15 (below the cap, matches exactly) - see
        # change log 2026-09-01 (e).
        "known_send_factor": 30, "factor_confidence": "confirmed (cap at 30 Tage beachten!)",
    },
    "Party": {
        "unit": "Stunden", "expected_default": 6, "roomstatus": ROOM_STATUS_PARTY,
        "known_send_factor": 4, "factor_confidence": "tentative - nur 1 Datenpunkt (3->12)",
    },
    "Boost": {
        "unit": "Minuten", "expected_default": 60, "roomstatus": ROOM_STATUS_BOOST,
        "known_send_factor": 6, "factor_confidence": "tentative - nur 1 Datenpunkt (20->120)",
    },
}

# Included alongside the four presets in the all-scenes snapshot (see
# snapshot_all_scenes()) specifically to catch Standby unexpectedly
# remaining active in parallel with a preset that should have replaced
# it - see change log 2026-09-01 (c) for why this was added (Holiday's
# roomstatus stayed stuck at 12/Standby despite scene/status.isActive
# (Holiday) already being True).
ALL_SCENE_NAMES = ["Standby", *PRESET_INFO.keys()]


def _prompt_nonempty(prompt: str) -> str:
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("  (leere Eingabe - bitte erneut versuchen)")


def check_durations_while_inactive(api: ApiMethods) -> dict[str, dict]:
    """Phase 1: read isActive + duration for all four presets, before any
    activation is attempted. Purely read-only (get_specific_scene and
    get_scene_duration are both GET-shaped, no side effects).
    """
    print(f"\n{'=' * 70}")
    print("PHASE 1: duration-Werte bei (hoffentlich) inaktiven Presets lesen")
    print(f"{'=' * 70}")

    results: dict[str, dict] = {}
    for scene_name, info in PRESET_INFO.items():
        scene = api.get_specific_scene(scene_name)
        is_active = scene["isActive"]
        duration = api.get_scene_duration(scene_name)
        results[scene_name] = {"duration": duration, "isActive": is_active}

        warning = ""
        if is_active:
            warning = "  <-- WARNUNG: Szene ist gerade AKTIV, Wert evtl. Restlaufzeit statt Default!"
        elif duration in (None, 0, "0"):
            warning = "  <-- WARNUNG: leer/0 bei inaktiver Szene - würde eine Aktivierung vermutlich zum No-Op machen!"

        print(
            f"  {scene_name:8s} isActive={is_active!s:5s}  duration={duration!s:>6} {info['unit']:8s}"
            f"  (erwarteter Default lt. Projektbeschreibung: {info['expected_default']}){warning}"
        )

    print(f"{'=' * 70}")
    any_bad = any(
        not r["isActive"] and r["duration"] in (None, 0, "0") for r in results.values()
    )
    if any_bad:
        print(
            "-> Mindestens ein Preset liefert bei inaktiver Szene einen leeren/0-"
            "Wert. Das allein könnte die beobachtete Symptomatik (Preset springt "
            "zurück) bereits vollständig erklären - Phase 2/3 sind dann nur noch "
            "zur Bestätigung/Ausschluss der Roomstatus-Recompute-Theorie relevant."
        )
    else:
        print(
            "-> Alle inaktiven Presets liefern einen plausiblen duration-Wert. "
            "Die Ursache liegt vermutlich tatsächlich im Roomstatus-Recompute-"
            "Verhalten des Gateways (wie beim Standby-Bug) - weiter mit Phase 2."
        )
    return results


def snapshot_all_scenes(api: ApiMethods) -> dict[str, bool]:
    """Read isActive for Standby + all four presets in one go. Read-only
    (get_specific_scene is GET-shaped, no side effects).
    """
    return {name: api.get_specific_scene(name)["isActive"] for name in ALL_SCENE_NAMES}


def print_scene_snapshot(label: str, snapshot: dict[str, bool]) -> None:
    parts = "  ".join(f"{name}={active!s}" for name, active in snapshot.items())
    print(f"  [{label}] {parts}")


def _poll_roomstatus(
    api: ApiMethods, room_id, scene_name: str, expected_status: int, label: str, duration: float
) -> tuple[int | None, float | None]:
    """Poll roomstatus for `duration` seconds, printing each reading
    alongside scene/status.isActive for scene_name. Returns
    (final_status, seconds_until_it_first_reached expected_status) - the
    second value is None if it never reached the expected code.
    """
    t0 = time.monotonic()
    last_status = None
    reached_at = None
    while time.monotonic() - t0 < duration:
        elapsed = time.monotonic() - t0
        room = api.get_specific_room(room_id)
        status = room["data"].get("roomstatus") if room else None
        scene_active = api.get_specific_scene(scene_name)["isActive"]
        marker = "  <-- ÄNDERUNG" if status != last_status else ""
        if status == expected_status and reached_at is None:
            reached_at = elapsed
            marker += "  <-- ERWARTETER WERT ERREICHT"
        print(
            f"  [{label}] t={elapsed:6.2f}s  roomstatus={status!s:>4} "
            f"(erwartet: {expected_status})  scene/status.isActive({scene_name})={scene_active!s:>5}{marker}"
        )
        last_status = status
        time.sleep(POLL_INTERVAL_SECONDS)
    return last_status, reached_at


def check_activation_timing(api: ApiMethods, room_id, scene_name: str, duration_value) -> bool:
    """Phase 2: activate scene_name for room_id directly via ApiMethods
    (bypassing SceneManager's own wait, so the raw gateway timing is
    visible) and poll roomstatus. Returns True if roomstatus reached the
    expected code within the poll window on its own (no nudge needed).
    """
    expected_status = PRESET_INFO[scene_name]["roomstatus"]

    print(f"\n{'=' * 70}")
    print(f"PHASE 2: Aktivierungs-Timing für '{scene_name}' (Raum-ID {room_id})")
    print(f"{'=' * 70}")

    rooms = api.get_scene_rooms(scene_name)
    if room_id not in rooms:
        rooms.append(room_id)
        print(f"Raum {room_id} ist noch kein Mitglied von '{scene_name}' - füge hinzu ...")
        api.set_scene_rooms(scene_name, rooms)

    scene = api.get_specific_scene(scene_name)
    if scene["isActive"]:
        answer = input(
            f"\n'{scene_name}' ist aktuell schon aktiv - für einen sauberen "
            "Aktivierungstest jetzt deaktivieren? [Y/n]: "
        ).strip().lower()
        if answer in ("", "y", "j"):
            api.set_scene(scene_name, active=False, duration=duration_value)
            print("Deaktiviert. Warte kurz, damit sich der Ausgangszustand setzt ...")
            time.sleep(5)
        else:
            print("Abbruch - Phase 2 setzt einen sauberen inaktiven Ausgangszustand voraus.")
            return False

    room = api.get_specific_room(room_id)
    print(f"\nroomstatus vor Aktivierung: {room['data'].get('roomstatus')}")
    snapshot_before = snapshot_all_scenes(api)
    print_scene_snapshot("Szenen-Snapshot vor Aktivierung", snapshot_before)

    if snapshot_before.get("Standby"):
        # Leave's live run showed Standby=True the whole time causing no
        # problem at all (roomstatus still resolved instantly) - but
        # Holiday's run showed the identical Standby=True+preset=True
        # combination with roomstatus permanently stuck. Testing whether
        # explicitly deactivating Standby first (mirroring what the Smile
        # App plausibly does internally) fixes Holiday specifically - see
        # change log 2026-09-01 (d).
        deactivate = input(
            "\nStandby ist aktuell aktiv. Vorher explizit deaktivieren, um zu "
            "testen, ob das (wie vermutlich in der Smile App) für ein "
            "korrektes roomstatus-Ergebnis nötig ist? [y/N]: "
        ).strip().lower()
        if deactivate in ("y", "j"):
            print("Deaktiviere Standby zuerst ...")
            api.set_scene("Standby", active=False, duration=1)
            time.sleep(3)
            room = api.get_specific_room(room_id)
            print(f"roomstatus nach Standby-Deaktivierung: {room['data'].get('roomstatus')}")
            print_scene_snapshot("Szenen-Snapshot nach Standby-Deaktivierung", snapshot_all_scenes(api))

    input(
        f"\nDrücke Enter: '{scene_name}' wird JETZT direkt per API aktiviert "
        f"(duration={duration_value}, ohne SceneManager-Verifikations-Wartezeit) "
        f"und roomstatus für bis zu {ACTIVATION_POLL_DURATION_SECONDS}s beobachtet ..."
    )

    t0 = time.monotonic()
    response = api.set_scene(scene_name, active=True, duration=duration_value)
    print(f"set_scene(active=True) abgeschickt bei t=0.00s. Antwort: {response}")

    # Immediately re-read duration WHILE the scene is now genuinely active -
    # correlating this raw value with what we sent (duration_value) and
    # with the human-readable value shown on the physical regler/App is
    # what actually reveals the real unit/scale, instead of guessing. See
    # change log 2026-09-01 (c).
    active_duration = api.get_scene_duration(scene_name)
    print(f"get_scene_duration('{scene_name}') sofort nach Aktivierung (jetzt aktiv): {active_duration}")
    print_scene_snapshot("Szenen-Snapshot nach Aktivierung", snapshot_all_scenes(api))
    regler_value = input(
        f"\nBitte JETZT in der Smile App oder am physischen Regler nachsehen: welche "
        f"Dauer ist für '{scene_name}' tatsächlich konfiguriert? (Wert eingeben, oder "
        "Enter zum Überspringen): "
    ).strip()
    if regler_value:
        unit = PRESET_INFO[scene_name]["unit"]
        print(
            f"-> Vergleich: gesendet={duration_value}  "
            f"get_scene_duration()-Rückgabewert={active_duration}  "
            f"am Regler/App abgelesen={regler_value} {unit}"
        )

    print("\nBeobachte roomstatus:\n")

    final_status, reached_at = _poll_roomstatus(
        api, room_id, scene_name, expected_status, "nach Aktivierung", ACTIVATION_POLL_DURATION_SECONDS
    )
    elapsed_total = time.monotonic() - t0
    print_scene_snapshot("Szenen-Snapshot am Ende der Beobachtung", snapshot_all_scenes(api))

    print("\n" + "=" * 70)
    if reached_at is not None:
        print(
            f"ERGEBNIS: roomstatus hat den erwarteten Wert ({expected_status}) "
            f"nach {reached_at:.1f}s von selbst erreicht - kein Nudge nötig."
        )
        print("=" * 70)
        return True

    print(
        f"ERGEBNIS: roomstatus hat den erwarteten Wert ({expected_status}) "
        f"innerhalb von {elapsed_total:.1f}s NICHT von selbst erreicht "
        f"(zuletzt gesehen: {final_status})."
    )
    print(
        "-> Bestätigt vermutlich dasselbe Verhalten wie beim Standby-Bug: "
        "das Gateway braucht einen zusätzlichen, tatsächlich geänderten "
        "Schreibzugriff auf den Raum, um roomstatus neu zu berechnen."
    )
    print("=" * 70)
    return False


def check_nudge(api: ApiMethods, room_id, scene_name: str) -> None:
    """Phase 3: only called if Phase 2 showed roomstatus stuck. Sends a
    deliberately different desiredTemperature and re-polls, mirroring
    manual_check_standby_nudge.py's approach (a same-value resend was
    already proven to be treated as a no-op there).
    """
    expected_status = PRESET_INFO[scene_name]["roomstatus"]

    print(f"\n{'=' * 70}")
    print(f"PHASE 3: Nudge-Test für '{scene_name}' (Raum-ID {room_id})")
    print(f"{'=' * 70}")

    room = api.get_specific_room(room_id)
    current_temp = room["data"].get("desiredTemperature")
    print(f"\nAktuelle desiredTemperature: {current_temp}")
    if current_temp is None:
        print("desiredTemperature nicht verfügbar - Nudge-Test nicht möglich. Abbruch.")
        return

    print(
        "\nWICHTIG: ein Resend desselben Werts hat sich beim Standby-Bug als "
        "wirkungslos erwiesen (Gateway erkennt No-Op). Dieser Test sendet daher "
        "bewusst einen ANDEREN Wert - das ändert wirklich kurzzeitig die "
        "Solltemperatur des echten Heizsystems."
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

    input(f"\nDrücke Enter: sende set_temperature({test_temp}, room_id={room_id}) als Nudge ...")
    nudge_response = api.set_temperature(test_temp, room_id)
    print(f"Antwort auf den Nudge-Call: {nudge_response}")

    print(f"\nBeobachte roomstatus NACH dem Nudge ({NUDGE_POLL_DURATION_SECONDS}s)...")
    final_status, reached_at = _poll_roomstatus(
        api, room_id, scene_name, expected_status, "nach Nudge", NUDGE_POLL_DURATION_SECONDS
    )
    print_scene_snapshot("Szenen-Snapshot nach Nudge", snapshot_all_scenes(api))

    print("\n" + "=" * 70)
    if reached_at is not None:
        print(f"ERGEBNIS: Nudge wirkt - roomstatus erreichte {expected_status} nach {reached_at:.1f}s.")
    else:
        print(f"ERGEBNIS: Nudge hat NICHT geholfen - roomstatus blieb bei {final_status}.")
    print("=" * 70)


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
    print("Login erfolgreich.")

    duration_results = check_durations_while_inactive(api)

    rooms = api.get_rooms_list()
    print("\nVerfügbare Räume:")
    for room in rooms:
        print(f"  id={room['data']['id']}  name={room['name']}  roomstatus={room['data'].get('roomstatus')}")
    room_id = int(_prompt_nonempty("\nRaum-ID für den Aktivierungstest wählen: "))

    print("\nWelches Preset soll aktiviert/getestet werden?")
    print("  ACHTUNG: 'Holiday' hat einen mehrtägigen Default (Heizung ggf. tagelang")
    print("  aus/reduziert!) - für den ersten Test wird 'Boost' empfohlen (kürzester,")
    print("  am wenigsten störender Default von ~60 Minuten).")
    for i, name in enumerate(PRESET_INFO):
        print(f"  [{i}] {name}")
    idx = int(_prompt_nonempty(f"Auswahl [0-{len(PRESET_INFO) - 1}]: "))
    scene_name = list(PRESET_INFO)[idx]

    duration_value = duration_results[scene_name]["duration"]
    if duration_value in (None, 0, "0"):
        print(
            f"\nWARNUNG: duration für '{scene_name}' war in Phase 1 leer/0 - "
            "vermutlich der Grund, warum die Aktivierung fehlschlägt (siehe "
            "Skript-Änderungslog, live bereits einmal so bestätigt)."
        )

    expected_default = PRESET_INFO[scene_name]["expected_default"]
    unit = PRESET_INFO[scene_name]["unit"]
    known_factor = PRESET_INFO[scene_name]["known_send_factor"]
    if known_factor:
        suggested_send = expected_default / known_factor
        confidence = PRESET_INFO[scene_name]["factor_confidence"]
        factor_note = (
            f" (Faktor {known_factor}x [{confidence}]: senden von {suggested_send} "
            f"sollte real {expected_default} {unit} ergeben)"
        )
    else:
        suggested_send = expected_default
        factor_note = (
            " (Faktor für dieses Preset noch UNBEKANNT - roher Zielwert, "
            "die tatsächlich konfigurierte Dauer kann davon abweichen!)"
        )
    override = input(
        f"\nDuration-Wert für die Aktivierung eingeben (Einheit: {unit}; "
        f"Enter für {suggested_send}{factor_note}; von Phase 1 gelesen: {duration_value}): "
    ).strip()
    duration_value = float(override) if override else float(suggested_send)
    print(f"-> verwende duration={duration_value} {unit} für die Aktivierung.")

    reached_on_its_own = check_activation_timing(api, room_id, scene_name, duration_value)

    if not reached_on_its_own:
        answer = input("\nNudge-Test (Phase 3) jetzt durchführen? [Y/n]: ").strip().lower()
        if answer in ("", "y", "j"):
            check_nudge(api, room_id, scene_name)

    print(
        f"\nHinweis: '{scene_name}' wurde von diesem Skript für Raum {room_id} "
        "aktiviert und NICHT automatisch zurückgesetzt - bitte bei Bedarf über "
        "die App oder HA wieder deaktivieren."
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
