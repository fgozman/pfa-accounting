"""Signing keys: loaded from outside PostgreSQL, published through JWKS.

Production uses a managed secret/key service.  Development and tests use a local
directory of Ed25519 private keys plus a ``manifest.json`` describing creation
and retirement times so verification keys survive a rotation overlap.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from pfa.config import Settings, get_settings

MANIFEST_NAME = "manifest.json"
ALGORITHM = "EdDSA"


class KeyStoreError(RuntimeError):
    pass


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass(slots=True)
class SigningKey:
    kid: str
    private_key: Ed25519PrivateKey | None
    created_at: datetime
    retired_at: datetime | None

    def is_active(self, now: datetime) -> bool:
        if self.retired_at is None:
            return True
        return now < self.retired_at

    def public_jwk(self) -> dict:
        if self.private_key is None:
            raise KeyStoreError(f"key {self.kid} has no private material")
        raw = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return {
            "kty": "OKP",
            "crv": "Ed25519",
            "alg": ALGORITHM,
            "use": "sig",
            "kid": self.kid,
            "x": _b64url(raw),
        }


class KeyStore:
    def __init__(self, keys: dict[str, SigningKey], active_kid: str) -> None:
        if active_kid not in keys:
            raise KeyStoreError(f"active kid {active_kid!r} is not present in the key store")
        self._keys = keys
        self._active_kid = active_kid

    # -- construction ---------------------------------------------------
    @classmethod
    def load(cls, directory: Path | str, active_kid: str | None = None) -> KeyStore:
        directory = Path(directory)
        if not directory.exists():
            raise KeyStoreError(
                f"key directory {directory} does not exist; run `make keys` to create "
                "a development signing key"
            )
        manifest_path = directory / MANIFEST_NAME
        manifest: dict = {}
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        entries = {entry["kid"]: entry for entry in manifest.get("keys", [])}
        keys: dict[str, SigningKey] = {}
        for path in sorted(directory.glob("*.pem")):
            kid = path.stem
            private_key = serialization.load_pem_private_key(
                path.read_bytes(), password=None
            )
            if not isinstance(private_key, Ed25519PrivateKey):
                raise KeyStoreError(f"key {kid} is not an Ed25519 private key")
            entry = entries.get(kid, {})
            keys[kid] = SigningKey(
                kid=kid,
                private_key=private_key,
                created_at=_parse_dt(entry.get("created_at")) or datetime.fromtimestamp(
                    path.stat().st_mtime, tz=UTC
                ),
                retired_at=_parse_dt(entry.get("retired_at")),
            )
        if not keys:
            raise KeyStoreError(f"no PEM signing keys found in {directory}")
        resolved_active = active_kid or manifest.get("active_kid")
        if resolved_active is None:
            live = [k for k in keys.values() if k.retired_at is None]
            if not live:
                raise KeyStoreError("no non-retired signing key available")
            resolved_active = max(live, key=lambda k: k.created_at).kid
        return cls(keys, resolved_active)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> KeyStore:
        settings = settings or get_settings()
        return cls.load(settings.key_dir, settings.key_active_kid or None)

    @classmethod
    def generate(
        cls,
        directory: Path | str,
        kid: str,
        *,
        retire_kids: list[str] | None = None,
        now: datetime | None = None,
        overlap: timedelta | None = None,
    ) -> KeyStore:
        """Create a new key and (optionally) retire older ones with an overlap."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        now = now or datetime.now(tz=UTC)
        private_key = Ed25519PrivateKey.generate()
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        target = directory / f"{kid}.pem"
        # 0600: private material is never world readable
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(pem)

        manifest_path = directory / MANIFEST_NAME
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.exists()
            else {"keys": []}
        )
        entries = {entry["kid"]: entry for entry in manifest.get("keys", [])}
        entries[kid] = {"kid": kid, "created_at": now.isoformat(), "retired_at": None}
        retire_at = (now + (overlap or timedelta(hours=48))).isoformat()
        for old in retire_kids or []:
            if old in entries and entries[old].get("retired_at") is None:
                entries[old]["retired_at"] = retire_at
        manifest["keys"] = list(entries.values())
        manifest["active_kid"] = kid
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return cls.load(directory, kid)

    # -- use ------------------------------------------------------------
    @property
    def active_kid(self) -> str:
        return self._active_kid

    def active(self) -> SigningKey:
        return self._keys[self._active_kid]

    def get(self, kid: str) -> SigningKey | None:
        return self._keys.get(kid)

    def verification_keys(self, now: datetime | None = None) -> list[SigningKey]:
        now = now or datetime.now(tz=UTC)
        return [key for key in self._keys.values() if key.is_active(now)]

    def jwks(self, now: datetime | None = None) -> dict:
        return {"keys": [key.public_jwk() for key in self.verification_keys(now)]}

    def public_key(self, kid: str) -> Ed25519PublicKey | None:
        key = self._keys.get(kid)
        if key is None or key.private_key is None:
            return None
        return key.private_key.public_key()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
