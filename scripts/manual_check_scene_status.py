# Change log:
# - 2026-08-27: Initial version. Read-only diagnostic, created after
#   manual_check_roomstatus.py showed roomstatus=12 for every tested scene
#   and the user reported the real scene set includes "Economy" (hours),
#   not "Leave" as assumed from the generic HeatApp reference project.
#   This script exists to get the REAL scene names/duration units before
#   any further scene-activation experiments, rather than continuing to
#   guess. Not part of the automated test suite - lives in scripts/, not
#   tests/.
"""Manual diagnostic: dump the real /api/scene/status response as-is.

Run this directly in the dev container terminal:

    python3 scripts/manual_check_scene_status.py

Completely read-only - does NOT activate, deactivate, or modify any scene.
Safe to run any time, as often as you like.

Shows exactly what the gateway calls its scenes (name), their real
min/max/step (duration units/ranges), and whether each is currently
active - the ground truth to correct any assumptions carried over from
the generic HeatApp reference project (e.g. "Leave" vs "Economy").
"""
from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "custom_components"))

from honeywell_smileconnect.api.apiMethods import ApiMethods  # noqa: E402
from honeywell_smileconnect.api.login import Login  # noqa: E402


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

    print("Calling /api/scene/status (read-only) ...\n")
    scene_status = api.get_scene_status()

    print("=" * 60)
    print("RAW /api/scene/status RESPONSE:")
    print("=" * 60)
    print(json.dumps(scene_status, indent=2, ensure_ascii=False))
    print("=" * 60)

    scenes = scene_status.get("scenes", [])
    if scenes:
        print("\nParsed scene summary:")
        print(f"{'name':12s} {'min':>6s} {'max':>6s} {'step':>6s} {'isActive':>10s}")
        for scene in scenes:
            print(
                f"{str(scene.get('name')):12s} "
                f"{str(scene.get('min')):>6s} "
                f"{str(scene.get('max')):>6s} "
                f"{str(scene.get('step')):>6s} "
                f"{str(scene.get('isActive')):>10s}"
            )

    print("\nAlso showing the top-level isX flags from the response (if present):")
    for key in ("isParty", "isBoost", "isHoliday", "isShower", "isLeave", "isStandby"):
        if key in scene_status:
            print(f"  {key}: {scene_status[key]}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - diagnostic script
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
