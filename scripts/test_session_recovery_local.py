# Change log:
# - 2026-09-27: Added coverage for the reauth-flow feature
#   (coordinator.py's async_login() now translates a credentials-rejected
#   ValueError into ConfigEntryAuthFailed). test_failed_relogin_surfaces
#   renamed/split: one case injects ConfigEntryAuthFailed directly (the
#   mocked async_login() never runs the real translation, so this tests
#   only _async_update_data()'s propagation) and asserts it comes out
#   UNCHANGED, not downgraded to UpdateFailed; a second new case confirms
#   a non-auth re-login failure (OSError) still becomes UpdateFailed, so
#   only credentials problems get the special treatment. Added a case for
#   api is None + a bad-credentials re-login - the path that actually
#   matters in production (HA restart/reload with a since-changed
#   password), previously untested here. Added a fifth test exercising
#   the REAL async_login() method (every other test substitutes it),
#   pinning down the ValueError -> ConfigEntryAuthFailed translation
#   itself, which none of the control-flow tests above would catch
#   regressing on their own.
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
  * A re-login that fails because the gateway rejects the stored
    credentials raises ConfigEntryAuthFailed, UNCHANGED, from both call
    sites that matter (the post-expiry retry and the api-is-None initial
    login) - this is what lets Home Assistant start a reauth flow
    automatically. A re-login that fails for any OTHER reason (network,
    timeout) still becomes a plain UpdateFailed, never reauth.
  * async_login() itself performs the ValueError -> ConfigEntryAuthFailed
    translation - tested directly, not just through the mock the other
    cases substitute it with.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from homeassistant.exceptions import ConfigEntryAuthFailed  # noqa: E402
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


def test_failed_relogin_with_bad_credentials_triggers_reauth() -> None:
    """A ConfigEntryAuthFailed from the re-login must propagate UNCHANGED,
    not be caught by _async_update_data()'s surrounding except Exception
    and downgraded to UpdateFailed - that would silently swallow the
    signal DataUpdateCoordinator needs to start a reauth flow (see
    coordinator.py's async_login(), which is what actually raises this
    from a real ValueError - see test_async_login_translates_bad_
    credentials below for that translation itself).
    """
    coordinator = _make_coordinator(
        [SmileConnectSessionExpired("session gone")],
        login_error=ConfigEntryAuthFailed("Login failed: The Verification has failed."),
    )
    try:
        _run(coordinator)
        check("re-login rejected (bad credentials): raises ConfigEntryAuthFailed", False)
    except ConfigEntryAuthFailed as err:
        check(
            "re-login rejected (bad credentials): raises ConfigEntryAuthFailed unwrapped",
            "The Verification has failed." in str(err),
        )
    except UpdateFailed:
        check(
            "re-login rejected (bad credentials): raises ConfigEntryAuthFailed unwrapped",
            False,
        )


def test_failed_relogin_with_transport_error_stays_update_failed() -> None:
    """A non-auth re-login failure (network down, timeout, ...) must keep
    becoming a plain UpdateFailed - only a credentials problem should ever
    trigger reauth. Locks in that the two failure classes are NOT
    conflated by _async_update_data()'s control flow.
    """
    coordinator = _make_coordinator(
        [SmileConnectSessionExpired("session gone")],
        login_error=OSError("Connection refused"),
    )
    try:
        _run(coordinator)
        check("re-login fails (transport error): raises UpdateFailed", False)
    except UpdateFailed as err:
        check(
            "re-login fails (transport error): raises UpdateFailed mentioning re-login",
            "after re-login" in str(err),
        )
    except ConfigEntryAuthFailed:
        check("re-login fails (transport error): raises UpdateFailed, not reauth", False)


def test_initial_login_with_bad_credentials_triggers_reauth() -> None:
    """The path that actually matters in production: an HA restart or
    integration reload re-enters _async_update_data() with api is None,
    and the stored password no longer works (e.g. changed on the
    gateway). Before this feature, __init__.py's own unprotected
    coordinator.async_login() call meant this surfaced as a bare
    ValueError straight out of async_setup_entry() - no retry, no reauth
    prompt, just SETUP_ERROR. Confirms the fix covers this call site too,
    not just the post-expiry re-login further down.
    """
    coordinator = _make_coordinator(
        [{"rooms": ["ok"]}],
        login_error=ConfigEntryAuthFailed("Login failed: The Verification has failed."),
    )
    coordinator.api = None
    try:
        _run(coordinator)
        check("initial login rejected (api is None): raises ConfigEntryAuthFailed", False)
    except ConfigEntryAuthFailed:
        check("initial login rejected (api is None): raises ConfigEntryAuthFailed", True)
    except UpdateFailed:
        check(
            "initial login rejected (api is None): raises ConfigEntryAuthFailed, not UpdateFailed",
            False,
        )


def test_async_login_translates_bad_credentials() -> None:
    """The actual ValueError -> ConfigEntryAuthFailed translation, exercised
    against the REAL async_login() (not the mock _make_coordinator()
    substitutes it with) - none of the tests above touch this method's own
    body at all, so without this, the translation itself could silently
    regress (e.g. a future refactor dropping the except ValueError clause)
    with every test above still passing.
    """
    coordinator = object.__new__(SmileConnectCoordinator)
    coordinator.host = "192.168.1.132"
    coordinator.username = "someuser"
    coordinator.password = "wrongpass"
    coordinator.udid = "test-udid"
    coordinator._api_lock = asyncio.Lock()

    class _FakeHass:
        async def async_add_executor_job(self, func, *args):
            raise ValueError("Login failed: The Verification has failed.")

    coordinator.hass = _FakeHass()

    try:
        asyncio.run(coordinator.async_login())
        check("async_login() translates a bad-credentials ValueError", False)
    except ConfigEntryAuthFailed as err:
        check(
            "async_login() translates a bad-credentials ValueError into ConfigEntryAuthFailed",
            "The Verification has failed." in str(err),
        )
    except ValueError:
        check(
            "async_login() translates a bad-credentials ValueError into ConfigEntryAuthFailed",
            False,
        )


def main() -> int:
    for test in (
        test_healthy_cycle_does_not_log_in_again,
        test_initial_login_when_api_is_none,
        test_session_expiry_recovers,
        test_repeated_expiry_does_not_loop,
        test_api_error_does_not_trigger_relogin,
        test_transport_error_does_not_trigger_relogin,
        test_failed_relogin_with_bad_credentials_triggers_reauth,
        test_failed_relogin_with_transport_error_stays_update_failed,
        test_initial_login_with_bad_credentials_triggers_reauth,
        test_async_login_translates_bad_credentials,
    ):
        test()

    print("\n".join(RESULTS))
    failures = [line for line in RESULTS if line.startswith("FAIL")]
    print(f"\n{len(RESULTS) - len(failures)}/{len(RESULTS)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
