# Change log:
# - 2026-08-27: Initial version. One-off manual diagnostic script to
#   capture the real /api/weather response from a live Honeywell Smile
#   Connect gateway, since no verified fixture exists for this endpoint yet
#   (only the generic, unverified HeatApp reference fixture). See
#   CLAUDE.md "Test Suite" section for the pattern this feeds into: capture
#   real response -> add as tests/fixtures/*.json -> write a test against it.
"""Manual diagnostic: fetch the real /api/weather response.

Run this directly in the dev container terminal:

    python3 scripts/manual_check_weather.py

It prompts for the gateway host/username/password (password via getpass,
never echoed or stored), logs in using the same api/ layer the integration
itself uses, calls /api/weather, and pretty-prints the raw JSON response.

This is a one-off manual tool, not part of the automated test suite or the
integration itself - it exists purely to capture a real response so it can
be turned into a verified fixture (see tests/fixtures/room_list_response.json
for the pattern already followed for /api/room/list).
"""
from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path

# Make the HA-independent api/ layer importable without needing Home
# Assistant installed - mirrors tests/conftest.py's approach.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "custom_components"))

from honeywell_smileconnect.api.apiMethods import ApiMethods  # noqa: E402
from honeywell_smileconnect.api.login import Login  # noqa: E402


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

    print("Calling /api/weather ...\n")
    weather = api.get_weather()

    print("=" * 60)
    print("RAW /api/weather RESPONSE:")
    print("=" * 60)
    print(json.dumps(weather, indent=2, ensure_ascii=False))
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - deliberately broad for a diagnostic script
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
