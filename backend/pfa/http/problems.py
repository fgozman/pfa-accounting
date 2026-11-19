"""HTTP error mapping: RFC 9457 problem details with a correlation id."""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from pfa.errors import DomainError


def problem(
    *,
    status: int,
    code: str,
    message: str,
    correlation_id: str,
    field_errors: dict[str, str] | None = None,
    details: dict | None = None,
) -> JSONResponse:
    body = {
        "type": f"https://docs.example.ro/problems/{code}",
        "title": message,
        "status": status,
        "code": code,
        "correlation_id": correlation_id,
    }
    if field_errors:
        body["field_errors"] = field_errors
    if details:
        body["details"] = details
    return JSONResponse(
        status_code=status,
        content=body,
        headers={"Cache-Control": "no-store"},
    )


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", None) or uuid.uuid4().hex
        headers = {}
        retry_after = exc.details.get("retry_after_seconds") if exc.details else None
        if retry_after:
            headers["Retry-After"] = str(int(retry_after))
        response = problem(
            status=exc.http_status,
            code=exc.code,
            message=exc.message,
            correlation_id=correlation_id,
            field_errors=exc.field_errors,
            details=exc.details,
        )
        response.headers.update(headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", None) or uuid.uuid4().hex
        field_errors = {}
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
            field_errors[location or "body"] = error.get("msg", "invalid")
        return problem(
            status=422,
            code="validation_failed",
            message="Datele trimise nu sunt valide.",
            correlation_id=correlation_id,
            field_errors=field_errors,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", None) or uuid.uuid4().hex
        code = {
            401: "unauthenticated",
            403: "permission_denied",
            404: "not_found",
            405: "method_not_allowed",
            409: "conflict",
            429: "rate_limited",
        }.get(exc.status_code, "http_error")
        return problem(
            status=exc.status_code,
            code=code,
            message=str(exc.detail),
            correlation_id=correlation_id,
        )
