"""The authorization server's signing keys (process-wide, loaded once).

Private keys never leave the authorization server.  Anything else that needs to
verify a token uses the public JWKS (``/.well-known/jwks.json``) or asks the server
through introspection.
"""

from __future__ import annotations

from functools import lru_cache

from pfa.config import Settings
from pfa.security.keys import KeyStore, KeyStoreError


@lru_cache(maxsize=1)
def get_keys() -> KeyStore:
    return KeyStore.from_settings()


def reset_keys() -> None:
    get_keys.cache_clear()


def ensure_keys(settings: Settings) -> KeyStore:
    """Load the signing keys, generating development keys when none exist.

    Production refuses to start without provisioned keys.
    """
    if not settings.key_dir.exists():
        if settings.is_production:
            raise KeyStoreError(
                f"Signing keys are missing at {settings.key_dir}; refusing to start "
                "in production without them."
            )
        KeyStore.generate(settings.key_dir, settings.key_active_kid)
    reset_keys()
    return get_keys()
