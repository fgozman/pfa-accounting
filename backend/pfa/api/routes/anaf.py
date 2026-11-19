"""ANAF connection, synchronization, e-Factura transmission and import."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from pfa.anaf.efactura import external_id_for_import, parse_efactura_xml
from pfa.api.deps import AnafConnectionScope, Principal, require_scope, workspace_session
from pfa.config import get_settings
from pfa.models.base import DeductibilityTreatment, DocumentKind, DocumentSource, VatTreatment
from pfa.services import anaf_connections
from pfa.services import documents as documents_service
from pfa.services.audit import enqueue_job

router = APIRouter(tags=["anaf"])


class AnafSyncRequest(BaseModel):
    days_back: int = 30
    kinds: list[str] = ["efactura_inbox"]


@router.post("/api/v1/workspaces/{workspace_id}/anaf/connect")
async def connect_anaf(
    workspace_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.manage"))],
) -> dict:
    """Begin ANAF authorization.

    The user's SPV credentials are never requested or stored.  The callback below
    is a backend-only route with its own registered redirect URI, separate from
    the SPA's application-login callback, protected by a one-use state binding.
    """
    started = await anaf_connections.start_connection(
        session, workspace_id=workspace_id, actor_user_id=principal.user_id
    )
    connection = started.connection
    authorization_url = started.authorization_url
    return {
        "connection_id": str(connection.id),
        "status": connection.status,
        "authorization_url": authorization_url,
        "note": (
            "Conectarea ANAF este separată de autentificarea în aplicație. Un cont al "
            "aplicației nu conferă drepturi în SPV."
        ),
    }


@router.get("/integrations/anaf/callback")
async def anaf_callback(
    request: Request,
    state: str,
    connection_id: uuid.UUID,
    scope: AnafConnectionScope,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Backend-only external OAuth callback.

    Returns the browser to the SPA without any token; the SPA receives only
    connection status.
    """
    settings = get_settings()
    await anaf_connections.complete_authorization(
        scope.session, connection_id=connection_id, state=state, error=error
    )
    target = f"{settings.spa_origin}/setari/anaf?connection={connection_id}&status=done"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/api/v1/workspaces/{workspace_id}/anaf/status")
async def anaf_status(
    workspace_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
) -> dict:
    status_view = await anaf_connections.connection_status(session, workspace_id=workspace_id)
    connections = status_view.connections
    capabilities = status_view.verified_capabilities
    return {
        "connections": [
            {
                "id": str(connection.id),
                "status": connection.status,
                "environment": connection.environment,
                "connected_at": (
                    connection.connected_at.isoformat() if connection.connected_at else None
                ),
                "expires_at": connection.expires_at.isoformat() if connection.expires_at else None,
                "last_error": connection.last_error,
            }
            for connection in connections
        ],
        "verified_capabilities": [
            {
                "form_code": capability.form_code,
                "channel": str(capability.channel),
                "environment": capability.environment,
                "verified_at": (
                    capability.verified_at.isoformat() if capability.verified_at else None
                ),
            }
            for capability in capabilities
        ],
        "note": (
            "Automatizarea depunerii este activă doar pentru formularele cu capabilitate "
            "verificată. Pentru restul, exportul validat și importul recipisei rămân "
            "disponibile."
        ),
    }


@router.post("/api/v1/workspaces/{workspace_id}/anaf/sync", status_code=status.HTTP_202_ACCEPTED)
async def sync_anaf(
    workspace_id: uuid.UUID,
    payload: AnafSyncRequest,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
) -> dict:
    job = await enqueue_job(
        session,
        kind="anaf.sync",
        workspace_id=workspace_id,
        payload={"days_back": payload.days_back, "kinds": payload.kinds},
        logical_key=f"anaf-sync:{workspace_id}:{payload.days_back}",
        created_by_user_id=principal.user_id,
    )
    return {"job_id": str(job.id), "status": str(job.status)}


@router.post(
    "/api/v1/workspaces/{workspace_id}/efactura/transmit/{document_id}",
    status_code=status.HTTP_202_ACCEPTED,
)
async def transmit_efactura(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
) -> dict:
    transmission = await anaf_connections.request_efactura_transmission(
        session,
        workspace_id=workspace_id,
        document_id=document_id,
        actor_user_id=principal.user_id,
    )
    job, deadline, simulated = transmission.job, transmission.deadline, transmission.simulated
    return {
        "job_id": str(job.id),
        "document_id": str(document_id),
        "deadline_hint": deadline.isoformat() if deadline else None,
        # the caller must be able to tell a real upload from a simulated one
        "transport": transmission.driver,
        "simulated": simulated,
        "note": (
            "Transmitere SIMULATĂ: serviciul ANAF nu este configurat, deci fișierul nu a "
            "fost trimis nicăieri. Acceptarea este confirmată doar de recipisa ANAF."
            if simulated
            else (
                "Termenul se calculează cu calendarul legal (zile lucrătoare) și cu termenul "
                "suplimentar legat de data emiterii facturii. Acceptarea este confirmată doar "
                "de recipisa ANAF."
            )
        ),
    }


@router.post("/api/v1/workspaces/{workspace_id}/efactura/import", status_code=status.HTTP_201_CREATED)
async def import_efactura(
    workspace_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
    file: Annotated[UploadFile, File()],
) -> dict:
    """Import a received e-Factura.

    Import implies neither payment nor deductibility status, and duplicates are
    detected before any accounting document is created.
    """
    data = await file.read()
    payload = parse_efactura_xml(data)
    external_id = external_id_for_import(payload)

    existing = await documents_service.find_duplicates(
        session,
        workspace_id=workspace_id,
        payload=documents_service.DocumentInput(
            kind=DocumentKind.PURCHASE_INVOICE,
            external_id=external_id,
            source=DocumentSource.EFACTURA,
        ),
    )
    if existing:
        # Import never double-posts an accounting document.
        return {
            "external_id": external_id,
            "duplicate": True,
            "duplicate_ids": [str(item.id) for item in existing],
            "proposal": payload,
            "requires_review": True,
            "implies_payment": False,
            "implies_deductibility": False,
            "next_step": "Documentul există deja; reconciliați-l în loc să îl reînregistrați.",
        }

    # A draft document is recorded so the next import can detect the duplicate,
    # and so the reviewer has somewhere to confirm deductibility.  It is a draft:
    # import does not post entries, declare deductibility or claim payment.
    created = await documents_service.create_document(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        payload=documents_service.DocumentInput(
            kind=DocumentKind.PURCHASE_INVOICE,
            supplier_name_raw=payload.get("supplier"),
            issue_date=(
                date.fromisoformat(payload["issue_date"]) if payload.get("issue_date") else None
            ),
            currency=payload.get("currency") or "RON",
            vat_treatment=VatTreatment.UNDETERMINED,
            deductibility=DeductibilityTreatment.UNDETERMINED,
            source=DocumentSource.EFACTURA,
            external_id=external_id,
            lines=(
                documents_service.DocumentLineInput(
                    description="Factură primită prin RO e-Factura",
                    quantity=Decimal("1"),
                    unit_price=Decimal(str(payload.get("payable_amount") or "0")),
                    vat_rate=Decimal("0"),
                ),
            ),
        ),
        allow_duplicate=True,
    )
    return {
        "external_id": external_id,
        "duplicate": False,
        "duplicate_ids": [],
        "document_id": str(created.id),
        "proposal": payload,
        "requires_review": True,
        "implies_payment": False,
        "implies_deductibility": False,
        "next_step": (
            "Verificați datele propuse, stabiliți deductibilitatea și înregistrați separat "
            "plata, dacă există."
        ),
    }
