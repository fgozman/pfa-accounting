"""Durable worker: outbox dispatch, jobs, scheduled reconciliation.

Workers are restartable and idempotent.  They enumerate workspaces through a
SECURITY DEFINER function that returns identifiers only, then open a tenant
transaction for every unit of work so row-level security still applies.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import socket
import time
import uuid
from datetime import date

from opentelemetry.trace import SpanKind, Status, StatusCode, get_current_span
from sqlalchemy import text

from pfa import clock
from pfa.anaf import get_transport
from pfa.anaf.efactura import transmit_invoice
from pfa.config import get_settings
from pfa.db import Database, get_database
from pfa.models.base import JobStatus
from pfa.observability import context
from pfa.observability.logs import configure_logging, log_event
from pfa.observability.telemetry import configure_telemetry, tracer, work_duration
from pfa.repo import operations as operations_repo
from pfa.services import declaration_service
from pfa.services import notifications as notifications_service
from pfa.services import obligations as obligations_service
from pfa.services.audit import list_workspace_ids
from pfa.services.email import (
    mask_email,
    render_invitation_email,
    render_password_reset_email,
    render_reminder_email,
    render_verification_email,
    sink_from_settings,
)

WORKER_ID = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
BASE_BACKOFF_SECONDS = 5
MAX_BACKOFF_SECONDS = 3600


logger = logging.getLogger("pfa.worker")


def _mark_span_failed(exc: BaseException) -> None:
    span = get_current_span()
    span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))


def backoff_delay(attempts: int) -> float:
    """Bounded exponential backoff with jitter."""
    raw = min(BASE_BACKOFF_SECONDS * (2 ** max(attempts - 1, 0)), MAX_BACKOFF_SECONDS)
    return raw * (0.5 + random.random() * 0.5)


def mail_log_line(*, template: str, recipient: str, link: str | None, token: str, env: str) -> str:
    """Log line for a delivered message.

    In development/test the captured link is printed so it is discoverable in the
    ``make dev`` output (the local sink and the development mailbox already expose
    it).  Anywhere else a token or a token-bearing URL is a credential: only the
    template and a masked recipient are logged, never the secret.
    """
    if env in {"development", "test"}:
        detail = f"link={link}" if link else f"token={token}"
        return f"[mail:{template}] to={recipient} {detail}"
    return f"[mail:{template}] to={mask_email(recipient)} delivered"


class Worker:
    def __init__(self, database: Database | None = None) -> None:
        self.database = database or get_database()
        self.settings = get_settings()
        self.email = sink_from_settings(self.settings)
        self.running = True

    # -- outbox ---------------------------------------------------------
    async def process_outbox_once(self, *, batch: int = 20) -> int:
        async with self.database.session() as session:
            rows = (
                await session.execute(
                    text("SELECT * FROM app.lease_outbox(:worker, :batch, :lease)"),
                    {"worker": WORKER_ID, "batch": batch, "lease": 120},
                )
            ).mappings().all()
        for row in rows:
            # one span and one correlation id per event: its log lines and audit
            # rows are joined, and its duration feeds pfa.worker.work.duration
            await self._observed(
                f"outbox {row['event_type']}",
                f"outbox:{row['event_type']}",
                str(row["id"]),
                self._handle_outbox(dict(row)),
            )
        return len(rows)

    async def _observed(self, span_name: str, kind: str, correlation_id: str, work) -> None:
        bound = context.bind(correlation_id=correlation_id)
        started = time.perf_counter()
        outcome = "failed"
        try:
            with tracer.start_as_current_span(
                span_name,
                kind=SpanKind.CONSUMER,
                attributes={"pfa.work.kind": kind, "pfa.correlation_id": correlation_id},
            ):
                outcome = await work
        finally:
            work_duration.record(
                time.perf_counter() - started, {"pfa.work.kind": kind, "pfa.work.outcome": outcome}
            )
            context.reset(bound)

    async def _handle_outbox(self, row: dict) -> None:
        workspace_id = row.get("workspace_id")
        event_id = row["id"]
        event_type = row["event_type"]
        payload = row.get("payload") or {}
        try:
            if event_type.startswith("email."):
                await self._handle_email(event_type, payload)
            elif workspace_id is not None:
                async with self.database.tenant_session(
                    workspace_id, actor="worker"
                ) as session:
                    await self._handle_workspace_event(session, workspace_id, event_type, payload)
            async with self.database.worker_session() as session:
                await session.execute(
                    text(
                        "UPDATE outbox_events SET status = 'processed', processed_at = now(), "
                        "locked_by = NULL, locked_until = NULL, "
                        "payload = CASE WHEN :redact THEN payload - 'token' ELSE payload END "
                        "WHERE id = :id"
                    ),
                    {"id": event_id, "redact": event_type.startswith("email.")},
                )
            return "succeeded"
        except Exception as exc:
            attempts = int(row.get("attempts") or 0)
            _mark_span_failed(exc)
            log_event(
                logger,
                "outbox.failed",
                level=logging.ERROR if attempts >= 8 else logging.WARNING,
                exc_info=exc,
                attributes={
                    "pfa.outbox.event_id": str(event_id),
                    "pfa.outbox.event_type": event_type,
                    "pfa.workspace.id": str(workspace_id) if workspace_id else None,
                    "pfa.outbox.attempts": attempts,
                    "pfa.outbox.final": attempts >= 8,
                },
            )
            async with self.database.worker_session() as session:
                await session.execute(
                    text(
                        """
                        UPDATE outbox_events
                           SET status = CASE WHEN attempts >= 8 THEN 'failed' ELSE 'pending' END,
                               available_at = now() + (:delay || ' seconds')::interval,
                               locked_by = NULL,
                               locked_until = NULL,
                               last_error = :error
                         WHERE id = :id
                        """
                    ),
                    {
                        "id": event_id,
                        "delay": str(backoff_delay(int(row.get("attempts") or 0) + 1)),
                        "error": str(exc)[:1000],
                    },
                )
            return "failed"

    async def _handle_workspace_event(
        self, session, workspace_id: uuid.UUID, event_type: str, payload: dict
    ) -> None:
        if event_type in {"document.finalized", "payment.recorded", "asset.created"}:
            year = clock.local_now().year
            if payload.get("issue_date"):
                with contextlib.suppress(ValueError):
                    year = date.fromisoformat(str(payload["issue_date"])).year
            rows = await obligations_service.evaluate_obligations(
                session, workspace_id=workspace_id, income_year=year
            )
            # A newly entered late document must immediately reconsider historical
            # periods and possible correction obligations.
            previous_year = year - 1
            rows += await obligations_service.evaluate_obligations(
                session, workspace_id=workspace_id, income_year=previous_year
            )
            await notifications_service.evaluate_notifications(
                session, workspace_id=workspace_id, obligations=rows
            )
        elif event_type == "membership.removed":
            user_id = payload.get("user_id")
            if user_id:
                await session.execute(
                    text(
                        """
                        DELETE FROM notification_deliveries
                         WHERE workspace_id = :workspace_id
                           AND recipient_user_id = :user_id
                           AND status = 'pending'
                        """
                    ),
                    {"workspace_id": workspace_id, "user_id": user_id},
                )
        elif event_type == "declaration.approval_invalidated":
            pass

    async def _handle_email(self, event_type: str, payload: dict) -> None:
        to = payload.get("email")
        token = payload.get("token", "")
        if not to:
            return
        if event_type == "email.verification_requested":
            subject, body = render_verification_email(token, self.settings)
            template = "verification"
        elif event_type == "email.password_reset_requested":
            subject, body = render_password_reset_email(token, self.settings)
            template = "password_reset"
        elif event_type == "email.invitation":
            subject, body = render_invitation_email(
                token, payload.get("workspace_name", "PFA"), self.settings
            )
            template = "invitation"
        elif event_type == "email.reminder":
            subject, body = render_reminder_email(
                payload.get("title", "Termen fiscal"), payload.get("body", ""), self.settings
            )
            template = "reminder"
        else:
            return
        captured = self.email.send(
            to=to, subject=subject, body=body, template=template, metadata=payload
        )
        link = next((part for part in body.split() if part.startswith("http")), None)
        log_event(
            logger,
            "mail.delivered",
            # the link is shown in development/test only (see mail_log_line)
            message=mail_log_line(
                template=template,
                recipient=captured.to,
                link=link,
                token=token,
                env=self.settings.env,
            ),
            allow_secrets=True,
            attributes={
                "pfa.mail.template": template,
                "pfa.mail.recipient": mask_email(captured.to),
            },
        )

    # -- jobs -----------------------------------------------------------
    async def process_jobs_once(self, *, batch: int = 10) -> int:
        async with self.database.session() as session:
            rows = (
                await session.execute(
                    text("SELECT * FROM app.lease_jobs(:worker, :batch, :lease)"),
                    {"worker": WORKER_ID, "batch": batch, "lease": 300},
                )
            ).mappings().all()
        for row in rows:
            await self._observed(
                f"job {row['kind']}", f"job:{row['kind']}", str(row["id"]), self._handle_job(dict(row))
            )
        return len(rows)

    async def _handle_job(self, row: dict) -> None:
        job_id = row["id"]
        workspace_id = row["workspace_id"]
        kind = row["kind"]
        payload = row.get("payload") or {}
        attempts = int(row.get("attempts") or 0)
        max_attempts = int(row.get("max_attempts") or 8)
        started = time.perf_counter()
        job_attributes = {
            "pfa.job.id": str(job_id),
            "pfa.job.kind": kind,
            "pfa.workspace.id": str(workspace_id),
            "pfa.job.attempts": attempts,
        }
        try:
            async with self.database.tenant_session(workspace_id, actor="worker") as session:
                job = await operations_repo.get_job(session, job_id)
                if job is None:
                    return "skipped"
                result = await self._dispatch(session, workspace_id, kind, payload)
                await operations_repo.mark_job_succeeded(
                    session, job_id=job_id, result=result or {}, finished_at=clock.now()
                )
            log_event(
                logger,
                "job.succeeded",
                message=f"job {kind} succeeded",
                attributes={
                    **job_attributes,
                    "pfa.job.duration": round(time.perf_counter() - started, 6),
                },
            )
            return "succeeded"
        except Exception as exc:
            # the lease already counted this attempt
            dead = attempts >= max_attempts
            _mark_span_failed(exc)
            log_event(
                logger,
                "job.failed",
                level=logging.ERROR if dead else logging.WARNING,
                message=f"job {kind} failed" + (" permanently" if dead else ", will retry"),
                exc_info=exc,
                attributes={
                    **job_attributes,
                    "pfa.job.dead": dead,
                    "pfa.job.duration": round(time.perf_counter() - started, 6),
                },
            )
            async with self.database.worker_session() as session:
                await session.execute(
                    text(
                        """
                        UPDATE jobs
                           SET status = CAST(:status AS VARCHAR),
                               available_at = now() + (:delay || ' seconds')::interval,
                               leased_by = NULL,
                               lease_expires_at = NULL,
                               last_error = :error
                         WHERE id = :id
                        """
                    ),
                    {
                        "id": job_id,
                        "status": JobStatus.DEAD.value if dead else JobStatus.PENDING.value,
                        "delay": str(backoff_delay(attempts + 1)),
                        "error": str(exc)[:1000],
                    },
                )
            return "failed"

    async def _dispatch(
        self, session, workspace_id: uuid.UUID, kind: str, payload: dict
    ) -> dict | None:
        if kind == "obligations.reconcile":
            year = int(payload.get("income_year") or clock.local_now().year)
            rows = await obligations_service.evaluate_obligations(
                session, workspace_id=workspace_id, income_year=year
            )
            created = await notifications_service.evaluate_notifications(
                session, workspace_id=workspace_id, obligations=rows
            )
            return {"obligations": len(rows), "notifications": len(created)}

        if kind == "efactura.transmit":
            document_id = uuid.UUID(str(payload["document_id"]))
            outcome = await transmit_invoice(
                session,
                workspace_id=workspace_id,
                document_id=document_id,
                transport=get_transport(),
            )
            return {
                "ok": outcome.ok,
                "status": outcome.status,
                "remote_reference": outcome.remote_reference,
                "unknown": outcome.unknown,
            }

        if kind == "anaf.submit_declaration":
            attempt_id = uuid.UUID(str(payload["submission_attempt_id"]))
            from pfa.repo import anaf as anaf_repo

            attempt = await anaf_repo.get_submission_attempt(session, attempt_id)
            if attempt is None:
                return None
            version = await declaration_service.latest_version(
                session, declaration_id=attempt.declaration_id
            )
            if version is None or version.artifact_object_key is None:
                return {"skipped": "missing_artifact"}
            transport = get_transport()
            xml = __import__(
                "pfa.services.storage", fromlist=["ObjectStore"]
            ).ObjectStore.from_settings().get(version.artifact_object_key)
            from pfa.repo import compliance as compliance_repo

            declaration = await compliance_repo.get_declaration(
                session, attempt.declaration_id
            )
            response = transport.submit_declaration(
                xml,
                form_code=declaration.form_code,
                cif=str((declaration.taxpayer_identity or {}).get("cui", "")),
                caller_reference=attempt.idempotency_key,
            )
            if response.unknown:
                await declaration_service.record_submission_result(
                    session, attempt=attempt, outcome="timeout", raw_response=response.payload
                )
                return {"status": "submission_unknown"}
            if not response.ok:
                await declaration_service.record_submission_result(
                    session,
                    attempt=attempt,
                    outcome="failed",
                    error_class=response.error_class or "transport",
                    error_code=response.error_code,
                    error_message=response.error_message,
                    raw_response=response.payload,
                )
                return {"status": "failed"}
            payload_out = response.payload or {}
            accepted = str(payload_out.get("stare", "")).lower() in {"ok", "accepted"}
            if not accepted:
                await declaration_service.record_submission_result(
                    session,
                    attempt=attempt,
                    outcome="failed",
                    error_class="fiscal_validation",
                    error_code="rejected",
                    error_message="Fișierul a fost respins la validarea fiscală.",
                    raw_response=payload_out,
                    remote_reference=response.remote_reference,
                )
                return {"status": "rejected"}
            await declaration_service.record_submission_result(
                session,
                attempt=attempt,
                outcome="succeeded",
                remote_reference=response.remote_reference,
                raw_response=payload_out,
            )
            return {"status": "submitted", "remote_reference": response.remote_reference}

        if kind == "register.export":
            return {"status": "ready"}

        if kind == "anaf.sync":
            return {"synced": 0, "note": "Conector ANAF neactivat (driver fake/neconfigurat)."}

        if kind == "notifications.email":
            return {"sent": 0}

        return {"skipped": kind}

    # -- scheduled ------------------------------------------------------
    async def run_reconciliation(self, *, as_of: date | None = None) -> int:
        """At-least-hourly evaluation using Europe/Bucharest business dates."""
        as_of = as_of or clock.local_now().date()
        # one correlation id per run: groups its audit rows and log lines
        bound = context.bind(correlation_id=f"reconcile-{uuid.uuid4().hex}")
        try:
            return await self._reconcile_all(as_of)
        finally:
            context.reset(bound)

    async def _reconcile_all(self, as_of: date) -> int:
        processed = 0
        started = time.perf_counter()
        async with self.database.session() as session:
            workspace_ids = await list_workspace_ids(session)
        for workspace_id in workspace_ids:
            async with self.database.tenant_session(workspace_id, actor="worker") as session:
                for year in {as_of.year - 1, as_of.year}:
                    rows = await obligations_service.evaluate_obligations(
                        session, workspace_id=workspace_id, income_year=year, as_of=as_of
                    )
                    await notifications_service.evaluate_notifications(
                        session, workspace_id=workspace_id, obligations=rows, as_of=as_of
                    )
                processed += 1
        log_event(
            logger,
            "reconciliation.completed",
            message=f"reconciled {processed} workspace(s)",
            attributes={
                "pfa.reconciliation.workspaces": processed,
                "pfa.reconciliation.as_of": as_of.isoformat(),
                "pfa.reconciliation.duration": round(time.perf_counter() - started, 6),
            },
        )
        return processed

    async def run_forever(self, *, poll_seconds: float = 2.0, reconcile_every: int = 3600) -> None:
        last_reconcile = 0.0
        while self.running:
            worked = await self.process_outbox_once()
            worked += await self.process_jobs_once()
            now = asyncio.get_event_loop().time()
            if now - last_reconcile >= reconcile_every:
                await self.run_reconciliation()
                last_reconcile = now
            if worked == 0:
                await asyncio.sleep(poll_seconds)


async def run_once() -> dict:
    worker = Worker()
    outbox = await worker.process_outbox_once()
    jobs = await worker.process_jobs_once()
    workspaces = await worker.run_reconciliation()
    return {"outbox": outbox, "jobs": jobs, "workspaces": workspaces}


def main() -> None:  # pragma: no cover - entrypoint
    configure_telemetry("pfa-worker")
    configure_logging("pfa-worker")
    worker = Worker()
    log_event(
        logger,
        "worker.started",
        message=f"worker {WORKER_ID} started",
        attributes={"pfa.worker.id": WORKER_ID},
    )
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(worker.run_forever())


if __name__ == "__main__":  # pragma: no cover
    main()
