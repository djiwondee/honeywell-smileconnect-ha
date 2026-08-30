# Change log:
# - 2026-08-27: Initial regression tests for the crypto scheme confirmed by
#   extracting the gateway's own JS (see CLAUDE.md "Core Finding" section).
#   These use synthetic, self-computed test vectors, not real credentials -
#   the point is to lock in the *algorithm*, independent of any secret.
"""Tests for honeywell_smileconnect.api.crypto.

Cross-checks the PBKDF2/SHA-512/Base64 primitives against an independent
computation using Python's stdlib hashlib, so a future refactor can't
silently reintroduce the original MD5-based bug (see CLAUDE.md - the
request signature was originally implemented as plain MD5, which is wrong).
"""
from __future__ import annotations

import base64
import hashlib

from custom_components.honeywell_smileconnect.api import crypto


def _reference_pbkdf2_base64(a: str, b: str) -> str:
    """Independent reference implementation using only hashlib, mirroring
    Crypt.pbkdf2(a, b) exactly: PBKDF2-HMAC-SHA512, 1 iteration, 64-byte
    output, Base64-encoded. Used to cross-check crypto.pbkdf2_base64()
    without depending on pycryptodome giving "the same wrong answer twice".
    """
    derived = hashlib.pbkdf2_hmac("sha512", a.encode("utf-8"), b.encode("utf-8"), 1, dklen=64)
    return base64.b64encode(derived).decode("ascii")


class TestStringToCharcodes:
    def test_empty_string(self):
        assert crypto.string_to_charcodes("") == ""

    def test_known_example_from_gateway_docstring(self):
        # "AB" -> charCode('A')=65 -> "065", charCode('B')=66 -> "066"
        assert crypto.string_to_charcodes("AB") == "065066"

    def test_pads_to_three_digits(self):
        # charCode('!') == 33 -> zero-padded to "033"
        assert crypto.string_to_charcodes("!") == "033"

    def test_does_not_truncate_codes_of_three_or_more_digits(self):
        # charCode('€') == 8364 (4 digits) -> left as-is, no padding needed,
        # no truncation. Mirrors the JS loop condition `d.length < 3`.
        assert crypto.string_to_charcodes("€") == "8364"

    def test_concatenates_multiple_characters(self):
        assert crypto.string_to_charcodes("abc") == "097098099"


class TestPbkdf2Base64:
    def test_matches_independent_hashlib_reference(self):
        a = crypto.string_to_charcodes("testpassword")
        b = crypto.string_to_charcodes("52ac10b489b2c41091bed0ccf44dfd54")
        assert crypto.pbkdf2_base64(a, b) == _reference_pbkdf2_base64(a, b)

    def test_output_is_64_bytes_before_base64(self):
        result = crypto.pbkdf2_base64("a", "b")
        decoded = base64.b64decode(result)
        assert len(decoded) == 64  # keySize: 16 words * 4 bytes/word

    def test_different_inputs_produce_different_output(self):
        result_a = crypto.pbkdf2_base64("password_a", "salt")
        result_b = crypto.pbkdf2_base64("password_b", "salt")
        assert result_a != result_b

    def test_is_deterministic(self):
        # iterations=1 with fixed inputs must always produce the same
        # output - this is what makes it usable as a signature at all.
        first = crypto.pbkdf2_base64("stable_input", "stable_salt")
        second = crypto.pbkdf2_base64("stable_input", "stable_salt")
        assert first == second
