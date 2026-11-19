"""Security primitives: password hashing, opaque tokens, JWT signing keys."""

from pfa.security.jwt import (
    ACCESS_AUDIENCE,
    ISSUER,
    REFRESH_AUDIENCE,
    TokenError,
    decode_token,
    issue_access_token,
    issue_refresh_token,
)
from pfa.security.keys import KeyStore, KeyStoreError
from pfa.security.passwords import hash_password, needs_rehash, verify_password
from pfa.security.tokens import (
    constant_time_equals,
    hash_token,
    new_opaque_token,
    new_pkce_verifier,
    new_state,
    pkce_challenge_s256,
    sha256_hex,
    valid_pkce_verifier,
    verify_pkce,
)

__all__ = [
    "ACCESS_AUDIENCE",
    "ISSUER",
    "REFRESH_AUDIENCE",
    "KeyStore",
    "KeyStoreError",
    "TokenError",
    "constant_time_equals",
    "decode_token",
    "hash_password",
    "hash_token",
    "issue_access_token",
    "issue_refresh_token",
    "needs_rehash",
    "new_opaque_token",
    "new_pkce_verifier",
    "new_state",
    "pkce_challenge_s256",
    "sha256_hex",
    "valid_pkce_verifier",
    "verify_password",
    "verify_pkce",
]
