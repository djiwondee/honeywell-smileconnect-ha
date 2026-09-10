# Change log:
# - 2026-09-09: v1. Follow-up to
#   scripts/manual_probe_leave_duration_contradiction.py, which confirmed
#   Leave uses a DIFFERENT write formula than Party/Boost (real_hours =
#   sent_value x 3, not sent_value x scene_max) - matching the original
#   docs/protocol.md §4d table, contradicting this session's earlier
#   assumption that Leave shares Party's fraction-of-scene_max formula.
#   This script: (1) confirms the x3 factor across more data points for
#   confidence, and (2) tests values ABOVE the previously-tested
#   boundary (sent=4 -> 12h) to determine whether the gateway hard-caps
#   real duration at 12h (matching the app's own Leave limit) or whether
#   it keeps scaling linearly past the app's visible range.
"""Manual diagnostic: characterize Leave's duration=sent*3 write formula
and find its actual ceiling.

Run this directly in the dev container terminal:

    python3 scripts/manual_probe_leave_formula_ceiling.py

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

LEAVE_SCENE_MAX_HOURS = 12  # read-side scale, confirmed correct in the previous script
SETTLE_SECONDS = 3
# 1, 3 re-confirm the x3 factor with different numbers than 2/4 already
# tested. 5, 6, 8 go past the previously-tested "boundary" of 4 to see
# whether the gateway caps at 12h or keeps scaling (5x3=15h, 6x3=18h,
# 8x3=24h if uncapped).
CANDIDATE_SENT_VALUES = [1, 3, 5, 6, 8]


def probe(api: ApiMethods, sent_value: float) -> float | None:
    print(f"\n-- sending duration={sent_value} --")
    set_response = api.set_scene("Leave", True, duration=sent_value)
    if not set_response.get("success"):
        print(f"   set_scene did NOT report success: {set_response}")
        return None

    time.sleep(SETTLE_SECONDS)

    scene_status = api.get_specific_scene("Leave")
    print(f"   isActive: {scene_status.get('isActive')}")

    raw = api.get_scene_duration("Leave")
    implied_hours = raw * LEAVE_SCENE_MAX_HOURS
    predicted_if_x3 = sent_value * 3
    print(f"   get_scene_duration raw: {raw!r} -> implied {implied_hours:.2f}h "
          f"(x3 formula predicts {predicted_if_x3}h)")

    deactivate = api.set_scene("Leave", False, duration=0)
    print(f"   deactivated: success={deactivate.get('success')}")

    return implied_hours


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
    room_id = rooms[0]["data"]["id"]

    if not api.get_scene_rooms("Leave"):
        api.set_scene_rooms("Leave", [room_id])

    print(f"Will test sent values: {CANDIDATE_SENT_VALUES} (x3 formula predicts "
          f"{[v * 3 for v in CANDIDATE_SENT_VALUES]}h respectively)")
    confirm = input(
        "This will activate/deactivate Leave repeatedly. "
        "Type 'yes' to proceed, anything else to abort: "
    ).strip().lower()
    if confirm != "yes":
        print("Aborted.")
        return

    results = []
    for sent in CANDIDATE_SENT_VALUES:
        implied = probe(api, sent)
        results.append((sent, implied))

    print("\n" + "=" * 60)
    print("SUMMARY (sent -> implied hours, x3-formula prediction)")
    print("=" * 60)
    plateau_at = None
    prev_implied = None
    for sent, implied in results:
        predicted = sent * 3
        marker = ""
        if implied is not None:
            if abs(implied - predicted) < 0.5:
                marker = "  (matches x3)"
            elif abs(implied - LEAVE_SCENE_MAX_HOURS) < 0.5:
                marker = "  (capped at 12h, does NOT match x3)"
                if plateau_at is None:
                    plateau_at = sent
            prev_implied = implied
        print(f"  {sent:>3} -> {implied!r}h, predicted {predicted}h{marker}")

    print()
    if plateau_at is not None:
        print(f"=> Leave's real ceiling is 12h (app's own limit) - values "
              f"predicting more than 12h under x3 get capped starting "
              f"around sent={plateau_at}. The x3 formula only holds BELOW "
              f"the cap.")
    else:
        print("=> No capping observed within tested range - x3 formula may "
              "hold even past the app's visible 1-12h range, or the cap is "
              "higher than tested here. Consider testing higher sent "
              "values if this matters for validation logic.")

    print("\nBitte die komplette Ausgabe (SUMMARY-Tabelle) zurückspiegeln.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - deliberately broad for a diagnostic script
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
