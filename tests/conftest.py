# Change log:
# - 2026-08-27 (d): pytest.ini's `pythonpath` changed from `custom_components`
#   (which flattened the prefix away, importing as bare `honeywell_smileconnect...`)
#   to `.` (repo root), with a new empty `custom_components/__init__.py`
#   added. Tests now import via `custom_components.honeywell_smileconnect...`
#   - the exact same dotted path Home Assistant itself uses at runtime -
#   instead of a test-only shortcut. This fixed a persistent
#   `KeyError: 'honeywell_smileconnect'` / inconsistent `ModuleNotFoundError`
#   that survived even after fixing (c) below; root cause suspected to be
#   pytest-homeassistant-custom-component's own expectations about how the
#   `custom_components` namespace should resolve, which the flattened
#   shortcut conflicted with. If you add new test files, import via
#   `custom_components.honeywell_smileconnect.xxx`, not the bare
#   `honeywell_smileconnect.xxx` form.
# - 2026-08-27 (c): Removed the sys.path.insert block entirely - having it
#   run ALONGSIDE pytest.ini's `pythonpath` setting (rather than instead of
#   it, as intended when it was kept as a "redundant safety net") caused
#   duplicate/conflicting sys.path entries for the same directory. This
#   alone did not fully fix the issue - see (d) above for the actual fix.
# - 2026-08-27 (b): (superseded) sys.path.insert kept as a "redundant
#   safety net" alongside the new pytest.ini mechanism.
# - 2026-08-27 (a): Initial test harness setup (Option C from project
#   discussion: verified real gateway payloads as fixtures + regression
#   tests for the crypto/signing/parsing layer, per CLAUDE.md "Next planned
#   work").
"""Shared pytest fixtures and import path setup for this project's tests.

The `api/` layer (crypto, login, apiRequest, apiMethods, credentials,
scene_manager, ping) and `device.py` have no Home Assistant dependency by
design (see CLAUDE.md, "Integration Architecture"), so these tests import
them directly without requiring the full pytest-homeassistant-custom-
component test harness. Only tests that touch HA-specific code (climate.py,
sensor.py, binary_sensor.py, coordinator.py, ping_coordinator.py,
config_flow.py) would need that heavier harness - none currently do.

Import path setup itself lives entirely in pytest.ini (`pythonpath = .`)
plus `custom_components/__init__.py` - do NOT add a sys.path.insert here;
see change log above for the debugging story of why that broke things.
Test files import via `custom_components.honeywell_smileconnect.xxx`,
matching Home Assistant's own runtime import path exactly.
"""
from __future__ import annotations

import json
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> dict:
    """Load a verified real-gateway JSON fixture from tests/fixtures/."""
    with open(FIXTURES_DIR / name, encoding="utf-8") as f:
        return json.load(f)
