"""Development-only mailbox.

Development and tests do not send real email: messages are captured to a local
sink so the flow is exercisable without an SMTP provider (see
``pfa/services/email.py``).  This router exposes that sink so a developer can
retrieve the activation link instead of digging through files.

It is **refused outside development/test**, answers ``404`` there, and never
returns anything from a real mailbox.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from pfa.config import get_settings
from pfa.errors import NotFound
from pfa.services.email import sink_from_settings

router = APIRouter(prefix="/api/v1/dev", tags=["dev"])

LINK_PATTERN = re.compile(r"https?://\S+")

DISABLED_REASON = (
    "Mailbox-ul de dezvoltare este dezactivat: în acest mediu emailul este trimis prin "
    "furnizorul configurat, nu capturat local."
)


def _guard() -> None:
    settings = get_settings()
    if settings.env not in {"development", "test"}:
        # Identical answer to any other unknown path: no information leak.
        raise NotFound("Resursa nu există.")


@router.get("/mailbox")
async def mailbox(
    template: str | None = Query(default=None, description="verification | password_reset | invitation | reminder"),
    email: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
) -> JSONResponse:
    """Captured messages, newest first, with the link extracted for convenience."""
    _guard()
    settings = get_settings()
    sink = sink_from_settings(settings)
    messages = sink.messages(template=template)
    if email:
        wanted = email.strip().casefold()
        messages = [item for item in messages if str(item.get("to", "")).casefold() == wanted]
    messages = sorted(messages, key=lambda item: str(item.get("created_at", "")), reverse=True)[
        :limit
    ]
    items = []
    for message in messages:
        body = str(message.get("body", ""))
        match = LINK_PATTERN.search(body)
        items.append(
            {
                "to": message.get("to"),
                "subject": message.get("subject"),
                "template": message.get("template"),
                "created_at": message.get("created_at"),
                "link": match.group(0) if match else None,
                "token": (message.get("metadata") or {}).get("token"),
                "body": body,
            }
        )
    return JSONResponse(
        {
            "items": items,
            "count": len(items),
            "environment": settings.env,
            "sink_directory": str(sink.directory),
            "note": (
                "Emailul nu este trimis către un furnizor în development: mesajul este capturat "
                "local, iar linkul de mai sus poate fi deschis direct."
            ),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/mailbox/latest")
async def latest(
    request: Request,
    template: str = Query(default="verification"),
    email: str | None = Query(default=None, description="only messages to this recipient"),
) -> JSONResponse:
    """The newest captured message for a template (optionally for one recipient)."""
    _guard()
    settings = get_settings()
    sink = sink_from_settings(settings)
    messages = sink.messages(template=template)
    if email:
        wanted = email.strip().casefold()
        messages = [item for item in messages if str(item.get("to", "")).casefold() == wanted]
    messages = sorted(messages, key=lambda item: str(item.get("created_at", "")), reverse=True)
    if not messages:
        raise NotFound(f"Nu există niciun mesaj capturat pentru {template}.")
    message = messages[0]
    body = str(message.get("body", ""))
    match = LINK_PATTERN.search(body)
    return JSONResponse(
        {
            "to": message.get("to"),
            "template": message.get("template"),
            "created_at": message.get("created_at"),
            "link": match.group(0) if match else None,
            "token": (message.get("metadata") or {}).get("token"),
        },
        headers={"Cache-Control": "no-store"},
    )
