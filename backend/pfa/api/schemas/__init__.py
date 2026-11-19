"""Request and response contracts.

``pfa.api.schemas.responses`` holds the typed response models; this module keeps
the shared request field types and re-exports the response models so call sites can
import from one place.

``EmailStr`` is deliberately not used: it rejects reserved test domains such as
``example.test``.  Addresses are validated with ``email_validator`` in test mode so
synthetic and reserved domains work in development and CI, while deliverability is
never checked by the API itself.
"""

from __future__ import annotations

from typing import Annotated

from email_validator import EmailNotValidError, validate_email
from pydantic import AfterValidator


def _validate_email(value: str) -> str:
    try:
        result = validate_email(value, check_deliverability=False, test_environment=True)
    except EmailNotValidError as exc:
        raise ValueError(str(exc)) from exc
    return result.normalized


EmailAddress = Annotated[str, AfterValidator(_validate_email)]


from pfa.api.schemas.responses import (  # noqa: E402  (re-exported for call sites)
    AcceptedResponse,
    AssetCreatedResponse,
    AssetListResponse,
    AssetView,
    CloseChecklistResponse,
    DashboardResponse,
    DeclarationArtifact,
    DeclarationListResponse,
    DeclarationPreparedResponse,
    DeclarationTraceResponse,
    DeclarationView,
    DocumentDetail,
    DocumentLineView,
    DocumentListResponse,
    DocumentView,
    HealthResponse,
    InvitationAcceptedResponse,
    InvitationCreatedResponse,
    InvitationListResponse,
    InvitationView,
    JobView,
    MemberListResponse,
    MemberView,
    MeResponse,
    NotificationListResponse,
    NotificationView,
    ObligationCapabilities,
    ObligationListResponse,
    ObligationView,
    PaymentListResponse,
    PaymentRecordedResponse,
    PaymentView,
    PeriodLockResponse,
    ReadinessResponse,
    ReceiptImportedResponse,
    ReceiptListResponse,
    ReceiptView,
    RegisterExportResponse,
    RegisterResponse,
    SettlementSummary,
    UnassessedForm,
    WorkspaceCreatedResponse,
    WorkspaceListResponse,
    WorkspaceSummary,
)

__all__ = [
    "AcceptedResponse",
    "AssetCreatedResponse",
    "AssetListResponse",
    "AssetView",
    "CloseChecklistResponse",
    "DashboardResponse",
    "DeclarationArtifact",
    "DeclarationListResponse",
    "DeclarationPreparedResponse",
    "DeclarationTraceResponse",
    "DeclarationView",
    "DocumentDetail",
    "DocumentLineView",
    "DocumentListResponse",
    "DocumentView",
    "EmailAddress",
    "HealthResponse",
    "InvitationAcceptedResponse",
    "InvitationCreatedResponse",
    "InvitationListResponse",
    "InvitationView",
    "JobView",
    "MeResponse",
    "MemberListResponse",
    "MemberView",
    "NotificationListResponse",
    "NotificationView",
    "ObligationCapabilities",
    "ObligationListResponse",
    "ObligationView",
    "PaymentListResponse",
    "PaymentRecordedResponse",
    "PaymentView",
    "PeriodLockResponse",
    "ReadinessResponse",
    "ReceiptImportedResponse",
    "ReceiptListResponse",
    "ReceiptView",
    "RegisterExportResponse",
    "RegisterResponse",
    "SettlementSummary",
    "UnassessedForm",
    "WorkspaceCreatedResponse",
    "WorkspaceListResponse",
    "WorkspaceSummary",
]
