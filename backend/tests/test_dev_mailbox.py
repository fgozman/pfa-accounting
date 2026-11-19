"""Development mailbox and the activation flow it unblocks.

Requirement IDs: IDENTITY (activation), OPERATIONS (mail capture), API/DATA
(environment-gated development affordances).
"""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import TEST_USER_PASSWORD, fetch_outbox_payload


async def test_activation_link_is_discoverable_in_development(
    client: AsyncClient, owner_database, database
) -> None:
    """A developer must be able to fetch the activation link instead of reading files."""
    registered = await client.post(
        "/auth/register",
        json={"email": "activable@example.com", "password": TEST_USER_PASSWORD},
    )
    assert registered.status_code == 202

    # deliver the captured mail the way the worker does
    payload = await fetch_outbox_payload(owner_database, event_type="email.verification_requested")
    from pfa.config import reload_settings
    from pfa.services.email import sink_from_settings

    sink = sink_from_settings(reload_settings())
    sink.clear()
    sink.send(
        to=payload["email"],
        subject="Confirmați adresa de email",
        body=f"Confirmați: http://127.0.0.1:5173/verify-email?token={payload['token']}\n",
        template="verification",
        metadata={"token": payload["token"], "email": payload["email"]},
    )

    mailbox = await client.get("/api/v1/dev/mailbox", params={"template": "verification"})
    assert mailbox.status_code == 200, mailbox.text
    body = mailbox.json()
    assert body["environment"] in {"development", "test"}
    item = next(entry for entry in body["items"] if entry["to"] == "activable@example.com")
    assert item["template"] == "verification"
    assert item["link"] and "token=" in item["link"]
    assert item["token"] == payload["token"]

    latest = await client.get("/api/v1/dev/mailbox/latest", params={"template": "verification"})
    assert latest.status_code == 200
    assert latest.json()["token"] == payload["token"]

    # the link from the mailbox activates the account
    token = item["token"]
    verified = await client.post("/auth/verify-email", json={"token": token})
    assert verified.status_code == 200

    # and the token is single use
    replay = await client.post("/auth/verify-email", json={"token": token})
    assert replay.status_code == 409


async def test_dev_mailbox_is_disabled_outside_development(
    client: AsyncClient, monkeypatch
) -> None:
    """The endpoint must not exist in production, and must say nothing useful there."""
    from pfa.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "env", "production", raising=False)
    response = await client.get("/api/v1/dev/mailbox")
    assert response.status_code == 404
    assert "mailbox" not in response.text.lower()
