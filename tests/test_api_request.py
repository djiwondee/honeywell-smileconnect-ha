# Change log:
# - 2026-08-30 (c): Added bool-rendering tests (TestRenderValue) and a
#   dedicated /api/scene/set body-level regression test - the third bug
#   found in this function: Python's str(True)/str(False) produces
#   "True"/"False" (capitalized), but the gateway's own JS coerces
#   booleans to lowercase "true"/"false". This meant every
#   /api/scene/set(active=False) call returned success:true without the
#   gateway ever actually deactivating the scene - see api_request.py's
#   change log for the full production-debugging story.
# - 2026-08-30 (b): Fixed test_empty_list_renders_empty_value /
#   test_empty_list_renders_empty_string - the correct rendering for an
#   empty array is the literal string "undefined" (mirroring the
#   gateway's own JS string-coercion of an undefined array element), NOT
#   an empty string as these tests previously (incorrectly) asserted.
#   This wrong assumption was baked into the very first version of
#   api_request.py and went unnoticed until a real production timeout
#   (removing the last room from a scene sent a genuinely empty "rooms="
#   value, which the gateway's firmware appears to hang on).
# - 2026-08-30 (a): Added TestRenderValue and TestRequestBodyMatchesSignature -
#   regression tests for a real bug found in production: the actual HTTP
#   body previously used Python's default str() on raw list values
#   (producing "[1]" for a single-element list instead of the bare "1",
#   and "[6, 7, 8, 9]" with spaces instead of "[6,7,8,9]"), diverging from
#   both the signature string and the protocol's expected wire format -
#   see api_request.py's own change log for the full story. These tests
#   would have caught it.
# - 2026-08-27: Initial regression tests for the pipe-string signature
#   construction rules extracted from request.getRequestSignature() (see
#   CLAUDE.md "Pipe-string construction" section). Locks in sorting, array
#   rendering, and None-filtering behaviour.
"""Tests for honeywell_smileconnect.api.api_request._build_pipe_signature_string.

These encode the exact rules reverse-engineered from the gateway's own JS:
keys sorted alphabetically, values joined as key=value|key=value|...| with
a trailing pipe, single-element lists rendered bare, 2+ element lists
rendered as [a,b,c], and (tested separately, in the request() method itself)
None values dropped entirely rather than rendered as "None".
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from custom_components.honeywell_smileconnect.api.api_request import ApiRequest
from custom_components.honeywell_smileconnect.api.credentials import Credentials
from custom_components.honeywell_smileconnect.api.default_params import DefaultApiParams


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

    def test_empty_list_renders_as_literal_undefined(self):
        # NOT "rooms=|" - the gateway's own JS string-coerces an empty
        # array's g[0] (JavaScript's `undefined`) into the literal text
        # "undefined". Getting this wrong caused a real production
        # timeout - see api_request.py's change log.
        result = ApiRequest._build_pipe_signature_string([("rooms", [])])
        assert result == "rooms=undefined|"

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


class TestRenderValue:
    """_render_value() is the single source of truth for how a parameter
    value is turned into a string, for BOTH the signature and the actual
    body - see api_request.py's module change log for the real bug this
    guards against (Python's default str() on raw lists producing a
    different, protocol-incorrect representation than intended).
    """

    def test_empty_list_renders_as_literal_undefined(self):
        # Mirrors the gateway's own JS: an empty array's g[0] is
        # JavaScript's `undefined`, string-concatenated into the literal
        # text "undefined" - NOT an empty string. See api_request.py's
        # change log for the real production bug this guards against.
        assert ApiRequest._render_value([]) == "undefined"

    def test_single_element_list_renders_bare_value_no_brackets(self):
        assert ApiRequest._render_value([1]) == "1"

    def test_multi_element_list_renders_bracketed_no_spaces(self):
        assert ApiRequest._render_value([6, 7, 8, 9]) == "[6,7,8,9]"

    def test_scalar_renders_via_str(self):
        assert ApiRequest._render_value(20.5) == "20.5"
        assert ApiRequest._render_value("Boost") == "Boost"

    def test_bool_renders_as_lowercase_js_style_not_python_capitalized(self):
        # THE actual production bug: Python's str(True)/str(False) would
        # produce "True"/"False" (capitalized) - the gateway's own JS
        # coerces booleans to lowercase "true"/"false" in string
        # concatenation. This is why /api/scene/set(active=False) always
        # returned success:true without the scene ever actually
        # deactivating (confirmed via live log: scene/status polled right
        # after showed Standby still isActive:true every time).
        assert ApiRequest._render_value(True) == "true"
        assert ApiRequest._render_value(False) == "false"

    def test_bool_does_not_render_as_integer(self):
        # bool is a subclass of int in Python - guard against a future
        # refactor accidentally routing it through numeric/str() handling
        # and producing "1"/"0" instead of "true"/"false".
        assert ApiRequest._render_value(True) != "1"
        assert ApiRequest._render_value(False) != "0"


class TestRequestBodyMatchesSignature:
    """Regression tests for the real production bug: the HTTP body must
    use the exact same rendering as the signature string for array-valued
    parameters. Verified by mocking requests.post and inspecting the
    actual `data=` payload sent, not just the signature computation in
    isolation - a test that only checked _build_pipe_signature_string()
    would NOT have caught this bug, since that function was always
    correct; the bug was specifically in how the body was built
    separately from it.
    """

    def _make_credentials(self) -> Credentials:
        creds = Credentials(username="u", password="p", udid="web")
        creds.user_id = 1
        creds.authorization_token = "faketoken"
        return creds

    def test_single_element_list_in_body_has_no_brackets(self):
        params = DefaultApiParams()
        params.scene = "Standby"
        params.rooms = [1]

        fake_response = MagicMock()
        fake_response.content = b'{"success": true}'

        with patch("requests.post", return_value=fake_response) as mock_post:
            ApiRequest().request("http://x/api/scene/setrooms", self._make_credentials(), params)

        sent_body = mock_post.call_args.kwargs["data"]
        assert "rooms=1" in sent_body
        assert "rooms=%5B1%5D" not in sent_body  # "[1]" URL-encoded - the bug's signature

    def test_multi_element_list_in_body_has_no_spaces(self):
        params = DefaultApiParams()
        params.scene = "Boost"
        params.rooms = [6, 7, 8, 9]

        fake_response = MagicMock()
        fake_response.content = b'{"success": true}'

        with patch("requests.post", return_value=fake_response) as mock_post:
            ApiRequest().request("http://x/api/scene/setrooms", self._make_credentials(), params)

        sent_body = mock_post.call_args.kwargs["data"]
        # "[6,7,8,9]" URL-encoded, no spaces:
        assert "rooms=%5B6%2C7%2C8%2C9%5D" in sent_body
        # The buggy version would have produced "[6, 7, 8, 9]" (with
        # spaces), URL-encoded as containing "%2C+" (comma-space) instead
        # of a bare "%2C" (comma) between elements:
        assert "%2C+" not in sent_body

    def test_empty_list_in_body_is_literal_undefined_not_empty(self):
        """The exact real-world production bug: removing the last/only
        room from a scene calls set_scene_rooms(scene_name, []) - an empty
        list. Sending a genuinely empty "rooms=" value made the gateway's
        firmware hang/timeout (10s ReadTimeout observed in production).
        The correct wire value is the literal string "undefined",
        mirroring the gateway's own JS string-coercion of an empty
        array's undefined first element.
        """
        params = DefaultApiParams()
        params.scene = "Standby"
        params.rooms = []

        fake_response = MagicMock()
        fake_response.content = b'{"success": true}'

        with patch("requests.post", return_value=fake_response) as mock_post:
            ApiRequest().request("http://x/api/scene/setrooms", self._make_credentials(), params)

        sent_body = mock_post.call_args.kwargs["data"]
        assert "rooms=undefined" in sent_body
        assert "rooms=&" not in sent_body  # a genuinely empty value - the bug

    def test_scene_set_active_false_in_body_is_lowercase(self):
        """The exact real-world production bug: /api/scene/set(active=
        False) always returned success:true, but the scene never actually
        deactivated - live-log-confirmed via a scene/status poll right
        after showing Standby still isActive:true, repeatedly, across
        multiple attempts. Root cause: "active=False" (Python-capitalized)
        was sent instead of "active=false" (the gateway's own JS
        convention).
        """
        params = DefaultApiParams()
        params.scene = "Standby"
        params.active = False
        params.duration = 1

        fake_response = MagicMock()
        fake_response.content = b'{"success": true}'

        with patch("requests.post", return_value=fake_response) as mock_post:
            ApiRequest().request("http://x/api/scene/set", self._make_credentials(), params)

        sent_body = mock_post.call_args.kwargs["data"]
        assert "active=false" in sent_body
        assert "active=False" not in sent_body

    def test_scene_set_active_true_in_body_is_lowercase(self):
        params = DefaultApiParams()
        params.scene = "Boost"
        params.active = True
        params.duration = 30

        fake_response = MagicMock()
        fake_response.content = b'{"success": true}'

        with patch("requests.post", return_value=fake_response) as mock_post:
            ApiRequest().request("http://x/api/scene/set", self._make_credentials(), params)

        sent_body = mock_post.call_args.kwargs["data"]
        assert "active=true" in sent_body
        assert "active=True" not in sent_body
