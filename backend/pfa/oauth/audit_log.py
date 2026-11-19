"""Audit sink for a standalone authorization server: structured log events.

The monolith writes the server's security events to the application's
``audit_events`` table (see :mod:`pfa.wiring`), which also emits them as ``audit``
log events.  A standalone server has no such table, so it emits the **same**
``audit`` event directly on the ``pfa.oauth.audit`` logger: one query
(``event="audit"``) covers both deployment shapes in a log pipeline.  No secret
(token, code, password) is ever part of an audit event.
"""

from __future__ import annotations

import logging
import uuid

from pfa.observability import context
from pfa.observability.logs import log_event

logger = logging.getLogger("pfa.oauth.audit")


class LoggingAuditSink:
    async def record(
        self,
        *,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID,
        actor_user_id: uuid.UUID,
    ) -> None:
        log_event(
            logger,
            "audit",
            message=f"{action} {entity_type}",
            attributes={
                "pfa.audit.action": action,
                "pfa.audit.entity_type": entity_type,
                "pfa.audit.entity_id": str(entity_id),
                "pfa.audit.actor_kind": "user",
                "user.id": str(actor_user_id),
                "pfa.correlation_id": context.correlation_id(),
            },
        )
