"""Durable job status, as seen by the API (enqueueing lives in :mod:`pfa.services.audit`)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from pfa.errors import NotFound
from pfa.models.operations import Job
from pfa.repo import operations as operations_repo


async def job_workspace(session: AsyncSession, job_id: uuid.UUID) -> uuid.UUID | None:
    """Resolve a job's workspace without a tenant context (identifier only)."""
    return await operations_repo.job_workspace(session, job_id)


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Job:
    """A job visible in the current tenant context."""
    job = await session.get(Job, job_id)
    if job is None:
        raise NotFound("Lucrarea nu există.")
    return job
