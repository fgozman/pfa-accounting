"""Opaque token helpers: generation, hashing, PKCE verification.

Every opaque credential (email token, invitation, authorization code, refresh
token, transaction proof) is stored as a SHA-256 hash only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import uuid

#: RFC 7636 §4.1: 43..128 characters from the unreserved set
PKCE_VERIFIER_RE = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")
UNRESERVED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"


def new_opaque_token(nbytes: int = 32) -> str:
    """URL-safe, high-entropy token."""
    return base64.urlsafe_b64encode(secrets.token_bytes(nbytes)).rstrip(b"=").decode("ascii")


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_token(value: str) -> str:
    return sha256_hex(value)


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def new_state() -> str:
    return new_opaque_token(32)


def new_pkce_verifier() -> str:
    return new_opaque_token(48)


def pkce_challenge_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def valid_pkce_verifier(verifier: str) -> bool:
    return bool(PKCE_VERIFIER_RE.match(verifier))


def verify_pkce(verifier: str, challenge: str, method: str) -> bool:
    if method != "S256":
        return False
    if not valid_pkce_verifier(verifier):
        return False
    return constant_time_equals(pkce_challenge_s256(verifier), challenge)


def new_jti() -> uuid.UUID:
    return uuid.uuid4()


def content_hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
