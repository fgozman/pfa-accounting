"""ANAF connections and e-Factura transmission requests.

The ANAF connection is an *external* OAuth authorization (SPV), unrelated to the
application's own login: the user's SPV credentials are never requested or
stored, and the callback is protected by a one-use state secret.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pfa import clock
from pfa.anaf.efactura import transmission_deadline
from pfa.config import get_settings
from pfa.errors import CapabilityUnavailable, NotFound, ValidationFailed
from pfa.models.anaf import AnafConnection, ConnectorCapability
from pfa.models.documents import Document
from pfa.repo import anaf as anaf_repo
from pfa.repo.query import Row
from pfa.security.tokens import hash_token, new_opaque_token
from pfa.services.audit import enqueue_job, record_audit


@dataclass(frozen=True, slots=True)
class ConnectionStart:
    connection: AnafConnection
    #: where the browser goes to authorize; carries the one-use state
    authorization_url: str


@dataclass(frozen=True, slots=True)
class ConnectionStatus:
    connections: list[AnafConnection]
    verified_capabilities: list[ConnectorCapability]


@dataclass(frozen=True, slots=True)
class TransmissionRequest:
    job: Row
    #: legal transmission deadline derived from the invoice's issue date
    deadline: date | None
    #: configured transport ("fake" means nothing leaves the application)
    driver: str

    @property
    def simulated(self) -> bool:
        return self.driver == "fake"


async def start_connection(
    session: AsyncSession, *, workspace_id: uuid.UUID, actor_user_id: uuid.UUID
) -> ConnectionStart:
    settings = get_settings()
    state = new_opaque_token(32)
    connection = AnafConnection(
        workspace_id=workspace_id,
        environment=settings.anaf_environment,
        connector_client_ref=settings.anaf_client_id or None,
        status="pending",
        pending_state_hash=hash_token(state),
        created_by_user_id=actor_user_id,
    )
    session.add(connection)
    await session.flush()
    await record_audit(
        session,
        action="anaf.connect_started",
        entity_type="anaf_connection",
        entity_id=connection.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
    )
    authorization_url = (
        f"{settings.authorization_origin}/integrations/anaf/callback"
        f"?state={state}&connection_id={connection.id}"
    )
    return ConnectionStart(connection=connection, authorization_url=authorization_url)


async def connection_workspace(session: AsyncSession, connection_id: uuid.UUID) -> uuid.UUID | None:
    """Resolve a connection's workspace without a tenant context (identifier only)."""
    return await anaf_repo.connection_workspace(session, connection_id)


async def complete_authorization(
    session: AsyncSession,
    *,
    connection_id: uuid.UUID,
    state: str,
    error: str | None,
) -> AnafConnection:
    """Finish the external authorization once the one-use state is verified."""
    connection = await session.get(AnafConnection, connection_id)
    if connection is None:
        raise NotFound("Conexiunea ANAF nu există.")
    if connection.pending_state_hash is None or connection.pending_state_hash != hash_token(state):
        raise ValidationFailed("Legătura de stare ANAF nu este validă.")
    connection.pending_state_hash = None
    if error:
        connection.status = "error"
        connection.last_error = error
    else:
        connection.status = "active"
        connection.connected_at = clock.now()
        connection.authorized_identity = None
    await session.flush()
    return connection


async def connection_status(session: AsyncSession, *, workspace_id: uuid.UUID) -> ConnectionStatus:
    connections = (
        (
            await session.execute(
                select(AnafConnection)
                .where(AnafConnection.workspace_id == workspace_id)
                .order_by(AnafConnection.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    capabilities = (
        (
            await session.execute(
                select(ConnectorCapability).where(ConnectorCapability.enabled.is_(True))
            )
        )
        .scalars()
        .all()
    )
    return ConnectionStatus(connections=list(connections), verified_capabilities=list(capabilities))


async def request_efactura_transmission(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> TransmissionRequest:
    """Queue an e-Factura upload, or refuse when nothing could actually be sent."""
    capabilities = (
        (
            await session.execute(
                select(ConnectorCapability).where(
                    ConnectorCapability.form_code == "EFACTURA",
                    ConnectorCapability.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    settings = get_settings()
    # Without an authorized ANAF connection nothing can be transmitted, whatever the
    # driver is. Saying "registered" in that state told the user a filing had happened
    # when nothing had left the application.
    connection = (
        await session.execute(
            select(AnafConnection)
            .where(
                AnafConnection.workspace_id == workspace_id,
                AnafConnection.status == "active",
            )
            .order_by(AnafConnection.connected_at.desc().nullslast())
            .limit(1)
        )
    ).scalar_one_or_none()
    if connection is None:
        raise CapabilityUnavailable(
            "Transmiterea prin RO e-Factura nu este disponibilă: nu există o conexiune ANAF "
            "autorizată pentru acest spațiu de lucru. Autorizați conexiunea în "
            "Setări → Conexiune ANAF, sau generați XML-ul și încărcați-l în SPV."
        )
    if not capabilities and settings.anaf_driver != "fake":
        raise CapabilityUnavailable(
            "Transmiterea automată către RO e-Factura nu este activată: capabilitatea "
            "conectorului nu este verificată. Generați XML-ul și încărcați-l în SPV."
        )
    job = await enqueue_job(
        session,
        kind="efactura.transmit",
        workspace_id=workspace_id,
        payload={"document_id": str(document_id)},
        logical_key=f"efactura:{document_id}",
        created_by_user_id=actor_user_id,
    )
    # The deadline is derived from the invoice's own issue date, never from the
    # date the user happened to press the button.
    document = (
        await session.execute(
            select(Document).where(
                Document.id == document_id, Document.workspace_id == workspace_id
            )
        )
    ).scalar_one_or_none()
    if document is None:
        raise NotFound("Factura nu există în acest spațiu de lucru.")
    return TransmissionRequest(
        job=job,
        deadline=transmission_deadline(document.issue_date or clock.today()),
        driver=settings.anaf_driver,
    )
