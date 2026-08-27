# Change log:
# - 2026-08-27: Initial test harness setup (Option C from project discussion:
#   verified real gateway payloads as fixtures + regression tests for the
#   crypto/signing/parsing layer, per CLAUDE.md "Next planned work").
"""Shared pytest fixtures and import path setup for this project's tests.

The `api/` layer (crypto, login, apiRequest, apiMethods, credentials,
scene_manager) has no Home Assistant dependency by design (see CLAUDE.md,
"Integration Architecture"), so these tests import it directly without
requiring the full pytest-homeassistant-custom-component test harness.
Only tests that touch HA-specific code (climate.py, coordinator.py,
config_flow.py) would need that heavier harness - none currently do.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CUSTOM_COMPONENTS_DIR = REPO_ROOT / "custom_components"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# Makes `import honeywell_smileconnect.api...` work directly, mirroring the
# real runtime import path (`custom_components.honeywell_smileconnect...`)
# minus the custom_components prefix, without needing HA installed.
if str(CUSTOM_COMPONENTS_DIR) not in sys.path:
    sys.path.insert(0, str(CUSTOM_COMPONENTS_DIR))


def load_fixture(name: str) -> dict:
    """Load a verified real-gateway JSON fixture from tests/fixtures/."""
    with open(FIXTURES_DIR / name, encoding="utf-8") as f:
        return json.load(f)
