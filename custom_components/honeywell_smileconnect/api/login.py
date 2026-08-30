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

The password-hashing scheme below has been confirmed by extracting the
gateway's own JS implementation via its admin console (see CLAUDE.md for the
reverse-engineering method):

    request.hashAuthenticationToken = function(a, b) {
        return a = request.stringToCharcodes(a),
               b = request.stringToCharcodes(b),
               Crypt.pbkdf2(a, "" + b)
    }

    request.stringToCharcodes = function(a) {
        var b = "";
        if (a.length > 0)
            for (var c = 0; c < a.length; c++) {
                for (var d = "" + a.charCodeAt(c); d.length < 3;)
                    d = "0" + d;
                b += d
            }
        return b
    }

    Crypt.pbkdf2 = function(a, b, c) {
        c || (c = "base64");
        var d = CryptoJS.PBKDF2(a, b, {
            hasher: CryptoJS.algo.SHA512,
            keySize: 16,   // CryptoJS counts in 32-bit words -> 64 bytes
            iterations: 1  // yes, really just 1 iteration
        });
        return d.toString(CryptoJS.enc.Base64)
    }

i.e. hashed = Base64(PBKDF2-HMAC-SHA512(
        password = charcodes(password),
        salt     = charcodes(challenge_token),
        iterations = 1,
        dkLen    = 64 bytes))
"""
from __future__ import annotations

import base64
import json
import logging

import requests
from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.Util.Padding import unpad

from . import crypto
from .credentials import Credentials

_LOGGER = logging.getLogger(__name__)

HEADERS = {
    "Accept": "application/json, application/xml, text/plain, text/html, *.*",
    "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
}

FIXED_UDID = "web"
DEVICE_NAME = "Computer"

# Preshared IV used for AES decryption of devicetoken_encrypted. Confirmed
# against the gateway's own Crypt.aes256decrypt implementation.
_STATIC_IV_B64 = "D3GC5NQEFH13is04KD2tOg=="


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
        """Reproduces request.hashAuthenticationToken() from the gateway's
        own JS: pbkdf2(charcodes(password), charcodes(challenge_token)).
        """
        password_codes = crypto.string_to_charcodes(password)
        token_codes = crypto.string_to_charcodes(device_token)
        return crypto.pbkdf2_base64(password_codes, token_codes)

    def _decrypt_devicetoken(self, encrypted_data: str, decrypt_key: str) -> str:
        """Reproduces Crypt.aes256decrypt(): key = SHA-256(password), fixed
        IV, AES-256-CBC. Confirmed against the gateway's own JS.

        CryptoJS.toString(Utf8) implicitly strips standard PKCS7 padding -
        pycryptodome does not do this automatically, so it's done explicitly
        here via Crypto.Util.Padding.unpad rather than the fragile
        "\\x10"-stripping hack seen in the generic HeatApp reference code
        (which only happens to work when the padding is exactly 16 bytes).
        """
        crypt_key = SHA256.new(decrypt_key.encode("utf-8")).digest()
        cipher = AES.new(crypt_key, AES.MODE_CBC, base64.b64decode(_STATIC_IV_B64))
        decrypted = cipher.decrypt(base64.b64decode(encrypted_data))
        decrypted = unpad(decrypted, AES.block_size)
        return decrypted.decode("utf-8")

