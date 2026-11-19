"""Composition root: connects the application and the authorization server in-process.

The two sides only know each other's *interfaces*:

* the authorization server (:mod:`pfa.oauth`) needs an
  :class:`~pfa.oauth.ports.IdentityProvider` and an :class:`~pfa.oauth.ports.AuditSink`;
* the application needs an
  :class:`~pfa.clients.authorization_server.AuthorizationServerClient`.

This module is the only place that imports both sides to implement those
interfaces with direct calls.  :func:`wire` picks the deployment shape:

* **monolith** (default): both sides in one process, connected by the in-process
  adapters below;
* **split** (``PFA_AUTHORIZATION_SERVER_URL`` set): this process is the
  application only and talks to the server over HTTP
  (:class:`~pfa.clients.authorization_server_http.HttpAuthorizationServer`); the
  server itself is started with :func:`pfa.oauth.app.create_authorization_server_app`.

Every adapter opens its **own** unit of work, exactly as a remote call would: no
database session crosses the boundary.  The two sides do not even share a
database: the application uses ``PFA_DATABASE_URL``, the authorization server
``PFA_OAUTH_DATABASE_URL``.
"""

from __future__ import annotations

import uuid

from pfa.clients import authorization_server as authorization_server_client
from pfa.clients.authorization_server import AccessGrant
from pfa.clients.authorization_server_http import HttpAuthorizationServer
from pfa.config import Settings, get_settings
from pfa.db import get_database
from pfa.oauth import introspection
from pfa.oauth import ports as oauth_ports
from pfa.oauth import sessions as login_sessions
from pfa.oauth.db import get_oauth_database
from pfa.oauth.ports import LoginRejected, Subject
from pfa.services import accounts
from pfa.services.audit import record_audit

# ---------------------------------------------------------------------------
# Authorization server -> application
# ---------------------------------------------------------------------------


class AccountsIdentityProvider:
    """Answers the authorization server's credential checks from the account service."""

    async def authenticate(
        self,
        *,
        email: str,
        password: str,
        client_ip: str | None,
        attempt_key: str,
    ) -> Subject:
        # A refusal is returned, not raised, so this unit of work commits and the
        # failed-login audit record survives.
        async with get_database().session() as session:
            result = await accounts.check_credentials(
                session,
                email=email,
                password=password,
                client_ip=client_ip,
                attempt_key=attempt_key,
            )
        if not result.accepted:
            raise LoginRejected(result.rejection, result.message, http_status=result.http_status)
        return Subject(id=result.user_id)


class AuditLogSink:
    """Writes the authorization server's security events to the application's audit log.

    The audit log is in the application database, the event's source in the OAuth
    database: the record is written in its own unit of work.
    """

    async def record(
        self,
        *,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID,
        actor_user_id: uuid.UUID,
    ) -> None:
        async with get_database().session() as session:
            await record_audit(
                session,
            action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                actor_user_id=actor_user_id,
            )


# ---------------------------------------------------------------------------
# Application -> authorization server
# ---------------------------------------------------------------------------


class InProcessAuthorizationServer:
    """Calls the authorization server's public functions directly."""

    async def introspect(self, access_token: str) -> AccessGrant:
        token = await introspection.introspect_access_token(access_token)
        return AccessGrant(
            user_id=token.subject_id,
            session_id=token.session_id,
            client_id=token.client_id,
            scope=token.scope,
            jti=token.jti,
        )

    async def revoke_user_sessions(self, user_id: uuid.UUID, *, reason: str) -> None:
        async with get_oauth_database().session() as session:
            await login_sessions.revoke_all_for_user(session, user_id, reason=reason)


def wire(settings: Settings | None = None) -> None:
    """Configure the application's side for the deployment shape in ``settings``."""
    settings = settings or get_settings()
    if settings.authorization_server_is_remote:
        authorization_server_client.configure(HttpAuthorizationServer.from_settings(settings))
    else:
        wire_in_process()


def wire_in_process() -> None:
    """Connect both sides inside one process (idempotent)."""
    oauth_ports.configure(
        identity_provider=AccountsIdentityProvider(),
        audit_sink=AuditLogSink(),
    )
    authorization_server_client.configure(InProcessAuthorizationServer())
