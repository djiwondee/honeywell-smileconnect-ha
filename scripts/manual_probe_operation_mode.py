# Change log:
# - 2026-10-29: Initial version. Read-only probe to find out whether any
#   known gateway endpoint reports the heating controller's "operation
#   mode" / "heating mode" (the mixer's "Automatik" state, which the Smile
#   App cannot set). No endpoint is known to carry it yet, so this script
#   calls the known read-only endpoints and searches every response for
#   mode-related keys/values.
"""Manual diagnostic: look for "operation mode" / "heating mode" values.

Run this directly in the dev container terminal:

    python3 scripts/manual_probe_operation_mode.py

Optionally probe additional (read-only) endpoint paths, e.g. ones seen in
a mitmproxy capture of the Smile App:

    python3 scripts/manual_probe_operation_mode.py /api/some/endpoint

It prompts for host/username/password (password via getpass, never echoed
or stored), logs in with the same api/ layer the integration uses, calls a
fixed list of READ-ONLY endpoints (no write endpoint is ever called), and:

  1. prints every hit where a JSON key or string value looks mode-related
     (operation/heating/mode/mixer/betrieb/mischer/automat/summer/...), and
  2. prints each raw response so nothing is hidden by the filter.

Not part of the automated test suite (needs a live gateway and prompts for
input), same category as the other scripts/manual_*.py tools.
"""
from __future__ import annotations

import getpass
import json
import re
import sys
from pathlib import Path

# Make the HA-independent api/ layer importable without Home Assistant.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "custom_components"))

from honeywell_smileconnect.api.api_methods import ApiMethods  # noqa: E402
from honeywell_smileconnect.api.default_params import DefaultApiParams  # noqa: E402
from honeywell_smileconnect.api.login import Login  # noqa: E402

# Read-only endpoints known from the integration / CLAUDE.md.
KNOWN_READ_ENDPOINTS = [
    "/api/systemstate",
    "/initial/system/state",
    "/api/room/list",
    "/api/scene/status",
    "/api/weather",
    "/api/portal/access/data",
    "/api/user/datetime",
]

# Keys or string values matching this are reported as hits.
MODE_PATTERN = re.compile(
    r"operat|heating|heizung|mode|modus|mixer|mischer|betrieb|automat|summer|"
    r"winter|sommer|circuit|kreis",
    re.IGNORECASE,
)
# Envelope fields present in every response; never interesting.
IGNORED_KEYS = {"success", "message", "loginRejected", "language", "performance"}


def find_hits(node, path: str = "") -> list[tuple[str, object]]:
    """Recursively collect (json_path, value) pairs that look mode-related."""
    hits: list[tuple[str, object]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in IGNORED_KEYS and not path:
                continue
            child = f"{path}.{key}" if path else key
            if MODE_PATTERN.search(str(key)):
                hits.append((child, value))
            else:
                hits.extend(find_hits(value, child))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            hits.extend(find_hits(value, f"{path}[{index}]"))
    elif isinstance(node, str) and MODE_PATTERN.search(node):
        hits.append((path, node))
    return hits


def main() -> None:
    host = input("Gateway host/IP [192.168.1.132]: ").strip() or "192.168.1.132"
    username = input("Username: ").strip()
    password = getpass.getpass("Password (hidden): ")

    base_url = f"http://{host}"
    print(f"\nLogging in to {base_url} ...")
    credentials = Login(base_url).authorize(username, password)
    print("Login successful.\n")

    api = ApiMethods(credentials, base_url)
    paths = KNOWN_READ_ENDPOINTS + sys.argv[1:]

    responses: dict[str, object] = {}
    for path in paths:
        try:
            responses[path] = api._request.request(
                base_url + path, credentials, DefaultApiParams()
            )
        except Exception as err:  # noqa: BLE001 - diagnostic script, keep going
            responses[path] = f"<{type(err).__name__}: {err}>"

    print("=" * 60)
    print("MODE-RELATED HITS")
    print("=" * 60)
    any_hit = False
    for path, payload in responses.items():
        if isinstance(payload, str):
            continue
        for json_path, value in find_hits(payload):
            any_hit = True
            print(f"{path}  ->  {json_path} = {json.dumps(value, ensure_ascii=False)}")
    if not any_hit:
        print("(none - no known endpoint reports an operation/heating mode)")

    print("\n" + "=" * 60)
    print("RAW RESPONSES")
    print("=" * 60)
    for path, payload in responses.items():
        print(f"\n--- {path} ---")
        if isinstance(payload, str):
            print(payload)
        else:
            print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
