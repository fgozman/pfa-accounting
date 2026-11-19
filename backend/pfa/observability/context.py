"""Per-request (or per-job) context attached to every log record and audit row.

Values live in :mod:`contextvars`, so they follow the request through ``await``
points and never leak between concurrent requests.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass

#: what a client-supplied correlation id may look like: anything else (newlines,
#: control characters, huge values) would let a caller forge or break log lines
_CORRELATION_ID = re.compile(r"[A-Za-z0-9._:-]{8,64}")

_correlation_id: ContextVar[str | None] = ContextVar("pfa_correlation_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("pfa_user_id", default=None)
_client_ip: ContextVar[str | None] = ContextVar("pfa_client_ip", default=None)
_user_agent: ContextVar[str | None] = ContextVar("pfa_user_agent", default=None)


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def accept_correlation_id(value: str | None) -> str:
    """Keep a well-formed incoming id (to join logs across services), else mint one."""
    if value and _CORRELATION_ID.fullmatch(value):
        return value
    return new_correlation_id()


@dataclass(frozen=True, slots=True)
class Bound:
    """Tokens to restore the previous context (see :func:`bind` / :func:`reset`)."""

    tokens: tuple[tuple[ContextVar, Token], ...]


def bind(
    *,
    correlation_id: str | None = None,
    user_id: object | None = None,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> Bound:
    """Set the given values for the current context; ``None`` leaves a value as is."""
    tokens = []
    for var, value in (
        (_correlation_id, correlation_id),
        (_user_id, str(user_id) if user_id is not None else None),
        (_client_ip, client_ip[:64] if client_ip else None),
        (_user_agent, user_agent[:300] if user_agent else None),
    ):
        if value is not None:
            tokens.append((var, var.set(value)))
    return Bound(tuple(tokens))


def reset(bound: Bound) -> None:
    for var, token in reversed(bound.tokens):
        var.reset(token)


def correlation_id() -> str | None:
    return _correlation_id.get()


def user_id() -> str | None:
    return _user_id.get()


def client_ip() -> str | None:
    return _client_ip.get()


def user_agent() -> str | None:
    return _user_agent.get()


def snapshot() -> dict[str, str]:
    """The non-empty context values, as log attributes (OpenTelemetry names)."""
    values = {
        "pfa.correlation_id": _correlation_id.get(),
        "user.id": _user_id.get(),
        "client.address": _client_ip.get(),
    }
    return {key: value for key, value in values.items() if value}
