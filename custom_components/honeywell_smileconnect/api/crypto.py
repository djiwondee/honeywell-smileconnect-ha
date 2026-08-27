"""Shared cryptographic primitives extracted from the gateway's own JS.

Both password hashing (login) and request signing use the exact same
underlying scheme:

    Crypt.pbkdf2(a, b) = Base64(
        CryptoJS.PBKDF2(a, b, { hasher: SHA512, keySize: 16, iterations: 1 })
    )

...where `a` and `b` are first passed through `stringToCharcodes`. Only the
inputs differ:

- Login:     hashAuthenticationToken(password, challenge_token)
             -> pbkdf2(charcodes(password), charcodes(challenge_token))
- Signing:   encodeRequestSignature(data_string, devicetoken)
             -> pbkdf2(charcodes(data_string), charcodes(devicetoken))

Both were confirmed by extracting the gateway's admin-console JS - see
CLAUDE.md for the reverse-engineering method and docs/protocol.md for the
full write-up.
"""
from __future__ import annotations

import base64

from Crypto.Hash import SHA512
from Crypto.Protocol.KDF import PBKDF2

_PBKDF2_ITERATIONS = 1
_PBKDF2_DKLEN = 64  # bytes (CryptoJS keySize: 16 words * 4 bytes/word)


def string_to_charcodes(value: str) -> str:
    """Reproduces request.stringToCharcodes(): each character's char code,
    zero-padded to (at least) 3 digits, concatenated into one string.
    """
    return "".join(str(ord(c)).zfill(3) for c in value)


def pbkdf2_base64(a: str, b: str) -> str:
    """Reproduces Crypt.pbkdf2(a, b) with its default output format
    ("base64"). Callers pass already charcode-encoded strings for `a` and
    `b`, matching how the gateway's own callers use it.
    """
    derived = PBKDF2(
        a.encode("utf-8"),
        salt=b.encode("utf-8"),
        dkLen=_PBKDF2_DKLEN,
        count=_PBKDF2_ITERATIONS,
        hmac_hash_module=SHA512,
    )
    return base64.b64encode(derived).decode("ascii")
