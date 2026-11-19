"""Email delivery.

In-app notifications are required; email reminders are optional per user and
contain minimal data with a link back to an authenticated page.  Development and
tests use a file-capture sink so e2e suites can assert on the exact message.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pfa import clock
from pfa.config import Settings, get_settings


@dataclass(slots=True)
class CapturedEmail:
    to: str
    subject: str
    body: str
    template: str
    created_at: datetime
    metadata: dict = field(default_factory=dict)


class EmailSink:
    """Test/dev capture sink: one JSON file per message.

    Production replaces this with an SMTP/API sender; the interface is unchanged.
    The contract is that messages never carry raw sensitive tax data beyond the
    minimum needed to prompt the user.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        template: str,
        metadata: dict | None = None,
    ) -> CapturedEmail:
        message = CapturedEmail(
            to=to,
            subject=subject,
            body=body,
            template=template,
            created_at=clock.now(),
            metadata=metadata or {},
        )
        filename = f"{message.created_at.strftime('%Y%m%dT%H%M%S%f')}-{uuid.uuid4().hex[:8]}.json"
        payload = {
            "to": message.to,
            "subject": message.subject,
            "body": message.body,
            "template": message.template,
            "created_at": message.created_at.isoformat(),
            "metadata": message.metadata,
        }
        (self.directory / filename).write_text(
            json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8"
        )
        return message

    def messages(self, *, template: str | None = None) -> list[dict]:
        items = []
        for path in sorted(self.directory.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if template is None or payload.get("template") == template:
                items.append(payload)
        return items

    def clear(self) -> None:
        for path in self.directory.glob("*.json"):
            path.unlink()


def sink_from_settings(settings: Settings | None = None) -> EmailSink:
    settings = settings or get_settings()
    # resolved so API/UI messages can point at the exact directory
    return EmailSink(Path(settings.email_dir).resolve())


def mask_email(address: str) -> str:
    """Mask a recipient for logs: ``j***@example.com``.

    E-mail addresses are personal data; only a masked form may reach logs.
    """
    local, separator, domain = address.partition("@")
    if not separator or not local:
        return "***"
    return f"{local[0]}***@{domain}"


def render_verification_email(token: str, settings: Settings) -> tuple[str, str]:
    link = f"{settings.spa_origin}/verify-email?token={token}"
    hours = max(1, settings.email_token_ttl_seconds // 3600)
    validity = f"{hours} de ore" if hours != 1 else "o oră"
    subject = "Confirmați adresa de email"
    body = (
        "Bună ziua,\n\n"
        "Confirmați adresa de email pentru contul dumneavoastră:\n"
        f"{link}\n\n"
        f"Linkul este valabil o singură dată și expiră în {validity}. "
        "Dacă a expirat, puteți cere un link nou din pagina de verificare.\n"
    )
    return subject, body


def render_invitation_email(token: str, workspace_name: str, settings: Settings) -> tuple[str, str]:
    link = f"{settings.spa_origin}/invitations/accept?token={token}"
    subject = f"Invitație în spațiul de lucru {workspace_name}"
    body = (
        f"Ați fost invitat(ă) în spațiul de lucru {workspace_name}.\n\n"
        f"Acceptați invitația aici:\n{link}\n\n"
        "Invitația este valabilă 7 zile, este legată de adresa de email și poate fi "
        "folosită o singură dată.\n"
    )
    return subject, body


def render_password_reset_email(token: str, settings: Settings) -> tuple[str, str]:
    link = f"{settings.spa_origin}/password-reset/confirm?token={token}"
    subject = "Resetarea parolei"
    body = (
        "A fost solicitată resetarea parolei.\n\n"
        f"{link}\n\n"
        "Dacă nu ați solicitat această resetare, ignorați mesajul.\n"
    )
    return subject, body


def render_reminder_email(title: str, body: str, settings: Settings) -> tuple[str, str]:
    """Minimal-data reminder linking back to an authenticated page."""
    return (
        f"[PFA] {title}",
        f"{body}\n\nDeschideți aplicația: {settings.spa_origin}/declaratii\n",
    )
