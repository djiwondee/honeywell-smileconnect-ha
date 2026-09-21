# Change log:
# - 2026-09-21: Initial version, alongside the 0.3.1 session-recovery fix.
#   Locks in coordinator._async_update_data()'s retry CONTROL FLOW, which
#   is the heart of that fix and would otherwise have no automated test at
#   all: the repo has no Home Assistant test harness, and tests/ covers
#   only the HA-independent api/ layer.
"""Local regression test for the coordinator's session-recovery logic.

Unlike its siblings in scripts/, this needs **no gateway, no credentials
and no network** - and unlike tests/, it does not need a Home Assistant
test harness either. It constructs the coordinator without running
DataUpdateCoordinator.__init__ and substitutes async_login() /
_async_fetch_all(), so only the decision logic under test actually runs.

Same niche as scripts/test_scene_guards_local.py: a real regression test
for behaviour that cannot be covered by tests/, kept in scripts/ so it
stays outside lint.yml's scope. Run it any time:

    python3 scripts/test_session_recovery_local.py

What it pins down, and why each matters:

  * A healthy cycle never logs in again. Re-logging-in needlessly would
    start a fresh gateway session (and reset reqcount) on every hiccup.
  * A session expiry triggers EXACTLY ONE re-login and ONE retry.
  * A second expiry straight after a successful login does NOT loop -
    it becomes UpdateFailed. An infinite re-login loop against the
    gateway is the worst possible outcome here.
  * A non-session failure (SmileConnectApiError, or a plain transport
    error) never triggers a re-login at all. This targeted behaviour is
    the whole reason api_request.py raises a DISTINCT exception type for
    loginRejected - see api/exceptions.py.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from homeassistant.helpers.update_coordinator import UpdateFailed  # noqa: E402

from custom_components.honeywell_smileconnect.api.exceptions import (  # noqa: E402
    SmileConnectApiError,
    SmileConnectSessionExpired,
)
from custom_components.honeywell_smileconnect.coordinator import (  # noqa: E402
    SmileConnectCoordinator,
)

RESULTS: list[str] = []


def check(name: str, condition: bool) -> None:
    RESULTS.append(("PASS  " if condition else "FAIL  ") + name)


def _make_coordinator(fetch_results, login_error: Exception | None = None):
    """A coordinator with only the pieces _async_update_data() touches.

    object.__new__ skips DataUpdateCoordinator.__init__, which would need a
    real HomeAssistant instance. `fetch_results` is consumed one entry per
    _async_fetch_all() call (the last entry repeats); an Exception entry is
    raised instead of returned.
    """
    coordinator = object.__new__(SmileConnectCoordinator)
    coordinator.api = MagicMock()
    coordinator.login_count = 0
    coordinator.fetch_count = 0

    async def _login() -> None:
        coordinator.login_count += 1
        if login_error is not None:
            raise login_error

    async def _fetch():
        index = min(coordinator.fetch_count, len(fetch_results) - 1)
        coordinator.fetch_count += 1
        result = fetch_results[index]
        if isinstance(result, Exception):
            raise result
        return result

    coordinator.async_login = _login
    coordinator._async_fetch_all = _fetch
    return coordinator


def _run(coordinator):
    return asyncio.run(coordinator._async_update_data())


def test_healthy_cycle_does_not_log_in_again() -> None:
    coordinator = _make_coordinator([{"rooms": ["ok"]}])
    result = _run(coordinator)
    check(
        "healthy cycle: returns data, no re-login, one fetch",
        result == {"rooms": ["ok"]}
        and coordinator.login_count == 0
        and coordinator.fetch_count == 1,
    )


def test_initial_login_when_api_is_none() -> None:
    coordinator = _make_coordinator([{"rooms": []}])
    coordinator.api = None
    _run(coordinator)
    check("api is None: performs the initial login", coordinator.login_count == 1)


def test_session_expiry_recovers() -> None:
    coordinator = _make_coordinator(
        [SmileConnectSessionExpired("session gone"), {"rooms": ["ok"]}]
    )
    result = _run(coordinator)
    check(
        "session expired once: exactly one re-login, one retry, data returned",
        result == {"rooms": ["ok"]}
        and coordinator.login_count == 1
        and coordinator.fetch_count == 2,
    )


def test_repeated_expiry_does_not_loop() -> None:
    coordinator = _make_coordinator([SmileConnectSessionExpired("session gone")])
    try:
        _run(coordinator)
        check("session expired twice: raises UpdateFailed", False)
    except UpdateFailed as err:
        check(
            "session expired twice: raises UpdateFailed mentioning the re-login",
            "after re-login" in str(err),
        )
    check(
        "session expired twice: stops after ONE re-login (no loop)",
        coordinator.login_count == 1 and coordinator.fetch_count == 2,
    )


def test_api_error_does_not_trigger_relogin() -> None:
    coordinator = _make_coordinator([SmileConnectApiError("The input format is invalid: 14")])
    try:
        _run(coordinator)
        check("gateway API error: raises UpdateFailed", False)
    except UpdateFailed as err:
        check(
            "gateway API error: raises UpdateFailed keeping the gateway's message",
            "The input format is invalid: 14" in str(err),
        )
    check("gateway API error: never re-logs-in", coordinator.login_count == 0)


def test_transport_error_does_not_trigger_relogin() -> None:
    coordinator = _make_coordinator([OSError("Connection refused")])
    try:
        _run(coordinator)
        check("transport error: raises UpdateFailed", False)
    except UpdateFailed:
        check("transport error: raises UpdateFailed", True)
    check("transport error: never re-logs-in", coordinator.login_count == 0)


def test_failed_relogin_surfaces() -> None:
    coordinator = _make_coordinator(
        [SmileConnectSessionExpired("session gone")],
        login_error=ValueError("Login failed: The Verification has failed."),
    )
    try:
        _run(coordinator)
        check("re-login itself fails: raises UpdateFailed", False)
    except UpdateFailed as err:
        check(
            "re-login itself fails: raises UpdateFailed keeping the login error",
            "The Verification has failed." in str(err),
        )


def main() -> int:
    for test in (
        test_healthy_cycle_does_not_log_in_again,
        test_initial_login_when_api_is_none,
        test_session_expiry_recovers,
        test_repeated_expiry_does_not_loop,
        test_api_error_does_not_trigger_relogin,
        test_transport_error_does_not_trigger_relogin,
        test_failed_relogin_surfaces,
    ):
        test()

    print("\n".join(RESULTS))
    failures = [line for line in RESULTS if line.startswith("FAIL")]
    print(f"\n{len(RESULTS) - len(failures)}/{len(RESULTS)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
