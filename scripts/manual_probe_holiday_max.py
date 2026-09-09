# Change log:
# - 2026-09-09: v1. Holiday's scene/set `duration` is confirmed to be raw
#   days, not a fraction (see api_methods.py change log) - but the actual
#   upper bound (scene_max equivalent) is still unknown, since scene/status
#   reports no min/max/step for Holiday, unlike Boost/Party/Leave. This
#   script finds it experimentally: send increasing raw day values and
#   watch get_scene_duration() for the point where the echoed value stops
#   tracking the sent value linearly (the clamp ceiling), mirroring how
#   Boost's clamp-at-1 was discovered.
"""Manual diagnostic: find Holiday's actual maximum duration in days.

Run this directly in the dev container terminal:

    python3 scripts/manual_probe_holiday_max.py

For each candidate value in CANDIDATE_DAYS:
    1. set_scene("Holiday", True, duration=<candidate>)
    2. get_scene_duration("Holiday")
    3. deactivate
    4. print sent vs. echoed

If echoed keeps matching sent (within a small tolerance) across all
candidates, the true ceiling is above the highest value tested - rerun
with a higher CANDIDATE_DAYS list. If echoed plateaus at some value while
sent keeps increasing, that plateau is the scene_max for Holiday.

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

SETTLE_SECONDS = 3
# Start around the old apiMethods.py's claimed "App-Default ist 30 Tage"
# and bracket it on both sides, so a plateau (if any) shows up clearly.
CANDIDATE_DAYS = [10, 20, 25, 30, 35, 40, 60, 100]


def probe(api: ApiMethods, sent_days: float) -> float | None:
    print(f"\n-- sending duration={sent_days} days --")
    set_response = api.set_scene("Holiday", True, duration=sent_days)
    if not set_response.get("success"):
        print(f"   set_scene did NOT report success: {set_response}")
        return None

    time.sleep(SETTLE_SECONDS)

    scene_status = api.get_specific_scene("Holiday")
    print(f"   isActive: {scene_status.get('isActive')}")

    echoed = api.get_scene_duration("Holiday")
    print(f"   get_scene_duration raw: {echoed!r}")

    deactivate = api.set_scene("Holiday", False, duration=0)
    print(f"   deactivated: success={deactivate.get('success')}")

    return echoed


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

    rooms = api.get_scene_rooms("Holiday")
    if not rooms:
        print("Holiday has no rooms assigned - assigning room 1 for this test.")
        api.set_scene_rooms("Holiday", [1])

    print(f"Will test candidate values: {CANDIDATE_DAYS}")
    confirm = input(
        "This will activate/deactivate Holiday repeatedly. "
        "Type 'yes' to proceed, anything else to abort: "
    ).strip().lower()
    if confirm != "yes":
        print("Aborted.")
        return

    results = []
    for sent in CANDIDATE_DAYS:
        echoed = probe(api, sent)
        results.append((sent, echoed))

    print("\n" + "=" * 60)
    print("SUMMARY (sent -> echoed):")
    print("=" * 60)
    prev_echoed = None
    plateau_at = None
    for sent, echoed in results:
        marker = ""
        if echoed is not None and prev_echoed is not None:
            # if echoed stops growing while sent keeps growing, that's the plateau
            if abs(echoed - prev_echoed) < 0.5 and plateau_at is None:
                plateau_at = prev_echoed
                marker = "  <-- plateau starts around here"
        print(f"  {sent:>6} -> {echoed!r}{marker}")
        if echoed is not None:
            prev_echoed = echoed

    if plateau_at is not None:
        print(
            f"\n=> Holiday's scene_max appears to be around {plateau_at:.1f} days.")
    else:
        print("\n=> No plateau detected within tested range - true ceiling is at or "
              f"above {CANDIDATE_DAYS[-1]} days. Rerun with higher CANDIDATE_DAYS.")

    print("\nBitte die komplette Ausgabe (SUMMARY-Tabelle) zurückspiegeln.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - deliberately broad for a diagnostic script
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
