"""Internal identity endpoint: credential checks for a standalone authorization server.

Mounted only when the authorization server runs as its own service
(``PFA_AUTHORIZATION_SERVER_URL`` set), never part of the public OpenAPI contract,
and protected by the service-to-service token.  The wire contract is documented in
:mod:`pfa.oauth.identity_http`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from pfa.api.deps import PublicSession
from pfa.http.service_auth import require_service_token
from pfa.services import accounts

router = APIRouter(
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

NO_STORE = {"Cache-Control": "no-store"}


class CredentialCheckRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    client_ip: str | None = Field(default=None, max_length=64)
    attempt_key: str = Field(min_length=1, max_length=128)


@router.post("/internal/identity/authenticate")
async def authenticate(payload: CredentialCheckRequest, session: PublicSession) -> JSONResponse:
    result = await accounts.check_credentials(
        session,
        email=payload.email,
        password=payload.password,
        client_ip=payload.client_ip,
        attempt_key=payload.attempt_key,
    )
    if result.accepted:
        return JSONResponse({"subject_id": str(result.user_id)}, headers=NO_STORE)
    return JSONResponse(
        {"rejection": result.rejection, "message": result.message},
        status_code=result.http_status,
        headers=NO_STORE,
    )
