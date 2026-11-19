"""Application settings.

All configuration comes from the environment (or a local ``.env``).  Secrets are
never committed; production values come from a managed secret store.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]

#: The backend is a self-contained project: its ``.env`` sits next to
#: ``pyproject.toml`` and is found regardless of the process working directory.
BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: Environment = Field(default="development", alias="PFA_ENV")

    # --- Database ----------------------------------------------------------
    # Runtime role: cannot bypass RLS, cannot own tables.
    database_url: str = Field(
        default="postgresql+asyncpg://pfa_app:dev_app_password@127.0.0.1:5432/pfa",
        alias="PFA_DATABASE_URL",
    )
    # Owner role: DDL only (migrations, role/grant management).
    migration_database_url: str = Field(
        default="postgresql+asyncpg://pfa_owner:dev_owner_password@127.0.0.1:5432/pfa",
        alias="PFA_MIGRATION_DATABASE_URL",
    )
    # The OAuth 2.0 authorization server's own database (oauth_* tables only).
    # Separate from the application database: no table, foreign key or
    # transaction spans the two.  Same two-role model (runtime / owner).
    oauth_database_url: str = Field(
        default="postgresql+asyncpg://pfa_app:dev_app_password@127.0.0.1:5432/pfa_oauth",
        alias="PFA_OAUTH_DATABASE_URL",
    )
    oauth_migration_database_url: str = Field(
        default="postgresql+asyncpg://pfa_owner:dev_owner_password@127.0.0.1:5432/pfa_oauth",
        alias="PFA_OAUTH_MIGRATION_DATABASE_URL",
    )
    db_pool_size: int = Field(default=10, alias="PFA_DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, alias="PFA_DB_MAX_OVERFLOW")
    db_echo: bool = Field(default=False, alias="PFA_DB_ECHO")

    # --- Origins / OAuth ---------------------------------------------------
    authorization_origin: str = Field(
        default="http://127.0.0.1:8000", alias="PFA_AUTHORIZATION_ORIGIN"
    )
    #: Origin of the web services consumed by the SPA.  Separate from the SPA's own
    #: origin on purpose: the SPA calls the API directly, and CORS — not a dev
    #: proxy — is what authorizes the browser to do so.
    api_origin: str = Field(default="", alias="PFA_API_ORIGIN")
    spa_origin: str = Field(default="http://127.0.0.1:5173", alias="PFA_SPA_ORIGIN")
    spa_callback_uri: str = Field(
        default="http://127.0.0.1:5173/auth/callback", alias="PFA_SPA_CALLBACK_URI"
    )
    spa_client_id: str = Field(default="pfa-web-dev", alias="PFA_SPA_CLIENT_ID")
    #: interactive API documentation (Swagger UI / ReDoc).  Never mounted in
    #: production; the OpenAPI document itself stays published.
    docs_enabled: bool = Field(default=True, alias="PFA_DOCS_ENABLED")

    access_token_ttl_seconds: int = Field(default=300, alias="PFA_ACCESS_TOKEN_TTL_SECONDS")
    refresh_token_ttl_seconds: int = Field(
        default=604800, alias="PFA_REFRESH_TOKEN_TTL_SECONDS"
    )
    authorization_transaction_ttl_seconds: int = Field(
        default=300, alias="PFA_AUTHORIZATION_TRANSACTION_TTL_SECONDS"
    )
    authorization_code_ttl_seconds: int = Field(
        default=60, alias="PFA_AUTHORIZATION_CODE_TTL_SECONDS"
    )
    invitation_ttl_seconds: int = Field(default=604800, alias="PFA_INVITATION_TTL_SECONDS")
    email_token_ttl_seconds: int = Field(default=86400, alias="PFA_EMAIL_TOKEN_TTL_SECONDS")
    login_attempt_limit: int = Field(default=5, alias="PFA_LOGIN_ATTEMPT_LIMIT")

    # --- Service split (optional) ----------------------------------------
    #: Internal base URL of a *separately deployed* authorization server.  Empty
    #: (the default) runs it in-process inside the application.  When set, the
    #: application stops serving /oauth/*, introspects tokens and revokes sessions
    #: over HTTP, and exposes its credential-check endpoint to the server.
    authorization_server_url: str = Field(default="", alias="PFA_AUTHORIZATION_SERVER_URL")
    #: Internal base URL of the application, used by a standalone authorization
    #: server to verify credentials (``/internal/identity/authenticate``).
    identity_provider_url: str = Field(default="", alias="PFA_IDENTITY_PROVIDER_URL")
    #: Shared secret for service-to-service calls (both directions).  Internal
    #: endpoints answer 404 while it is empty.
    internal_service_token: str = Field(default="", alias="PFA_INTERNAL_SERVICE_TOKEN")
    internal_http_timeout_seconds: float = Field(
        default=5.0, alias="PFA_INTERNAL_HTTP_TIMEOUT_SECONDS"
    )

    # --- Logging -----------------------------------------------------------
    #: ``json`` (one object per line, for Loki/Elastic/Datadog...) or ``text``.
    #: Empty: ``json`` in staging/production, ``text`` in development/test.
    log_format: Literal["", "json", "text"] = Field(default="", alias="PFA_LOG_FORMAT")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO", alias="PFA_LOG_LEVEL"
    )
    #: client address in logs (GDPR data minimisation): ``full`` (security
    #: investigations), ``anonymized`` (IPv4 /24, IPv6 /48) or ``off``.  The
    #: audit_events table keeps the full address as the system of record.
    log_client_ip: Literal["full", "anonymized", "off"] = Field(
        default="full", alias="PFA_LOG_CLIENT_IP"
    )

    # --- Keys (outside PostgreSQL) ----------------------------------------
    key_dir: Path = Field(default=Path("./keys"), alias="PFA_KEY_DIR")
    key_active_kid: str = Field(default="dev-2026-01", alias="PFA_KEY_ACTIVE_KID")
    key_rotation_overlap_hours: int = Field(default=48, alias="PFA_KEY_ROTATION_OVERLAP_HOURS")

    # --- Password hashing --------------------------------------------------
    argon2_time_cost: int = Field(default=3, alias="PFA_ARGON2_TIME_COST")
    argon2_memory_cost: int = Field(default=65536, alias="PFA_ARGON2_MEMORY_COST")
    argon2_parallelism: int = Field(default=4, alias="PFA_ARGON2_PARALLELISM")

    # --- Storage -----------------------------------------------------------
    object_storage_dir: Path = Field(
        default=Path("./.run/objects"), alias="PFA_OBJECT_STORAGE_DIR"
    )
    #: Directory of the file-capture e-mail sink used in development/test.  It is a
    #: setting of its own so environments never share an inbox (running the test
    #: suite clears its own captured mail, and must not wipe development's).
    email_dir: Path = Field(default=Path("./.run/emails"), alias="PFA_EMAIL_DIR")
    max_attachment_bytes: int = Field(default=20 * 1024 * 1024, alias="PFA_MAX_ATTACHMENT_BYTES")

    # --- ANAF --------------------------------------------------------------
    anaf_driver: Literal["fake", "https"] = Field(default="fake", alias="PFA_ANAF_DRIVER")
    anaf_client_id: str = Field(default="", alias="PFA_ANAF_CLIENT_ID")
    anaf_client_secret: str = Field(default="", alias="PFA_ANAF_CLIENT_SECRET")
    anaf_redirect_uri: str = Field(
        default="http://127.0.0.1:8000/integrations/anaf/callback",
        alias="PFA_ANAF_REDIRECT_URI",
    )
    anaf_environment: Literal["test", "production"] = Field(
        default="test", alias="PFA_ANAF_ENVIRONMENT"
    )

    # --- Deadlines / notifications ----------------------------------------
    deadline_timezone: str = Field(default="Europe/Bucharest", alias="PFA_DEADLINE_TIMEZONE")
    reminder_offsets_days: tuple[int, ...] = (30, 14, 7, 3, 1, 0, -7)

    # --- Seed (development only) ------------------------------------------
    seed_admin_email: str = Field(default="admin@example.test", alias="PFA_SEED_ADMIN_EMAIL")
    seed_admin_password: str = Field(
        default="Correct-Horse-Battery-9", alias="PFA_SEED_ADMIN_PASSWORD"
    )
    seed_member_email: str = Field(default="member@example.test", alias="PFA_SEED_MEMBER_EMAIL")
    seed_member_password: str = Field(
        default="Correct-Horse-Battery-9", alias="PFA_SEED_MEMBER_PASSWORD"
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("log_format", mode="before")
    @classmethod
    def _lower_log_format(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value

    @field_validator("key_dir", "object_storage_dir", "email_dir", mode="after")
    @classmethod
    def _project_relative(cls, value: Path) -> Path:
        """Resolve local paths against the backend project, not the process CWD.

        The backend is started from different places (``make -C backend``, the
        repository-root development harness, tests).  A relative ``./.run/...`` must
        always mean the same directory, otherwise two processes could read and write
        different sinks.
        """
        if value.is_absolute():
            return value
        return (BACKEND_DIR / value).resolve()

    @field_validator(
        "database_url",
        "migration_database_url",
        "oauth_database_url",
        "oauth_migration_database_url",
    )
    @classmethod
    def _require_postgres(cls, value: str) -> str:
        if not value.startswith("postgresql"):
            raise ValueError(
                "Only PostgreSQL is supported: SQLite cannot provide RLS, NUMERIC "
                "semantics or the locking guarantees this application requires."
            )
        return value

    # ------------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def is_test(self) -> bool:
        return self.env == "test"

    @property
    def web_service_origin(self) -> str:
        """Where the SPA must send API requests (falls back to the auth origin)."""
        return (self.api_origin or self.authorization_origin).rstrip("/")

    @property
    def spa_public_config(self) -> dict[str, str]:
        """Public ``VITE_*`` configuration the SPA must be built with.

        The SPA is a separate project that cannot read these settings, so this is
        the documented integration contract: the registered origins, client id and
        redirect URI are projected into the SPA's build-time variables.  The
        repository-root development harness applies exactly this mapping.
        """
        return {
            "VITE_API_BASE_URL": self.web_service_origin,
            "VITE_AUTHORIZATION_ORIGIN": self.authorization_origin,
            "VITE_SPA_CLIENT_ID": self.spa_client_id,
            "VITE_SPA_CALLBACK_URI": self.spa_callback_uri,
        }

    @property
    def effective_log_format(self) -> str:
        if self.log_format:
            return self.log_format
        return "text" if self.env in {"development", "test"} else "json"

    @property
    def authorization_server_is_remote(self) -> bool:
        """True when the authorization server runs as a separate service."""
        return bool(self.authorization_server_url)

    def require_internal_service_token(self) -> str:
        """The service-to-service secret; refuses to run split mode without a strong one."""
        token = self.internal_service_token
        if len(token) < 32:
            raise RuntimeError(
                "PFA_INTERNAL_SERVICE_TOKEN must be set (at least 32 characters) when the "
                "authorization server and the application run as separate services."
            )
        return token

    @property
    def docs_available(self) -> bool:
        """Interactive docs are a development/test affordance only."""
        return self.docs_enabled and self.env != "production"

    @property
    def docs_client_id(self) -> str:
        """Public OAuth client used by the Swagger UI "Authorize" button."""
        return f"{self.spa_client_id}-docs"

    @property
    def docs_redirect_uri(self) -> str:
        """Registered redirect URI of the Swagger UI OAuth2 redirect page."""
        return f"{self.authorization_origin}/docs/oauth2-redirect"

    @property
    def cookie_free(self) -> bool:
        """The application never uses cookies for authentication."""
        return True

    def allowed_origins(self) -> list[str]:
        origins = {self.spa_origin}
        if self.env in {"development", "test"}:
            origins.add("http://127.0.0.1:5173")
            origins.add("http://localhost:5173")
        return sorted(origins)

    def assert_not_production(self, action: str) -> None:
        if self.is_production:
            raise RuntimeError(f"Refusing to {action} with PFA_ENV=production")


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
