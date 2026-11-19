"""Argon2id password hashing with deployment-benchmarked parameters."""

from __future__ import annotations

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from pfa.config import Settings, get_settings


def _hasher(settings: Settings | None = None) -> PasswordHasher:
    settings = settings or get_settings()
    return PasswordHasher(
        time_cost=settings.argon2_time_cost,
        memory_cost=settings.argon2_memory_cost,
        parallelism=settings.argon2_parallelism,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    )


def hash_password(password: str, settings: Settings | None = None) -> str:
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    if len(password) > 1024:
        raise ValueError("password must be at most 1024 characters")
    return _hasher(settings).hash(password)


def verify_password(password: str, password_hash: str, settings: Settings | None = None) -> bool:
    try:
        return _hasher(settings).verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(password_hash: str, settings: Settings | None = None) -> bool:
    try:
        return _hasher(settings).check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return True
