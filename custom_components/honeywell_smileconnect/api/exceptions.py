"""Exceptions raised by the Smile Connect protocol layer.

Until 0.3.1 this package had no exception types of its own: every failure
was a bare ValueError, and a gateway response that reported a FAILURE was
not detected at all - `ApiRequest.request()` handed the error payload back
to the caller as though it were data. See that module's change log for
what that cost in production.

**These deliberately do NOT subclass ValueError.** Three existing handlers
catch ValueError broadly, and each would mis-diagnose a dead session:

  * climate.py's `except (NotImplementedError, ValueError)` around
    _async_apply_preset - would report it to the user as an input
    validation error.
  * config_flow.py's `except ValueError -> InvalidAuth` - would report it
    as wrong credentials.
  * climate.py's async_set_desired_temperature already carries a comment
    warning about exactly this hazard ("json decode errors are
    ValueErrors too and would be mis-reported as user input errors").

That is the opposite call from switching_times.ScheduleValidationError,
which subclasses ValueError on purpose so climate.py can translate it into
a ServiceValidationError - that one really is bad user input. A failed
gateway response is not.
"""
# Change log:
# - 2026-09-21: Initial version, added with the central response check in
#   api_request.py (0.3.1).
from __future__ import annotations


class SmileConnectApiError(Exception):
    """The gateway answered, but reported a failure.

    Carries the gateway's own `message` where there is one, since those are
    specific and actionable (e.g. "The input format is invalid: 14"), plus
    the endpoint and raw payload for logging.
    """

    def __init__(self, message: str, *, uri: str | None = None, payload: dict | None = None) -> None:
        super().__init__(message)
        self.uri = uri
        self.payload = payload or {}


class SmileConnectSessionExpired(SmileConnectApiError):
    """The gateway rejected the request's session (loginRejected).

    Distinguished from the general error on purpose: this is the one
    failure a caller can actually recover from on its own, by logging in
    again. coordinator.py catches exactly this type to do that, and lets
    everything else become an UpdateFailed.
    """
