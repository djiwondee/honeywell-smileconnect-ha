# Makes `custom_components` importable as a regular Python package from the
# repo root (see pytest.ini's `pythonpath = .`), so tests can import via
# `custom_components.honeywell_smileconnect...` - the exact same path Home
# Assistant itself uses at runtime. This file has no effect on how Home
# Assistant loads the integration at runtime (HA uses its own component
# loader, not standard package resolution, to find custom_components/) -
# it exists purely for the test suite's benefit.
