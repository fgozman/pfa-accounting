"""Declaration lifecycle.

State machine::

    draft → calculated → validated → approved → exported or queued
          → submitted → accepted / rejected
    submission_unknown on upload timeout

Rules enforced here:
* every declaration stores an immutable input snapshot, form/schema version,
  fiscal rule-pack version, calculation trace, artifact hash, approval and
  submission actors, remote reference, raw response and receipt;
* a correction creates a linked new version;
* changing input after approval invalidates that approval;
* acceptance requires receipt evidence bound to taxpayer, form, period and
  artifact — a successful HTTP response is not acceptance.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from pfa import clock
from pfa.config import get_settings
from pfa.errors import (
    BlockingInputs,
    CapabilityUnavailable,
    Conflict,
    NotFound,
    StateTransitionError,
    ValidationFailed,
)
from pfa.fiscal.packs import RulePackError, get_pack, get_pack_by_code
from pfa.models.anaf import ConnectorCapability, Receipt, SubmissionAttempt
from pfa.models.base import DeclarationStatus
from pfa.models.compliance import (
    CalculationRun,
    Declaration,
    DeclarationVersion,
    FormVersion,
    Obligation,
)
from pfa.repo import compliance as compliance_repo
from pfa.services import form_schemas
from pfa.services import notifications as notifications_service
from pfa.services.audit import emit_event, enqueue_job, record_audit
from pfa.services.declarations import (
    SNAPSHOT_BUILDERS,
    CalculationOutcome,
    get_adapter,
)
from pfa.services.storage import ObjectStore
from pfa.services.xmlutil import stamp_workpaper

TERMINAL_STATES = {DeclarationStatus.ACCEPTED}
#: an artifact may be generated before or after approval; approval always refers
#: to one exact artifact of the current version
APPROVABLE_STATES = {
    DeclarationStatus.VALIDATED,
    DeclarationStatus.EXPORTED,
    DeclarationStatus.AWAITING_EXTERNAL_FILING,
}
EDITABLE_STATES = {
    DeclarationStatus.DRAFT,
    DeclarationStatus.CALCULATED,
    DeclarationStatus.VALIDATED,
    DeclarationStatus.APPROVED,
    DeclarationStatus.EXPORTED,
    DeclarationStatus.AWAITING_EXTERNAL_FILING,
    DeclarationStatus.REJECTED,
}


def jsonable(value: object) -> object:
    """Recursively convert a payload into strict JSON types.

    Monetary values become decimal *strings*, never floats, so an input snapshot
    preserves exact precision when it is stored and re-read.
    """
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, (date,)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        return jsonable(as_dict())
    if hasattr(value, "__dataclass_fields__"):
        import dataclasses

        return jsonable(dataclasses.asdict(value))
    return value


def _sha256(payload: object) -> str:
    canonical = json.dumps(
        jsonable(payload), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def get_declaration(
    session: AsyncSession, *, workspace_id: uuid.UUID, declaration_id: uuid.UUID
) -> Declaration:
    declaration = (
        await session.execute(
            select(Declaration).where(
                Declaration.id == declaration_id,
                Declaration.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if declaration is None:
        raise NotFound("Declarația nu există în acest spațiu de lucru.")
    return declaration


async def declaration_workspace(
    session: AsyncSession, declaration_id: uuid.UUID
) -> uuid.UUID | None:
    """Resolve a declaration's workspace without a tenant context.

    Goes through a SECURITY DEFINER helper that returns the identifier only: no
    declaration content is exposed before membership is checked.
    """
    return await session.scalar(
        text("SELECT app.declaration_workspace(:declaration)"),
        {"declaration": declaration_id},
    )


async def load_declaration(session: AsyncSession, declaration_id: uuid.UUID) -> Declaration:
    """A declaration visible in the current tenant context (row-level security)."""
    declaration = await session.get(Declaration, declaration_id)
    if declaration is None:
        raise NotFound("Declarația nu există.")
    return declaration


async def latest_version(
    session: AsyncSession, *, declaration_id: uuid.UUID
) -> DeclarationVersion | None:
    return (
        await session.execute(
            select(DeclarationVersion)
            .where(DeclarationVersion.declaration_id == declaration_id)
            .order_by(DeclarationVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def latest_versions_for(
    session: AsyncSession, *, declaration_ids: list[uuid.UUID]
) -> dict[uuid.UUID, DeclarationVersion]:
    """The newest version of each declaration, in a single query.

    The declarations list shows what was calculated (values, trace, artifact), so the
    review panel must not depend on how the page loaded its data.
    """
    if not declaration_ids:
        return {}
    rows = (
        await session.execute(
            select(DeclarationVersion)
            .where(DeclarationVersion.declaration_id.in_(declaration_ids))
            .order_by(DeclarationVersion.declaration_id, DeclarationVersion.version.desc())
        )
    )
    latest: dict[uuid.UUID, DeclarationVersion] = {}
    for version in rows.scalars().all():
        latest.setdefault(version.declaration_id, version)  # first per declaration is the newest
    return latest


async def prepare_declaration(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    form_code: str,
    income_year: int,
    period_start: date,
    period_end: date,
    taxpayer_id: uuid.UUID | None = None,
    taxpayer_identity: dict | None = None,
    taxpayer_key: str | None = None,
    correction_of: uuid.UUID | None = None,
) -> Declaration:
    """Create a draft declaration.

    D212 belongs to an individual taxpayer, not to a PFA CUI: two conflicting
    annual returns for the same taxpayer and year are rejected.
    """
    if form_code == "EFACTURA":
        raise CapabilityUnavailable(
            "RO e-Factura nu este o declarație: factura se transmite prin e-Factura, nu se "
            "pregătește o declarație pentru ea."
        )
    if form_code not in SNAPSHOT_BUILDERS:
        raise CapabilityUnavailable(
            f"Formularul {form_code} nu are un adaptor de calcul implementat în această versiune."
        )
    if period_end < period_start:
        raise ValidationFailed("Perioada declarației este invalidă.")

    resolved_taxpayer_key = taxpayer_key or (str(taxpayer_id) if taxpayer_id else None)
    if form_code == "D212":
        if not resolved_taxpayer_key:
            raise BlockingInputs(
                ["taxpayer_id"],
                "D212 aparține unei persoane fizice: identitatea contribuabilului este obligatorie.",
            )
        conflicting = (
            await session.execute(
                select(Declaration).where(
                    Declaration.taxpayer_key == resolved_taxpayer_key,
                    Declaration.form_code == "D212",
                    Declaration.income_year == income_year,
                )
            )
        ).scalar_one_or_none()
        if conflicting is not None:
            raise Conflict(
                "Există deja o declarație unică pentru acest contribuabil și an de venit. "
                "Folosiți o declarație rectificativă legată, nu o a doua declarație.",
                details={"existing_declaration_id": str(conflicting.id)},
            )

    form_version = (
        await session.execute(
            select(FormVersion).where(
                FormVersion.form_code == form_code, FormVersion.income_year == income_year
            )
        )
    ).scalars().first()

    declaration = Declaration(
        workspace_id=workspace_id,
        taxpayer_id=taxpayer_id,
        form_code=form_code,
        income_year=income_year,
        period_start=period_start,
        period_end=period_end,
        status=DeclarationStatus.DRAFT,
        form_version_id=form_version.id if form_version else None,
        taxpayer_identity=taxpayer_identity or {},
        taxpayer_key=resolved_taxpayer_key,
        correction_of_declaration_id=correction_of,
        estimate_only=not bool(form_version and form_version.published),
        created_by_user_id=actor_user_id,
    )
    session.add(declaration)
    await session.flush()

    # Link the obligation(s) this declaration addresses so the dashboard opens the
    # prepared draft instead of preparing a duplicate.
    linked_obligations = (
        await session.execute(
            select(Obligation).where(
                Obligation.workspace_id == workspace_id,
                Obligation.form_code == form_code,
                Obligation.period_end >= period_start,
                Obligation.period_start <= period_end,
            )
        )
    ).scalars().all()
    for obligation in linked_obligations:
        obligation.declaration_id = declaration.id

    if correction_of is not None:
        original = await get_declaration(
            session, workspace_id=workspace_id, declaration_id=correction_of
        )
        if original.form_code != form_code:
            raise ValidationFailed("Corecția trebuie să aibă același formular.")
        await record_audit(
            session,
            action="declaration.correction_opened",
            entity_type="declaration",
            entity_id=declaration.id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            after={"corrects": str(correction_of)},
        )

    await record_audit(
        session,
        action="declaration.prepared",
        entity_type="declaration",
        entity_id=declaration.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={"form_code": form_code, "income_year": income_year},
    )
    return declaration


async def _resolve_pack(declaration: Declaration) -> object | None:
    try:
        if declaration.rule_pack_code:
            return get_pack_by_code(declaration.rule_pack_code)
        return get_pack(declaration.income_year)
    except RulePackError:
        return None


async def calculate_declaration(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    declaration_id: uuid.UUID,
) -> Declaration:
    declaration = await get_declaration(
        session, workspace_id=workspace_id, declaration_id=declaration_id
    )
    if declaration.status in TERMINAL_STATES:
        raise Conflict(
            "Declarațiile acceptate sunt imutabile; deschideți o declarație rectificativă."
        )
    if declaration.status in {
        DeclarationStatus.QUEUED,
        DeclarationStatus.SUBMITTED,
        DeclarationStatus.SUBMISSION_UNKNOWN,
    }:
        raise StateTransitionError(
            "Declarația este în curs de transmitere; așteptați rezultatul."
        )

    builder = SNAPSHOT_BUILDERS[declaration.form_code]
    adapter = get_adapter(declaration.form_code)
    snapshot = jsonable(await builder(session, declaration=declaration))
    pack = await _resolve_pack(declaration)
    if pack is None:
        raise CapabilityUnavailable(
            f"Nu există un pachet de reguli aprobat pentru anul de venit {declaration.income_year}. "
            "Calculul nu poate produce valori autoritare."
        )
    outcome: CalculationOutcome = adapter.calculate(snapshot, pack)  # type: ignore[arg-type]

    snapshot_sha = _sha256(snapshot)
    run = CalculationRun(
        workspace_id=workspace_id,
        declaration_id=declaration.id,
        taxpayer_id=declaration.taxpayer_id,
        form_code=declaration.form_code,
        income_year=declaration.income_year,
        rule_pack_code=outcome.rule_pack_code,
        rule_pack_version=outcome.rule_pack_version,
        engine_version="fiscal-engine-1.0.0",
        input_snapshot=snapshot,
        input_snapshot_sha256=snapshot_sha,
        result=jsonable(outcome.calculated_values),
        trace=jsonable(outcome.trace),
        unresolved=jsonable(outcome.unresolved),
        created_by_user_id=actor_user_id,
    )
    session.add(run)
    await session.flush()

    previous = await latest_version(session, declaration_id=declaration.id)
    approval_invalidated = (
        declaration.approved_at is not None
        and previous is not None
        and previous.input_snapshot_sha256 != snapshot_sha
    )
    if approval_invalidated:
        declaration.approved_at = None
        declaration.approved_by_user_id = None
        await emit_event(
            session,
            event_type="declaration.approval_invalidated",
            workspace_id=workspace_id,
            payload={"declaration_id": str(declaration.id)},
            dedupe_key=f"approval-invalidated:{declaration.id}:{snapshot_sha[:12]}",
        )

    version_number = declaration.current_version + 1
    version = DeclarationVersion(
        workspace_id=workspace_id,
        declaration_id=declaration.id,
        version=version_number,
        status=DeclarationStatus.CALCULATED,
        input_snapshot=snapshot,
        input_snapshot_sha256=snapshot_sha,
        calculation=jsonable(outcome.calculated_values),
        trace=jsonable(outcome.trace),
        calculated_values=jsonable(outcome.calculated_values),
        form_schema_version=outcome.form_schema_version,
        rule_pack_code=outcome.rule_pack_code,
        rule_pack_version=outcome.rule_pack_version,
        created_by_user_id=actor_user_id,
    )
    session.add(version)
    declaration.current_version = version_number
    declaration.status = DeclarationStatus.CALCULATED
    declaration.rule_pack_code = outcome.rule_pack_code
    declaration.rule_pack_version = outcome.rule_pack_version
    await session.flush()
    await record_audit(
        session,
        action="declaration.calculated",
        entity_type="declaration",
        entity_id=declaration.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={"version": version_number, "unresolved": outcome.unresolved},
    )
    return declaration


async def validate_declaration(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    declaration_id: uuid.UUID,
) -> Declaration:
    declaration = await get_declaration(
        session, workspace_id=workspace_id, declaration_id=declaration_id
    )
    if declaration.status in TERMINAL_STATES:
        raise Conflict("Declarațiile acceptate sunt imutabile.")
    if declaration.status == DeclarationStatus.DRAFT:
        raise StateTransitionError("Calculați declarația înainte de validare.")
    version = await latest_version(session, declaration_id=declaration.id)
    if version is None:
        raise StateTransitionError("Nu există o versiune calculată.")
    adapter = get_adapter(declaration.form_code)
    required_missing = adapter.required_inputs(version.input_snapshot)
    # The unresolved inputs live on the calculation run, not on the version's calculated
    # values; reading them from the version silently let an unknown classification pass
    # validation instead of blocking it.
    run = (
        await session.execute(
            select(CalculationRun)
            .where(CalculationRun.declaration_id == declaration.id)
            .order_by(CalculationRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    calculation_unresolved = [key for key in ((run.unresolved if run else None) or []) if key]
    blocking = sorted(set(required_missing) | set(calculation_unresolved))
    if blocking:
        raise BlockingInputs(
            blocking,
            "Validarea este blocată: există informații obligatorii necunoscute sau "
            "clasificări nedeterminate.",
        )
    declaration.status = DeclarationStatus.VALIDATED
    version.status = DeclarationStatus.VALIDATED
    await session.flush()
    await record_audit(
        session,
        action="declaration.validated",
        entity_type="declaration",
        entity_id=declaration.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={"version": version.version},
    )
    return declaration


#: declarations that may no longer be deleted: an approval fixes an immutable artifact,
#: and an in-flight or accepted filing is a compliance record
UNDELETABLE_DECLARATION_STATUSES = (
    DeclarationStatus.QUEUED,
    DeclarationStatus.SUBMITTED,
    DeclarationStatus.SUBMISSION_UNKNOWN,
    DeclarationStatus.ACCEPTED,
)


async def delete_declaration(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    declaration_id: uuid.UUID,
) -> dict:
    """Delete a declaration that was neither approved nor filed.

    A generated or filed declaration is a compliance record: it is corrected by a
    rectifying declaration, never deleted.  Everything before that — drafts and
    workpapers, including an exported preview — can be removed together with its
    versions, calculation runs, receipts and source grants; linked obligations stop
    pointing at it but keep their own history.
    """
    declaration = await get_declaration(
        session, workspace_id=workspace_id, declaration_id=declaration_id
    )
    if declaration.approved_at is not None:
        raise Conflict(
            "Declarația este aprobată: aprobarea se referă la un fișier imutabil. "
            "Folosiți o declarație rectificativă."
        )
    if declaration.status in UNDELETABLE_DECLARATION_STATUSES:
        raise Conflict(
            "Declarația este în curs de transmitere sau acceptată și nu poate fi ștearsă. "
            "Folosiți o declarație rectificativă."
        )
    if await compliance_repo.count_declaration_corrections(
        session, declaration_id=declaration.id
    ):
        raise Conflict(
            "Există o declarație rectificativă legată de aceasta; ștergeți-o mai întâi pe aceea."
        )
    await record_audit(
        session,
        action="declaration.deleted",
        entity_type="declaration",
        entity_id=declaration.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        before={
            "form_code": declaration.form_code,
            "income_year": declaration.income_year,
            "period_start": declaration.period_start.isoformat(),
            "period_end": declaration.period_end.isoformat(),
            "status": str(declaration.status),
        },
    )
    await compliance_repo.delete_declaration_dependents(
        session, workspace_id=workspace_id, declaration_id=declaration.id
    )
    await compliance_repo.delete_declaration(
        session, workspace_id=workspace_id, declaration_id=declaration.id
    )

    # The user still has to file it: reopen the obligation the declaration addressed and
    # its reminders, then re-evaluate so an actionable item exists on the dashboard again.
    await _reopen_obligations(session, declaration=declaration)
    from pfa.services import obligations as obligations_service

    rows = await obligations_service.evaluate_obligations(
        session, workspace_id=workspace_id, income_year=declaration.income_year
    )
    await notifications_service.evaluate_notifications(
        session, workspace_id=workspace_id, obligations=rows
    )
    return {"status": "deleted", "declaration_id": str(declaration.id)}


async def approve_declaration(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    declaration_id: uuid.UUID,
    declared_taxpayer: dict | None = None,
) -> Declaration:
    declaration = await get_declaration(
        session, workspace_id=workspace_id, declaration_id=declaration_id
    )
    if declaration.status not in APPROVABLE_STATES:
        raise StateTransitionError(
            "Doar declarațiile validate, cu artefactul generat, pot fi aprobate."
        )
    version = await latest_version(session, declaration_id=declaration.id)
    if version is None or version.artifact_sha256 is None:
        raise StateTransitionError(
            "Generați artefactul înainte de aprobare: aprobarea se referă la un fișier exact."
        )
    if declared_taxpayer is not None:
        declaration.taxpayer_identity = declared_taxpayer
    declaration.approved_at = clock.now()
    declaration.approved_by_user_id = actor_user_id
    declaration.status = DeclarationStatus.APPROVED
    version.status = DeclarationStatus.APPROVED
    await session.flush()
    await record_audit(
        session,
        action="declaration.approved",
        entity_type="declaration",
        entity_id=declaration.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={
            "version": version.version,
            "artifact_sha256": version.artifact_sha256,
            "taxpayer_identity": declaration.taxpayer_identity,
        },
    )
    return declaration


async def export_declaration(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    declaration_id: uuid.UUID,
    store: ObjectStore | None = None,
) -> tuple[Declaration, dict]:
    declaration = await get_declaration(
        session, workspace_id=workspace_id, declaration_id=declaration_id
    )
    if declaration.status == DeclarationStatus.DRAFT:
        raise StateTransitionError("Calculați declarația înainte de generare.")
    if declaration.status in TERMINAL_STATES:
        raise Conflict("Declarațiile acceptate sunt imutabile.")

    version = await latest_version(session, declaration_id=declaration.id)
    if version is None:
        raise StateTransitionError("Nu există o versiune calculată.")
    adapter = get_adapter(declaration.form_code)
    form_version = (
        await session.get(FormVersion, declaration.form_version_id)
        if declaration.form_version_id
        else None
    )
    await _resolve_pack(declaration)
    outcome = CalculationOutcome(
        calculated_values=version.calculated_values,
        trace=list(version.trace or []),
        unresolved=[],
        warnings=[],
        rule_pack_code=version.rule_pack_code,
        rule_pack_version=version.rule_pack_version,
    )
    artifact = adapter.render(version.input_snapshot, outcome, form_version)

    # An artifact is only ever presented as official if it satisfies the official
    # ANAF XSD for its form; otherwise it is downgraded to a labelled workpaper.
    schema = form_schemas.schema_for(declaration.form_code)
    schema_errors: list[str] = []
    if schema is not None:
        schema_errors = form_schemas.validate_form(artifact.data, declaration.form_code)
        version.form_schema_version = schema.schema_id
        if schema_errors and not artifact.is_preview:
            artifact.is_preview = True
            suffix = f"nu respectă schema oficială ANAF ({schema.schema_id})"
            artifact.label_ro = (
                f"{artifact.label_ro} — {suffix}" if artifact.label_ro else f"Document de lucru — {suffix}"
            )
            artifact.data = stamp_workpaper(
                artifact.data,
                reason=(
                    "Document de lucru: fișierul nu respectă schema oficială ANAF "
                    f"({schema.schema_id}) și nu poate fi depus."
                ),
            )
    previous_hash = version.artifact_sha256
    store = store or ObjectStore.from_settings()
    stored = store.put(
        artifact.data,
        content_type="application/xml",
        namespace=f"workspace/{workspace_id}/declarations/{declaration.id}",
    )
    version.artifact_object_key = stored.key
    version.artifact_sha256 = stored.sha256
    version.artifact_format = artifact.artifact_format
    version.artifact_is_preview = artifact.is_preview
    declaration.generated_at = clock.now()
    if (
        declaration.approved_at is not None
        and previous_hash is not None
        and previous_hash != stored.sha256
    ):
        # the artifact changed after approval: the approval no longer refers to a
        # specific immutable file, so it is invalidated
        declaration.approved_at = None
        declaration.approved_by_user_id = None
        await emit_event(
            session,
            event_type="declaration.approval_invalidated",
            workspace_id=workspace_id,
            payload={"declaration_id": str(declaration.id), "reason": "artifact_changed"},
            dedupe_key=f"approval-invalidated-artifact:{declaration.id}:{stored.sha256[:12]}",
        )
    if artifact.is_preview:
        declaration.status = DeclarationStatus.AWAITING_EXTERNAL_FILING
    else:
        declaration.status = DeclarationStatus.EXPORTED
    capabilities = _capabilities_from_form_version(declaration.form_code, form_version)
    declaration.submission_available = capabilities["submit"]
    declaration.submission_unavailable_reason = capabilities["unavailable_reason"]
    await session.flush()
    await record_audit(
        session,
        action="declaration.artifact_generated",
        entity_type="declaration",
        entity_id=declaration.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={
            "version": version.version,
            "sha256": stored.sha256,
            "is_preview": artifact.is_preview,
            "format": artifact.artifact_format,
            "official_schema": schema.schema_id if schema else None,
            "schema_valid": not schema_errors,
        },
    )
    return declaration, {
        "object_key": stored.key,
        "sha256": stored.sha256,
        "artifact_format": artifact.artifact_format,
        "is_preview": artifact.is_preview,
        "label_ro": artifact.label_ro,
        "size_bytes": stored.size_bytes,
        "official_schema": schema.schema_id if schema else None,
        "schema_valid": not schema_errors,
        "schema_errors": schema_errors[:10],
    }


def _capabilities_from_form_version(form_code: str, form_version: FormVersion | None) -> dict:
    from pfa.services.obligations import capabilities_for

    return capabilities_for(form_code, income_year=form_version.income_year if form_version else 0, form_version=form_version)


async def available_channels(
    session: AsyncSession, *, form_code: str, environment: str | None = None
) -> list[ConnectorCapability]:
    settings = get_settings()
    environment = environment or settings.anaf_environment
    return list(
        (
            await session.execute(
                select(ConnectorCapability).where(
                    ConnectorCapability.form_code == form_code,
                    ConnectorCapability.environment == environment,
                    ConnectorCapability.enabled.is_(True),
                )
            )
        ).scalars().all()
    )


async def submit_declaration(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    declaration_id: uuid.UUID,
    idempotency_key: str,
) -> tuple[Declaration, SubmissionAttempt]:
    """Queue a submission for an approved artifact.

    Automated submission stays disabled unless a *verified* channel capability
    exists; generation and export remain available with an explanation.
    """
    declaration = await get_declaration(
        session, workspace_id=workspace_id, declaration_id=declaration_id
    )
    if declaration.status != DeclarationStatus.APPROVED:
        raise StateTransitionError(
            "Transmiterea necesită aprobarea prealabilă a artefactului imutabil."
        )
    version = await latest_version(session, declaration_id=declaration.id)
    if version is None or version.artifact_sha256 is None or version.artifact_object_key is None:
        raise StateTransitionError("Artefactul nu a fost generat.")
    if version.artifact_is_preview:
        raise CapabilityUnavailable(
            "Artefactul este marcat ca previzualizare/document de lucru și nu poate fi depus."
        )
    capabilities = await available_channels(session, form_code=declaration.form_code)
    if not capabilities:
        raise CapabilityUnavailable(
            declaration.submission_unavailable_reason
            or (
                "Nu există un canal oficial verificat pentru acest formular: folosiți "
                "exportul validat și încărcarea în SPV, apoi importați recipisa."
            )
        )
    channel = capabilities[0]
    attempt_number = (
        await session.scalar(
            select(SubmissionAttempt.attempt_number)
            .where(
                SubmissionAttempt.workspace_id == workspace_id,
                SubmissionAttempt.declaration_id == declaration.id,
            )
            .order_by(SubmissionAttempt.attempt_number.desc())
            .limit(1)
        )
        or 0
    ) + 1
    attempt = SubmissionAttempt(
        workspace_id=workspace_id,
        declaration_id=declaration.id,
        declaration_version=version.version,
        channel=channel.channel,
        status="queued",
        attempt_number=attempt_number,
        idempotency_key=idempotency_key,
        request_sha256=version.artifact_sha256,
        request_object_key=version.artifact_object_key,
    )
    session.add(attempt)
    declaration.status = DeclarationStatus.QUEUED
    declaration.submitted_at = clock.now()
    await session.flush()
    job = await enqueue_job(
        session,
        kind="anaf.submit_declaration",
        workspace_id=workspace_id,
        payload={"submission_attempt_id": str(attempt.id)},
        logical_key=f"submit:{declaration.id}:{version.version}",
        created_by_user_id=actor_user_id,
    )
    await record_audit(
        session,
        action="declaration.submission_queued",
        entity_type="declaration",
        entity_id=declaration.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={"attempt": attempt_number, "channel": str(channel.channel), "job": str(job.id)},
    )
    return declaration, attempt


async def record_submission_result(
    session: AsyncSession,
    *,
    attempt: SubmissionAttempt,
    outcome: str,
    remote_reference: str | None = None,
    error_class: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    raw_response: dict | None = None,
) -> Declaration:
    """Apply a transport outcome.

    ``succeeded`` means the artifact was accepted for processing, **not** that
    ANAF accepted it.  ``timeout`` produces ``submission_unknown`` and requires
    reconciliation instead of a blind resend.
    """
    declaration = (
        await session.execute(
            select(Declaration).where(Declaration.id == attempt.declaration_id)
        )
    ).scalar_one()
    attempt.status = outcome
    attempt.remote_reference = remote_reference
    attempt.error_class = error_class
    attempt.error_code = error_code
    attempt.error_message = error_message
    attempt.raw_response = raw_response
    attempt.finished_at = clock.now()
    if outcome == "succeeded":
        declaration.status = DeclarationStatus.SUBMITTED
    elif outcome == "timeout":
        declaration.status = DeclarationStatus.SUBMISSION_UNKNOWN
    elif outcome == "failed":
        if error_class == "fiscal_validation":
            declaration.status = DeclarationStatus.REJECTED
            declaration.rejected_at = clock.now()
            await _reopen_obligations(session, declaration=declaration)
        else:
            declaration.status = DeclarationStatus.APPROVED
    await session.flush()
    await record_audit(
        session,
        action="declaration.submission_result",
        entity_type="declaration",
        entity_id=declaration.id,
        workspace_id=declaration.workspace_id,
        actor_kind="worker",
        after={
            "outcome": outcome,
            "remote_reference": remote_reference,
            "error_class": error_class,
        },
    )
    return declaration


async def import_receipt(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    declaration_id: uuid.UUID,
    payload: dict,
    receipt_bytes: bytes | None = None,
    manually_reported: bool = False,
    store: ObjectStore | None = None,
) -> Receipt:
    """Import ANAF receipt evidence and bind it to taxpayer, form, period and artifact."""
    declaration = await get_declaration(
        session, workspace_id=workspace_id, declaration_id=declaration_id
    )
    version = await latest_version(session, declaration_id=declaration.id)
    if version is None:
        raise StateTransitionError("Nu există o versiune generată pentru această declarație.")
    adapter = get_adapter(declaration.form_code)
    interpretation = adapter.interpret_receipt(payload)

    identity_values = {str(value) for value in (declaration.taxpayer_identity or {}).values()}
    taxpayer_matches = (
        None
        if interpretation.taxpayer_value is None
        else interpretation.taxpayer_value in identity_values
        or interpretation.taxpayer_value == str(declaration.taxpayer_key)
    )
    form_matches = (
        None
        if interpretation.form_value is None
        else interpretation.form_value.upper() == declaration.form_code.upper()
    )
    period_matches = None
    if interpretation.period_value:
        period_matches = (
            interpretation.period_value == declaration.period_end.isoformat()
            or interpretation.period_value == declaration.period_start.isoformat()
        )
    artifact_matches = (
        None
        if interpretation.artifact_hash is None
        else interpretation.artifact_hash == version.artifact_sha256
    )

    store = store or ObjectStore.from_settings()
    object_key = None
    sha256 = None
    if receipt_bytes is not None:
        stored = store.put(
            receipt_bytes,
            content_type="application/xml",
            namespace=f"workspace/{workspace_id}/receipts",
        )
        object_key, sha256 = stored.key, stored.sha256

    verified = bool(
        interpretation.accepted
        and not manually_reported
        and taxpayer_matches is not False
        and form_matches is not False
        and period_matches is not False
        and artifact_matches is not False
        and taxpayer_matches is not None
    )
    receipt = Receipt(
        workspace_id=workspace_id,
        declaration_id=declaration.id,
        declaration_version=version.version,
        submission_attempt_id=None,
        kind="acceptance" if interpretation.accepted else "rejection",
        remote_reference=interpretation.remote_reference,
        artifact_sha256=version.artifact_sha256,
        taxpayer_matches=taxpayer_matches,
        form_matches=form_matches,
        period_matches=period_matches,
        artifact_matches=artifact_matches,
        verified=verified,
        verified_at=clock.now() if verified else None,
        verified_by_user_id=actor_user_id if verified and not manually_reported else None,
        manually_reported=manually_reported,
        object_key=object_key,
        sha256=sha256,
        rejection_errors=interpretation.rejection_errors or None,
        raw=payload,
    )
    session.add(receipt)
    await session.flush()

    if interpretation.accepted and verified:
        declaration.status = DeclarationStatus.ACCEPTED
        declaration.accepted_at = clock.now()
        await _resolve_obligations(session, declaration=declaration)
    elif not interpretation.accepted:
        declaration.status = DeclarationStatus.REJECTED
        declaration.rejected_at = clock.now()
        await _reopen_obligations(session, declaration=declaration)
    await session.flush()
    await record_audit(
        session,
        action="declaration.receipt_imported",
        entity_type="declaration",
        entity_id=declaration.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={
            "accepted": interpretation.accepted,
            "verified": verified,
            "manually_reported": manually_reported,
        },
    )
    return receipt


async def _resolve_obligations(session: AsyncSession, *, declaration: Declaration) -> None:
    obligations = (
        await session.execute(
            select(Obligation).where(
                Obligation.workspace_id == declaration.workspace_id,
                Obligation.form_code == declaration.form_code,
                Obligation.period_end >= declaration.period_start,
                Obligation.period_start <= declaration.period_end,
            )
        )
    ).scalars().all()
    for obligation in obligations:
        obligation.declaration_id = declaration.id
        await notifications_service.resolve_filing(session, obligation=obligation)


async def _reopen_obligations(session: AsyncSession, *, declaration: Declaration) -> None:
    obligations = (
        await session.execute(
            select(Obligation).where(
                Obligation.workspace_id == declaration.workspace_id,
                Obligation.form_code == declaration.form_code,
                Obligation.period_end >= declaration.period_start,
                Obligation.period_start <= declaration.period_end,
            )
        )
    ).scalars().all()
    for obligation in obligations:
        await notifications_service.reopen_after_rejection(session, obligation=obligation)


async def declaration_trace(
    session: AsyncSession, *, workspace_id: uuid.UUID, declaration_id: uuid.UUID
) -> dict:
    declaration = await get_declaration(
        session, workspace_id=workspace_id, declaration_id=declaration_id
    )
    versions = (
        await session.execute(
            select(DeclarationVersion)
            .where(DeclarationVersion.declaration_id == declaration.id)
            .order_by(DeclarationVersion.version)
        )
    ).scalars().all()
    runs = (
        await session.execute(
            select(CalculationRun)
            .where(CalculationRun.declaration_id == declaration.id)
            .order_by(CalculationRun.created_at)
        )
    ).scalars().all()
    return {
        "declaration_id": str(declaration.id),
        "form_code": declaration.form_code,
        "status": str(declaration.status),
        "rule_pack": {
            "code": declaration.rule_pack_code,
            "version": declaration.rule_pack_version,
        },
        "versions": [
            {
                "version_version": version.version,
                "created_at": version.created_at.isoformat(),
                "rule_pack_code": version.rule_pack_code,
                "rule_pack_version": version.rule_pack_version,
                "form_schema_version": version.form_schema_version,
                "input_snapshot_sha256": version.input_snapshot_sha256,
                "artifact_sha256": version.artifact_sha256,
                "artifact_is_preview": version.artifact_is_preview,
                "trace": version.trace,
            }
            for version in versions
        ],
        "calculation_runs": [
            {
                "id": str(run.id),
                "engine_version": run.engine_version,
                "rule_pack_code": run.rule_pack_code,
                "rule_pack_version": run.rule_pack_version,
                "unresolved": run.unresolved,
                "created_at": run.created_at.isoformat(),
            }
            for run in runs
        ],
    }


async def declaration_artifacts(
    session: AsyncSession, *, workspace_id: uuid.UUID, declaration_id: uuid.UUID
) -> list[dict]:
    declaration = await get_declaration(
        session, workspace_id=workspace_id, declaration_id=declaration_id
    )
    versions = (
        await session.execute(
            select(DeclarationVersion)
            .where(DeclarationVersion.declaration_id == declaration.id)
            .order_by(DeclarationVersion.version.desc())
        )
    ).scalars().all()
    return [
        {
            "version": version.version,
            "artifact_format": version.artifact_format,
            "artifact_object_key": version.artifact_object_key,
            "artifact_sha256": version.artifact_sha256,
            "is_preview": version.artifact_is_preview,
            "created_at": version.created_at.isoformat(),
        }
        for version in versions
        if version.artifact_object_key
    ]


async def declaration_receipts(
    session: AsyncSession, *, workspace_id: uuid.UUID, declaration_id: uuid.UUID
) -> list[dict]:
    declaration = await get_declaration(
        session, workspace_id=workspace_id, declaration_id=declaration_id
    )
    receipts = (
        await session.execute(
            select(Receipt)
            .where(Receipt.declaration_id == declaration.id)
            .order_by(Receipt.created_at.desc())
        )
    ).scalars().all()
    return [
        {
            "id": str(receipt.id),
            "kind": receipt.kind,
            "remote_reference": receipt.remote_reference,
            "verified": receipt.verified,
            "manually_reported": receipt.manually_reported,
            "verified_at": receipt.verified_at.isoformat() if receipt.verified_at else None,
            "rejection_errors": receipt.rejection_errors,
            "created_at": receipt.created_at.isoformat(),
        }
        for receipt in receipts
    ]


async def list_declarations(
    session: AsyncSession, *, workspace_id: uuid.UUID, limit: int = 100
) -> list[Declaration]:
    return list(
        (
            await session.execute(
                select(Declaration)
                .where(Declaration.workspace_id == workspace_id)
                .order_by(Declaration.period_end.desc(), Declaration.form_code)
                .limit(min(max(limit, 1), 200))
            )
        )
        .scalars()
        .all()
    )
