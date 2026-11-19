"""Declarations, calculation, approval, export, submission, jobs and downloads."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from pfa.api.deps import (
    DeclarationScope,
    JobScope,
    PersonalDeclarationScope,
    Principal,
    UserSession,
    idempotency_key,
    require_scope,
    workspace_session,
)
from pfa.errors import NotFound, PermissionDenied
from pfa.services import declaration_service, jobs, tenancy
from pfa.services.idempotency import execute_idempotent
from pfa.services.storage import ObjectStore

router = APIRouter(prefix="/api/v1", tags=["declarations"])

DECLARATION_FORMS = {
    "D212": "annual",
    "D300": "period",
    "D301": "period",
    "D390": "period",
    "D394": "quarter",
}


class DeclarationPrepare(BaseModel):
    form_code: str
    income_year: int
    period_start: date
    period_end: date
    taxpayer_id: uuid.UUID | None = None
    taxpayer_key: str | None = None
    taxpayer_identity: dict[str, Any] = Field(default_factory=dict)
    correction_of: uuid.UUID | None = None


class ReceiptImport(BaseModel):
    payload: dict[str, Any]
    receipt_xml: str | None = None
    manually_reported: bool = False


def _declaration_view(declaration: Any, extra: dict | None = None) -> dict:
    view = {
        "id": str(declaration.id),
        "form_code": declaration.form_code,
        "form_name_ro": declaration.form_name_ro,
        "income_year": declaration.income_year,
        "period_start": declaration.period_start.isoformat(),
        "period_end": declaration.period_end.isoformat(),
        "status": str(declaration.status),
        "current_version": declaration.current_version,
        "rule_pack_code": declaration.rule_pack_code,
        "rule_pack_version": declaration.rule_pack_version,
        "taxpayer_identity": declaration.taxpayer_identity,
        "estimate_only": declaration.estimate_only,
        "approved_at": declaration.approved_at.isoformat() if declaration.approved_at else None,
        "approved_by_user_id": (
            str(declaration.approved_by_user_id) if declaration.approved_by_user_id else None
        ),
        "submitted_at": declaration.submitted_at.isoformat() if declaration.submitted_at else None,
        "accepted_at": declaration.accepted_at.isoformat() if declaration.accepted_at else None,
        "rejected_at": declaration.rejected_at.isoformat() if declaration.rejected_at else None,
        "submission_available": declaration.submission_available,
        "submission_unavailable_reason": declaration.submission_unavailable_reason,
        "correction_of_declaration_id": (
            str(declaration.correction_of_declaration_id)
            if declaration.correction_of_declaration_id
            else None
        ),
    }
    if extra:
        view.update(extra)
    return view


@router.post("/taxpayers/{taxpayer_id}/declarations", status_code=status.HTTP_201_CREATED)
async def prepare_personal_declaration(
    taxpayer_id: uuid.UUID,
    payload: DeclarationPrepare,
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
    scope: PersonalDeclarationScope,
) -> dict:
    """Prepare a personal D212 with independently authorized source workspaces.

    The taxpayer may belong to a workspace directly, or authorize consolidation
    through an explicit access grant.  The workspace used must be one where the
    caller is an active member.
    """
    # the workspace is resolved and the membership checked by
    # pfa.api.deps.personal_declaration_scope before this handler runs
    workspace_id, session = scope.workspace_id, scope.session
    declaration = await declaration_service.prepare_declaration(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        form_code=payload.form_code,
        income_year=payload.income_year,
        period_start=payload.period_start,
        period_end=payload.period_end,
        taxpayer_id=taxpayer_id,
        taxpayer_identity=payload.taxpayer_identity,
        taxpayer_key=payload.taxpayer_key or str(taxpayer_id),
        correction_of=payload.correction_of,
    )
    return _declaration_view(
        declaration,
        {
            "sharing_notice": (
                "Declarația și documentele justificative atașate devin vizibile pentru "
                "toți membrii activi ai spațiului de lucru."
            )
        },
    )


@router.post("/workspaces/{workspace_id}/declarations", status_code=status.HTTP_201_CREATED)
async def prepare_declaration(
    workspace_id: uuid.UUID,
    payload: DeclarationPrepare,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
) -> dict:
    declaration = await declaration_service.prepare_declaration(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        form_code=payload.form_code,
        income_year=payload.income_year,
        period_start=payload.period_start,
        period_end=payload.period_end,
        taxpayer_id=payload.taxpayer_id,
        taxpayer_identity=payload.taxpayer_identity,
        taxpayer_key=payload.taxpayer_key,
        correction_of=payload.correction_of,
    )
    return _declaration_view(declaration)


@router.get("/workspaces/{workspace_id}/declarations")
async def list_declarations(
    workspace_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
) -> dict:
    declarations = await declaration_service.list_declarations(
        session, workspace_id=workspace_id
    )
    versions = await declaration_service.latest_versions_for(
        session, declaration_ids=[item.id for item in declarations]
    )
    items = []
    for item in declarations:
        version = versions.get(item.id)
        extra = {}
        if version is not None:
            # the list must carry what the review panel shows, exactly like the detail
            extra = {
                "calculated_values": version.calculated_values,
                "trace": version.trace,
                "artifact_sha256": version.artifact_sha256,
                "artifact_is_preview": version.artifact_is_preview,
            }
        items.append(_declaration_view(item, extra))
    return {"items": items}


@router.get("/declarations/{declaration_id}")
async def get_declaration(
    declaration_id: uuid.UUID,
    scope: DeclarationScope,
) -> dict:
    session = scope.session
    declaration = await declaration_service.load_declaration(session, declaration_id)
    version = await declaration_service.latest_version(session, declaration_id=declaration_id)
    extra = {}
    if version is not None:
        extra["calculated_values"] = version.calculated_values
        extra["trace"] = version.trace
        extra["artifact_sha256"] = version.artifact_sha256
        extra["artifact_is_preview"] = version.artifact_is_preview
    return _declaration_view(declaration, extra)


@router.post("/declarations/{declaration_id}/calculate")
async def calculate(
    declaration_id: uuid.UUID,
    scope: DeclarationScope,
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
    key: Annotated[str, Depends(idempotency_key)] = "",
) -> dict:
    workspace_id, session = scope.workspace_id, scope.session

    async def handler() -> tuple[dict, int, uuid.UUID | None, str | None]:
        declaration = await declaration_service.calculate_declaration(
            session,
            workspace_id=workspace_id,
            actor_user_id=principal.user_id,
            declaration_id=declaration_id,
        )
        version = await declaration_service.latest_version(
            session, declaration_id=declaration_id
        )
        return (
            _declaration_view(
                declaration,
                {
                    "calculated_values": version.calculated_values if version else {},
                    "trace": version.trace if version else [],
                },
            ),
            200,
            declaration_id,
            "declaration",
        )

    outcome = await execute_idempotent(
        session,
        workspace_id=workspace_id,
        user_id=principal.user_id,
        action="declaration.calculate",
        key=key,
        request_payload={"declaration_id": str(declaration_id)},
        handler=handler,
    )
    return outcome.response


@router.post("/declarations/{declaration_id}/validate")
async def validate(
    declaration_id: uuid.UUID,
    scope: DeclarationScope,
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
) -> dict:
    workspace_id, session = scope.workspace_id, scope.session
    declaration = await declaration_service.validate_declaration(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        declaration_id=declaration_id,
    )
    return _declaration_view(declaration)


@router.delete("/declarations/{declaration_id}")
async def delete_declaration(
    declaration_id: uuid.UUID,
    scope: DeclarationScope,
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
) -> dict:
    """Remove a declaration that was neither approved nor filed."""
    workspace_id, session = scope.workspace_id, scope.session
    return await declaration_service.delete_declaration(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        declaration_id=declaration_id,
    )


@router.post("/declarations/{declaration_id}/approve")
async def approve(
    declaration_id: uuid.UUID,
    scope: DeclarationScope,
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
) -> dict:
    workspace_id, session = scope.workspace_id, scope.session
    declaration = await declaration_service.approve_declaration(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        declaration_id=declaration_id,
    )
    return _declaration_view(declaration)


@router.post("/declarations/{declaration_id}/export")
async def export(
    declaration_id: uuid.UUID,
    scope: DeclarationScope,
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
    key: Annotated[str, Depends(idempotency_key)] = "",
) -> dict:
    workspace_id, session = scope.workspace_id, scope.session

    async def handler() -> tuple[dict, int, uuid.UUID | None, str | None]:
        declaration, artifact = await declaration_service.export_declaration(
            session,
            workspace_id=workspace_id,
            actor_user_id=principal.user_id,
            declaration_id=declaration_id,
        )
        return (
            _declaration_view(
                declaration,
                {
                    "artifact": artifact,
                    "download_path": f"/api/v1/declarations/{declaration_id}/artifacts/latest",
                },
            ),
            200,
            declaration_id,
            "declaration",
        )

    outcome = await execute_idempotent(
        session,
        workspace_id=workspace_id,
        user_id=principal.user_id,
        action="declaration.export",
        key=key,
        request_payload={"declaration_id": str(declaration_id)},
        handler=handler,
    )
    return outcome.response


@router.post("/declarations/{declaration_id}/submit", status_code=status.HTTP_202_ACCEPTED)
async def submit(
    declaration_id: uuid.UUID,
    scope: DeclarationScope,
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
    key: Annotated[str, Depends(idempotency_key)] = "",
) -> dict:
    workspace_id, session = scope.workspace_id, scope.session

    async def handler() -> tuple[dict, int, uuid.UUID | None, str | None]:
        declaration, attempt = await declaration_service.submit_declaration(
            session,
            workspace_id=workspace_id,
            actor_user_id=principal.user_id,
            declaration_id=declaration_id,
            idempotency_key=key,
        )
        return (
            _declaration_view(
                declaration,
                {
                    "submission_attempt_id": str(attempt.id),
                    "job_reference": f"/api/v1/jobs/{declaration_id}",
                    "note": (
                        "Succesul transportului nu este acceptare: recipisa ANAF este "
                        "necesară pentru starea 'accepted'."
                    ),
                },
            ),
            202,
            declaration_id,
            "declaration",
        )

    outcome = await execute_idempotent(
        session,
        workspace_id=workspace_id,
        user_id=principal.user_id,
        action="declaration.submit",
        key=key,
        request_payload={"declaration_id": str(declaration_id)},
        handler=handler,
    )
    return outcome.response


@router.post("/declarations/{declaration_id}/receipts", status_code=status.HTTP_201_CREATED)
async def import_receipt(
    declaration_id: uuid.UUID,
    payload: ReceiptImport,
    scope: DeclarationScope,
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
) -> dict:
    workspace_id, session = scope.workspace_id, scope.session
    receipt = await declaration_service.import_receipt(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        declaration_id=declaration_id,
        payload=payload.payload,
        receipt_bytes=payload.receipt_xml.encode("utf-8") if payload.receipt_xml else None,
        manually_reported=payload.manually_reported,
    )
    return {
        "id": str(receipt.id),
        "kind": receipt.kind,
        "verified": receipt.verified,
        "manually_reported": receipt.manually_reported,
        "rejection_errors": receipt.rejection_errors,
        "note": (
            "Acceptarea raportată manual rămâne neverificată până când recipisa este "
            "atașată și verificată față de contribuabil, formular, perioadă și artefact."
            if receipt.manually_reported
            else None
        ),
    }


@router.get("/declarations/{declaration_id}/trace")
async def trace(
    declaration_id: uuid.UUID,
    scope: DeclarationScope,
) -> dict:
    session = scope.session
    declaration = await declaration_service.load_declaration(session, declaration_id)
    return await declaration_service.declaration_trace(
        session, workspace_id=declaration.workspace_id, declaration_id=declaration_id
    )


@router.get("/declarations/{declaration_id}/artifacts")
async def artifacts(
    declaration_id: uuid.UUID,
    scope: DeclarationScope,
) -> dict:
    session = scope.session
    declaration = await declaration_service.load_declaration(session, declaration_id)
    return {
        "items": await declaration_service.declaration_artifacts(
            session, workspace_id=declaration.workspace_id, declaration_id=declaration_id
        )
    }


@router.get("/declarations/{declaration_id}/artifacts/latest")
async def download_latest_artifact(
    declaration_id: uuid.UUID,
    scope: DeclarationScope,
    principal: Annotated[Principal, Depends(require_scope("pfa.read"))],
) -> Response:
    """Authenticated download: the file is streamed, never a public URL."""
    session = scope.session
    declaration = await declaration_service.load_declaration(session, declaration_id)
    version = await declaration_service.latest_version(session, declaration_id=declaration_id)
    if version is None or version.artifact_object_key is None:
        raise NotFound("Nu există un artefact generat pentru această declarație.")
    data = ObjectStore.from_settings().get(version.artifact_object_key)
    filename = f"{declaration.form_code}-{declaration.income_year}.xml"
    return Response(
        content=data,
        media_type="application/xml",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Artifact-Sha256": version.artifact_sha256 or "",
            "Cache-Control": "no-store",
        },
    )


@router.get("/declarations/{declaration_id}/receipts")
async def receipts(
    declaration_id: uuid.UUID,
    scope: DeclarationScope,
) -> dict:
    session = scope.session
    declaration = await declaration_service.load_declaration(session, declaration_id)
    return {
        "items": await declaration_service.declaration_receipts(
            session, workspace_id=declaration.workspace_id, declaration_id=declaration_id
        )
    }


@router.get("/jobs/{job_id}")
async def job_status(
    job_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_scope("pfa.read"))],
    scope: JobScope,
) -> dict:
    """Poll durable job status with workspace membership checks."""
    job = await jobs.get_job(scope.session, job_id)
    return {
        "id": str(job.id),
        "kind": job.kind,
        "status": str(job.status),
        "attempts": job.attempts,
        "result": job.result,
        "last_error": job.last_error,
        "created_at": job.created_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


@router.get("/files/{object_key:path}")
async def download_object(
    object_key: str,
    principal: Annotated[Principal, Depends(require_scope("pfa.read"))],
    session: UserSession,
) -> Response:
    """Protected file streaming.

    Object keys are namespaced by workspace (``workspace/<id>/...``); the
    membership check happens before any bytes are read.
    """
    parts = object_key.split("/")
    if len(parts) < 3 or parts[0] != "workspace":
        raise NotFound("Fișierul nu există.")
    try:
        workspace_id = uuid.UUID(parts[1])
    except ValueError as exc:
        raise NotFound("Fișierul nu există.") from exc
    if not await tenancy.is_active_member(
        session, workspace_id=workspace_id, user_id=principal.user_id
    ):
        raise PermissionDenied("Nu aveți acces la acest fișier.")
    store = ObjectStore.from_settings()
    data = store.get(object_key)
    filename = object_key.rsplit("/", 1)[-1]
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
