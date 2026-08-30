# Change log:
# - 2026-08-27: Initial version, analogous to manual_check_weather.py.
#   Created because tests/fixtures/ping_response.json is currently
#   transcribed from the user's pre-existing PDF documentation rather than
#   live-captured in a chat session - this script closes that gap.
"""Manual diagnostic: fetch the real /api/ping response.

Run this directly in the dev container terminal:

    python3 scripts/manual_check_ping.py

Unlike manual_check_weather.py, this does NOT need a username/password -
/api/ping is deliberately unauthenticated (see api/ping.py's module
docstring and CLAUDE.md "Known API Endpoints").

This is a one-off manual tool, not part of the automated test suite or the
integration itself - it exists to confirm/refresh
tests/fixtures/ping_response.json against a live gateway.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "custom_components"))

from honeywell_smileconnect.api import ping as ping_api  # noqa: E402


def main() -> None:
    host = input("Gateway host/IP [192.168.1.132]: ").strip() or "192.168.1.132"
    base_url = f"http://{host}"

    print(f"\nCalling {base_url}/api/ping (no authentication needed) ...\n")
    result = ping_api.ping(base_url)

    print("=" * 60)
    print("RAW /api/ping RESPONSE:")
    print("=" * 60)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - deliberately broad for a diagnostic script
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
