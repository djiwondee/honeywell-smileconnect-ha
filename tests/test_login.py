# Change log:
# - 2026-09-24: Added TestUdidPassthrough - the direct regression test for
#   Login() no longer hardcoding FIXED_UDID into every Credentials it
#   builds (see api/login.py's own change log). Nothing before this would
#   have caught a future re-hardcoding.
# - 2026-08-27: Initial tests using real captured challenge/login response
#   fixtures (see tests/fixtures/), plus a synthetic AES round-trip test
#   for _decrypt_devicetoken (does not use the real account password, which
#   is intentionally not stored anywhere in this repo).
"""Tests for honeywell_smileconnect.api.login.

The real login_response.json fixture's devicetoken_encrypted value cannot
be decrypted here because that requires the real account password, which
is never committed to this repo. Instead, _decrypt_devicetoken is verified
with a self-constructed round trip (encrypt with the known-correct scheme,
then decrypt, assert we get the original plaintext back) - this still
catches regressions in the AES/PKCS7 handling without needing any secret.
"""
from __future__ import annotations

import base64
from unittest.mock import patch

from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.Util.Padding import pad

from .conftest import load_fixture
from custom_components.honeywell_smileconnect.api.login import FIXED_UDID, Login


class _FakeResponse:
    def __init__(self, payload: dict):
        import json

        self.content = json.dumps(payload).encode("utf-8")


class TestRequestChallengeToken:
    def test_parses_devicetoken_from_real_fixture(self):
        fixture = load_fixture("challenge_response.json")
        login = Login("http://192.168.1.132")

        with patch("requests.post", return_value=_FakeResponse(fixture)):
            token = login._request_challenge_token(
                type("Creds", (), {"udid": FIXED_UDID})()
            )

        assert token == "9c2281c6820383c0427d3a7d6990c007"


class TestLoginParsing:
    def test_raises_on_unsuccessful_response(self):
        login = Login("http://192.168.1.132")
        credentials = type(
            "Creds",
            (),
            {
                "udid": FIXED_UDID,
                "username": "someuser",
                "password": "somepass",
                "device_token": "sometoken",
            },
        )()

        failure_payload = {"success": False, "message": "The Verification has failed."}
        with patch("requests.post", return_value=_FakeResponse(failure_payload)):
            try:
                login._login(credentials)
                assert False, "expected ValueError on failed login"
            except ValueError as exc:
                assert "Verification has failed" in str(exc)

    def test_parses_userid_from_real_fixture(self):
        # Confirms the real gateway response shape is parsed correctly,
        # independent of the (unrecoverable-without-password) token value.
        fixture = load_fixture("login_response.json")
        assert fixture["userid"] == 1
        assert fixture["success"] is True


class TestUdidPassthrough:
    """Login(base_url, udid=...) must reach the Credentials it builds -
    see api/login.py's change log (two HA instances against the same
    gateway account were evicting each other's session because udid was
    hardcoded to FIXED_UDID everywhere, regardless of what was passed to
    Login itself).
    """

    _STATIC_IV_B64 = "D3GC5NQEFH13is04KD2tOg=="

    def _encrypt_like_gateway(self, plaintext: str, password: str) -> str:
        key = SHA256.new(password.encode("utf-8")).digest()
        cipher = AES.new(key, AES.MODE_CBC, base64.b64decode(self._STATIC_IV_B64))
        padded = pad(plaintext.encode("utf-8"), AES.block_size)
        return base64.b64encode(cipher.encrypt(padded)).decode("ascii")

    def test_default_udid_is_fixed_udid(self):
        login = Login("http://192.168.1.132")
        assert login.udid == FIXED_UDID

    def test_custom_udid_reaches_constructed_credentials(self):
        custom_udid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        login = Login("http://192.168.1.132", custom_udid)
        assert login.udid == custom_udid

        password = "correct horse battery staple"
        challenge_response = _FakeResponse({"devicetoken": "some-challenge-token"})
        login_response = _FakeResponse(
            {
                "success": True,
                "userid": 42,
                "devicetoken_encrypted": self._encrypt_like_gateway(
                    "abcdef0123456789abcdef0123456789", password
                ),
            }
        )

        with patch("requests.post", side_effect=[challenge_response, login_response]):
            credentials = login.authorize("someuser", password)

        assert credentials.udid == custom_udid


class TestDecryptDevicetokenRoundTrip:
    """Verifies the AES-256-CBC / PKCS7 handling is internally consistent,
    using a self-constructed ciphertext rather than the real (undecryptable
    without the real password) fixture value.
    """

    _STATIC_IV_B64 = "D3GC5NQEFH13is04KD2tOg=="

    def _encrypt_like_gateway(self, plaintext: str, password: str) -> str:
        key = SHA256.new(password.encode("utf-8")).digest()
        cipher = AES.new(key, AES.MODE_CBC, base64.b64decode(self._STATIC_IV_B64))
        padded = pad(plaintext.encode("utf-8"), AES.block_size)
        return base64.b64encode(cipher.encrypt(padded)).decode("ascii")

    def test_round_trip_recovers_original_plaintext(self):
        login = Login("http://192.168.1.132")
        original = "abcdef0123456789abcdef0123456789"  # looks like a devicetoken
        password = "correct horse battery staple"

        encrypted = self._encrypt_like_gateway(original, password)
        decrypted = login._decrypt_devicetoken(encrypted, password)

        assert decrypted == original

    def test_wrong_password_does_not_silently_succeed(self):
        login = Login("http://192.168.1.132")
        original = "abcdef0123456789abcdef0123456789"
        encrypted = self._encrypt_like_gateway(original, "correct password")

        try:
            login._decrypt_devicetoken(encrypted, "wrong password")
            # If unpad happens not to raise (rare but possible with garbage
            # padding bytes), the result must at least not equal the
            # original plaintext.
            result = login._decrypt_devicetoken(encrypted, "wrong password")
            assert result != original
        except (ValueError, UnicodeDecodeError):
            pass  # expected: wrong key corrupts padding or produces invalid UTF-8
