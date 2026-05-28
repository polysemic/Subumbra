"""Shared stdlib password hashing helpers for bootstrap-managed UI auth."""

from __future__ import annotations

import hashlib
import hmac
import os

_ALGORITHM = "sha256"
_ITERATIONS = 260_000
_SALT_BYTES = 16


def hash_ui_password(password: str) -> str:
    """Return a pbkdf2-sha256 hash for the provided plaintext password."""
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(_ALGORITHM, password.encode("utf-8"), salt, _ITERATIONS)
    return f"pbkdf2-sha256:{salt.hex()}:{digest.hex()}"


def verify_ui_password(password: str, stored: str) -> bool:
    """Return True when plaintext matches the stored pbkdf2-sha256 hash."""
    try:
        algorithm, salt_hex, digest_hex = stored.split(":")
    except ValueError:
        return False
    if algorithm != "pbkdf2-sha256":
        return False
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac(_ALGORITHM, password.encode("utf-8"), salt, _ITERATIONS)
    return hmac.compare_digest(candidate, expected)
