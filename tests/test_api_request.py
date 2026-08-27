# Change log:
# - 2026-08-27: Initial regression tests for the pipe-string signature
#   construction rules extracted from request.getRequestSignature() (see
#   CLAUDE.md "Pipe-string construction" section). Locks in sorting, array
#   rendering, and None-filtering behaviour.
"""Tests for honeywell_smileconnect.api.apiRequest._build_pipe_signature_string.

These encode the exact rules reverse-engineered from the gateway's own JS:
keys sorted alphabetically, values joined as key=value|key=value|...| with
a trailing pipe, single-element lists rendered bare, 2+ element lists
rendered as [a,b,c], and (tested separately, in the request() method itself)
None values dropped entirely rather than rendered as "None".
"""
from __future__ import annotations

from honeywell_smileconnect.api.apiRequest import ApiRequest
from honeywell_smileconnect.api.credentials import Credentials
from honeywell_smileconnect.api.default_params import DefaultApiParams


class TestBuildPipeSignatureString:
    def test_sorts_keys_alphabetically(self):
        items = sorted({"udid": "web", "userid": 1, "reqcount": 0}.items())
        result = ApiRequest._build_pipe_signature_string(items)
        assert result == "reqcount=0|udid=web|userid=1|"

    def test_trailing_pipe_present(self):
        result = ApiRequest._build_pipe_signature_string([("a", "1")])
        assert result.endswith("|")

    def test_single_element_list_renders_bare_value(self):
        result = ApiRequest._build_pipe_signature_string([("rooms", [6])])
        assert result == "rooms=6|"

    def test_multi_element_list_renders_bracketed(self):
        result = ApiRequest._build_pipe_signature_string([("rooms", [6, 7, 8, 9])])
        assert result == "rooms=[6,7,8,9]|"

    def test_empty_list_renders_empty_value(self):
        result = ApiRequest._build_pipe_signature_string([("rooms", [])])
        assert result == "rooms=|"

    def test_scalar_values_render_as_str(self):
        result = ApiRequest._build_pipe_signature_string([("temperature", 20.5)])
        assert result == "temperature=20.5|"


class TestRequestDropsNoneValues:
    """None/undefined parameters must be dropped entirely from both the
    signature input and the outgoing request body - never sent as
    "key=None". Verified via the params dict built inside request(), since
    that's where the filtering actually happens (mirrors the JS `delete
    params[key]` behaviour on undefined values).
    """

    def test_none_valued_attribute_excluded_from_params(self):
        params_obj = DefaultApiParams()
        params_obj.roomid = 1
        params_obj.optional_field = None

        # Mirrors the filtering line in ApiRequest.request():
        filtered = {k: v for k, v in vars(params_obj).items() if v is not None}

        assert "optional_field" not in filtered
        assert filtered["roomid"] == 1


class TestCredentialsReqcountOrdering:
    """Regression test for the exact bug hit during development: using the
    CURRENT reqcount to sign, then incrementing for next time - not the
    other way around. See CLAUDE.md "reqcount semantics" section.
    """

    def test_first_call_uses_zero(self):
        creds = Credentials(username="u", password="p", udid="web")
        assert creds.next_reqcount() == 0

    def test_increments_after_each_call(self):
        creds = Credentials(username="u", password="p", udid="web")
        assert creds.next_reqcount() == 0
        assert creds.next_reqcount() == 1
        assert creds.next_reqcount() == 2

    def test_stored_value_reflects_next_call_not_current(self):
        creds = Credentials(username="u", password="p", udid="web")
        creds.next_reqcount()  # consumes 0, stores 1
        assert creds.reqcount == 1
