# Change log:
# - 2026-09-09: v2. Extends the local guard tests (v1: empty room_ids,
#   room-assignment guard) with cases for SCENE_APP_LIMITS validation,
#   confirmed directly by the user 2026-09-09:
#     Boost:   30-120 minutes, raster 30/60/90/120
#     Party:   1-12 hours
#     Leave:   1-12 hours
#     Holiday: 1-30 days
#   Tests both the low-level _validate_target_against_app_limits()
#   function directly and set_scene(..., target=...) end-to-end (with
#   get_scene_rooms mocked so the room-assignment guard doesn't trigger
#   first). Also confirms duration= (the raw wire value) deliberately
#   bypasses this validation, as documented in set_scene()'s docstring.
"""Local-only test for set_scene()/set_scene_rooms() guard clauses and
SCENE_APP_LIMITS validation.

Run this directly, no gateway or credentials needed:

    python3 scripts/test_scene_guards_local.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "custom_components"))

from honeywell_smileconnect.api.api_methods import (  # noqa: E402
    ApiMethods,
    _validate_target_against_app_limits,
)


def make_api() -> ApiMethods:
    """ApiMethods with dummy credentials - fine since no request is ever
    actually sent in this test (guards raise before any network call)."""
    dummy_credentials = MagicMock()
    return ApiMethods(dummy_credentials, "http://unused.invalid")


# -- existing guard tests (v1) -----------------------------------------

def test_set_scene_rooms_rejects_empty_list() -> bool:
    print("\n-- test: set_scene_rooms(scene, []) raises ValueError --")
    api = make_api()
    with patch.object(api, "_request") as mock_request:
        try:
            api.set_scene_rooms("Boost", [])
            print("   FAIL: no exception raised")
            return False
        except ValueError as exc:
            print(f"   OK: ValueError raised: {exc}")
        if mock_request.request.called:
            print("   FAIL: a request was sent despite the empty list - "
                  "this is the exact bug that caused the gateway hang!")
            return False
        print("   OK: no request was sent")
        return True


def test_set_scene_raises_without_rooms() -> bool:
    print("\n-- test: set_scene(scene, active=True) raises ValueError when scene has no rooms --")
    api = make_api()
    with patch.object(api, "get_scene_rooms", return_value=[]) as mock_get_rooms:
        with patch.object(api, "_request") as mock_request:
            try:
                api.set_scene("Boost", True, target=30)
                print("   FAIL: no exception raised")
                return False
            except ValueError as exc:
                print(f"   OK: ValueError raised: {exc}")
            if mock_request.request.called:
                print("   FAIL: a request was sent despite no rooms assigned")
                return False
            print("   OK: no request was sent")
            mock_get_rooms.assert_called_once_with("Boost")
            return True


def test_set_scene_succeeds_with_rooms() -> bool:
    print("\n-- test: set_scene(scene, active=True) proceeds normally when rooms ARE assigned --")
    api = make_api()
    with patch.object(api, "get_scene_rooms", return_value=[1]):
        with patch.object(api, "_request") as mock_request:
            mock_request.request.return_value = {"success": True}
            result = api.set_scene("Boost", True, target=30)
            if not mock_request.request.called:
                print("   FAIL: no request was sent despite rooms being assigned")
                return False
            print(f"   OK: request was sent, result: {result}")
            return True


# -- new: SCENE_APP_LIMITS tests (v2) -----------------------------------

def test_validate_target_directly() -> bool:
    print("\n-- test: _validate_target_against_app_limits() low-level cases --")
    cases = [
        # (scene, target, should_raise, label)
        ("Boost", 25, True, "Boost off-raster (not a multiple of 30)"),
        ("Boost", 30, False, "Boost on-raster minimum"),
        ("Boost", 60, False, "Boost on-raster mid-value"),
        ("Boost", 120, False, "Boost on-raster maximum"),
        ("Boost", 0, True, "Boost below min (0 not selectable in app)"),
        ("Boost", 150, True, "Boost above max"),
        ("Party", 0, True, "Party below min (0 not selectable in app)"),
        ("Party", 1, False, "Party at min"),
        ("Party", 12, False, "Party at max"),
        ("Party", 13, True, "Party above max"),
        ("Leave", 0, True, "Leave below min (0 not selectable in app)"),
        ("Leave", 6, False, "Leave mid-range"),
        ("Holiday", 0, True, "Holiday below min (0 not selectable in app)"),
        ("Holiday", 1, False, "Holiday at min"),
        ("Holiday", 30, False, "Holiday at max"),
        ("Holiday", 40, True, "Holiday above max (gateway itself does NOT clamp this)"),
        ("Holiday", 100, True, "Holiday far above max"),
    ]

    all_ok = True
    for scene, target, should_raise, label in cases:
        try:
            _validate_target_against_app_limits(scene, target)
            raised = False
        except ValueError:
            raised = True

        ok = raised == should_raise
        status = "OK" if ok else "FAIL"
        expectation = "raises" if should_raise else "accepts"
        actual = "raised" if raised else "accepted"
        print(
            f"   {status}: {label} (target={target}) - expected {expectation}, got {actual}")
        all_ok = all_ok and ok

    return all_ok


def test_set_scene_target_enforces_limits() -> bool:
    print("\n-- test: set_scene(..., target=...) rejects out-of-range values end-to-end --")
    api = make_api()
    with patch.object(api, "get_scene_rooms", return_value=[1]):
        with patch.object(api, "_request") as mock_request:
            mock_request.request.return_value = {"success": True}

            all_ok = True

            # Boost off-raster - should raise, no request sent
            try:
                api.set_scene("Boost", True, target=25)
                print("   FAIL: Boost target=25 (off-raster) did not raise")
                all_ok = False
            except ValueError as exc:
                print(f"   OK: Boost target=25 raised: {exc}")
            if mock_request.request.called:
                print("   FAIL: a request was sent for Boost target=25")
                all_ok = False
            mock_request.reset_mock()

            # Holiday above app max - should raise, no request sent
            try:
                api.set_scene("Holiday", True, target=40)
                print("   FAIL: Holiday target=40 (above app max) did not raise")
                all_ok = False
            except ValueError as exc:
                print(f"   OK: Holiday target=40 raised: {exc}")
            if mock_request.request.called:
                print("   FAIL: a request was sent for Holiday target=40")
                all_ok = False
            mock_request.reset_mock()

            # Holiday within app max - should succeed
            result = api.set_scene("Holiday", True, target=30)
            if not mock_request.request.called:
                print("   FAIL: Holiday target=30 (at app max) did not send a request")
                all_ok = False
            else:
                print(
                    f"   OK: Holiday target=30 sent a request, result: {result}")

            return all_ok


def test_duration_bypasses_app_limits() -> bool:
    print("\n-- test: duration= (raw wire value) deliberately bypasses SCENE_APP_LIMITS --")
    api = make_api()
    with patch.object(api, "get_scene_rooms", return_value=[1]):
        with patch.object(api, "_request") as mock_request:
            mock_request.request.return_value = {"success": True}
            # 999 raw days for Holiday would fail app-limit validation if
            # it went through target=, but duration= must skip that check
            # entirely - it's the documented power-user escape hatch.
            result = api.set_scene("Holiday", True, duration=999)
            if not mock_request.request.called:
                print("   FAIL: duration=999 did not send a request - "
                      "it should bypass SCENE_APP_LIMITS entirely")
                return False
            print(
                f"   OK: duration=999 sent a request unchecked, result: {result}")
            return True


def main() -> None:
    results = [
        test_set_scene_rooms_rejects_empty_list(),
        test_set_scene_raises_without_rooms(),
        test_set_scene_succeeds_with_rooms(),
        test_validate_target_directly(),
        test_set_scene_target_enforces_limits(),
        test_duration_bypasses_app_limits(),
    ]
    print("\n" + "=" * 60)
    if all(results):
        print(f"ALL {len(results)} TESTS PASSED")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} TESTS FAILED")
        sys.exit(1)
    print("=" * 60)


if __name__ == "__main__":
    main()
