"""Authenticated, encrypted object storage.

Originals, generated artifacts and receipts are stored as ciphertext and are
never exposed as public URLs: downloads stream through bearer-authorized
endpoints that re-check workspace membership.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from pfa.config import Settings, get_settings
from pfa.errors import NotFound, ValidationFailed

KEY_FILE = "object-storage.key"


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    sha256: str
    size_bytes: int
    content_type: str


def _load_master_key(directory: Path) -> bytes:
    directory.mkdir(parents=True, exist_ok=True)
    key_path = directory / KEY_FILE
    if key_path.exists():
        return key_path.read_bytes()
    key = secrets.token_bytes(32)
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
    return key


class ObjectStore:
    """Content-addressed, AES-GCM encrypted blob store.

    Each object is stored as ``nonce || ciphertext``; the key never leaves the
    storage backend and is managed separately from PostgreSQL.
    """

    def __init__(self, root: Path, key: bytes) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._key_dir = self.root / ".keys"
        self._aead = AESGCM(key)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> ObjectStore:
        settings = settings or get_settings()
        return cls(settings.object_storage_dir, _load_master_key(settings.object_storage_dir))

    # ------------------------------------------------------------------
    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / digest[:2] / digest[2:4] / f"{digest}.bin"

    def put(self, data: bytes, *, content_type: str, namespace: str) -> StoredObject:
        digest = hashlib.sha256(data).hexdigest()
        key = f"{namespace}/{digest}"
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            nonce = secrets.token_bytes(12)
            aad = key.encode("utf-8")
            ciphertext = self._aead.encrypt(nonce, data, aad)
            tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            tmp.write_bytes(nonce + ciphertext)
            tmp.replace(path)
        return StoredObject(
            key=key, sha256=digest, size_bytes=len(data), content_type=content_type
        )

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise NotFound("Obiectul solicitat nu există.")
        blob = path.read_bytes()
        nonce, ciphertext = blob[:12], blob[12:]
        return self._aead.decrypt(nonce, ciphertext, key.encode("utf-8"))

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> bool:
        """Remove a stored object; False when it was already absent."""
        path = self._path(key)
        if not path.exists():
            return False
        path.unlink()
        return True

    def verify(self, key: str, expected_sha256: str) -> bool:
        return hashlib.sha256(self.get(key)).hexdigest() == expected_sha256


#: attachment content types that are accepted; active content is rejected
ALLOWED_ATTACHMENT_TYPES = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/tiff": ".tif",
    "text/csv": ".csv",
    "text/plain": ".txt",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}

REJECTED_ATTACHMENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "application/javascript",
    "text/javascript",
    "application/x-msdownload",
    "application/x-sh",
    "image/svg+xml",
}


def validate_attachment(content_type: str, size_bytes: int, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    base_type = content_type.split(";")[0].strip().lower()
    if base_type in REJECTED_ATTACHMENT_TYPES:
        raise ValidationFailed(
            "Tipul de fișier este respins: conținutul activ nu este acceptat.",
            field_errors={"content_type": "unsupported_type"},
        )
    if base_type not in ALLOWED_ATTACHMENT_TYPES:
        raise ValidationFailed(
            "Tipul de fișier nu este acceptat.",
            field_errors={"content_type": "unsupported_type"},
        )
    if size_bytes <= 0:
        raise ValidationFailed("Fișierul este gol.", field_errors={"size_bytes": "empty"})
    if size_bytes > settings.max_attachment_bytes:
        raise ValidationFailed(
            f"Fișierul depășește limita de {settings.max_attachment_bytes} octeți.",
            field_errors={"size_bytes": "too_large"},
        )


def scan_bytes(data: bytes) -> str:
    """Placeholder malware/active-content scan hook.

    Returns ``clean`` or ``flagged``; a flagged object is stored but never
    presented as evidence.  A real deployment plugs an AV service here.
    """
    lowered = data[:4096].lower()
    if b"<script" in lowered or b"javascript:" in lowered:
        return "flagged"
    if data[:2] == b"PK" and b"vbaproject" in data.lower():
        return "flagged"
    return "clean"
