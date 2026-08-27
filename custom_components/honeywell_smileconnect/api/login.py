"""Login / challenge-response handling for Honeywell Smile Connect gateways.

This differs from the standard HeatApp protocol in several important ways -
see docs/protocol.md for the full write-up. Summary:

- udid is the fixed literal "web", not a generated UUID.
- devicename sent during login is "Computer", not "homeassistant".
- Password hashing uses PBKDF2/SHA-512 with a "stringToCharcodes"
  pre-processing step (as opposed to plain MD5(password + token) on
  standard HeatApp gateways).
- The devicetoken_encrypted value returned by the gateway is decrypted the
  same way as standard HeatApp (AES-256-CBC, key = SHA-256(password), fixed
  IV) - this has NOT yet been independently reconfirmed against a Honeywell
  gateway and should be validated before relying on it in production.
"""
from __future__ import annotations

import base64
import json
import logging

import requests
from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import PBKDF2
from Crypto.Hash import SHA512

from .credentials import Credentials

_LOGGER = logging.getLogger(__name__)

HEADERS = {
    "Accept": "application/json, application/xml, text/plain, text/html, *.*",
    "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
}

FIXED_UDID = "web"
DEVICE_NAME = "Computer"

# Preshared IV used by the standard HeatApp protocol for AES decryption of
# devicetoken_encrypted. NOT YET CONFIRMED identical on Honeywell hardware -
# verify against a live login capture before trusting this in production.
_STATIC_IV_B64 = "D3GC5NQEFH13is04KD2tOg=="

# TODO VERIFY: exact PBKDF2 parameters (iteration count, salt, derived key
# length) as used by the gateway's own JS (oem.min.js / assets.min.js). The
# values below are placeholders based on common defaults and MUST be
# reconfirmed via the browser-console reverse engineering method described
# in CLAUDE.md before this is considered correct.
_PBKDF2_ITERATIONS = 1000
_PBKDF2_KEYLEN = 64  # bytes (SHA-512 digest size)


class Login:
    """Performs the challenge/response login against a Smile Connect gateway."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def authorize(self, username: str, password: str) -> Credentials:
        if not username or not password:
            raise ValueError("username and password are required")

        credentials = Credentials(username=username, password=password, udid=FIXED_UDID)
        credentials.device_token = self._request_challenge_token(credentials)
        return self._login(credentials)

    # -- internal -----------------------------------------------------

    def _request_challenge_token(self, credentials: Credentials) -> str:
        uri = self.base_url + "/api/user/token/challenge"
        body = {"udid": credentials.udid}
        response = requests.post(uri, headers=HEADERS, data=body, timeout=10)
        payload = json.loads(response.content)
        _LOGGER.debug("challenge token response: %s", payload)
        return payload["devicetoken"]

    def _login(self, credentials: Credentials) -> Credentials:
        hashed = self._hash_auth_token(credentials.password, credentials.device_token)

        login_body = {
            "udid": credentials.udid,
            "login": credentials.username,
            "token": credentials.device_token,
            "hashed": hashed,
            "devicename": DEVICE_NAME,
        }
        uri = self.base_url + "/api/user/token/response"
        response = requests.post(uri, headers=HEADERS, data=login_body, timeout=10)
        payload = json.loads(response.content)
        _LOGGER.debug("login response: %s", payload)

        if not payload.get("success", False):
            raise ValueError(f"Login failed: {payload.get('message', 'unknown error')}")

        credentials.user_id = payload["userid"]
        credentials.authorization_token = self._decrypt_devicetoken(
            payload["devicetoken_encrypted"], credentials.password
        )
        return credentials

    def _hash_auth_token(self, password: str, device_token: str) -> str:
        """Honeywell-specific password hashing.

        TODO VERIFY: this is a best-effort reconstruction based on partial
        reverse engineering (PBKDF2/SHA-512 with a "stringToCharcodes"
        pre-processing step observed in the gateway's minified JS). Confirm
        against a live capture using CryptoJS in the admin console before
        relying on this for anything beyond local experimentation.
        """
        charcodes = self._string_to_charcodes(password + device_token)
        derived = PBKDF2(
            charcodes,
            salt=device_token.encode("utf-8"),
            dkLen=_PBKDF2_KEYLEN,
            count=_PBKDF2_ITERATIONS,
            hmac_hash_module=SHA512,
        )
        return derived.hex()

    @staticmethod
    def _string_to_charcodes(value: str) -> bytes:
        """Mirrors a `stringToCharcodes`-style JS helper: each character's
        char code, concatenated, re-encoded as bytes.

        TODO VERIFY exact transformation against the gateway's own JS
        implementation - this is currently a reasonable guess, not a
        confirmed reproduction.
        """
        return "".join(str(ord(c)) for c in value).encode("utf-8")

    def _decrypt_devicetoken(self, encrypted_data: str, decrypt_key: str) -> str:
        crypt_key = SHA256.new(decrypt_key.encode("utf-8")).digest()
        cipher = AES.new(crypt_key, AES.MODE_CBC, base64.b64decode(_STATIC_IV_B64))
        decrypted = cipher.decrypt(base64.b64decode(encrypted_data))
        # Standard HeatApp strips a literal \x10 padding byte rather than
        # using proper PKCS7 unpadding - mirrored here pending verification.
        return decrypted.decode("ascii").strip("\x10")
