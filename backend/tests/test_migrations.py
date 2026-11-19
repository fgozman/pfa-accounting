"""Migrations build both schemas from EMPTY databases and match the models.

The shared test databases are already at head when the suite runs, so ``upgrade
head`` there proves nothing about a fresh install.  These tests use throwaway
databases on the same isolated test server:

* the application history (``migrations/``) and the authorization server's history
  (``migrations_oauth/``) each upgrade from empty, downgrade to base and upgrade
  again, and neither drifts from its models (``Base`` / ``OAuthBase``);
* an existing installation whose OAuth rows still live in the application database
  is migrated by ``pfa.scripts.migrate`` without losing a row, and the application
  revision that drops the old tables refuses to run while they hold data.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

import pfa.models  # noqa: F401  (registers every mapped class on Base.metadata)
from pfa.models.base import Base
from pfa.oauth.models import OAuthBase
from tests.conftest import ROOT

pytestmark = pytest.mark.postgres

OAUTH_TABLES = (
    "oauth_clients",
    "oauth_redirect_uris",
    "oauth_origins",
    "oauth_authorization_transactions",
    "oauth_authorization_codes",
    "oauth_sessions",
    "oauth_refresh_tokens",
)


def _with_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{database}"))


def _runtime(owner_url: str, runtime_template: str) -> str:
    """The runtime-role URL for the same database as ``owner_url``."""
    runtime = urlsplit(os.environ[runtime_template])
    return urlunsplit(runtime._replace(path=urlsplit(owner_url).path))


def _dsn(url: str) -> str:
    return url.replace("+asyncpg", "")


@dataclass(frozen=True, slots=True)
class Scratch:
    """Owner URLs of a throwaway application database and OAuth database."""

    app: str
    oauth: str

    def env(self) -> dict[str, str]:
        return {
            **os.environ,
            "PFA_MIGRATION_DATABASE_URL": self.app,
            "PFA_DATABASE_URL": _runtime(self.app, "PFA_DATABASE_URL"),
            "PFA_OAUTH_MIGRATION_DATABASE_URL": self.oauth,
            "PFA_OAUTH_DATABASE_URL": _runtime(self.oauth, "PFA_OAUTH_DATABASE_URL"),
        }


@pytest.fixture
async def scratch() -> AsyncIterator[Scratch]:
    owner_url = os.environ["PFA_MIGRATION_DATABASE_URL"]
    suffix = uuid.uuid4().hex[:8]
    names = (f"pfa_test_migrations_{suffix}", f"pfa_test_migrations_oauth_{suffix}")
    admin = await asyncpg.connect(_dsn(_with_database(owner_url, "postgres")))
    try:
        for name in names:
            await admin.execute(f'CREATE DATABASE "{name}"')
        yield Scratch(_with_database(owner_url, names[0]), _with_database(owner_url, names[1]))
    finally:
        for name in names:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


def _run(scratch: Scratch, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", *args],
        cwd=ROOT,
        env=scratch.env(),
        capture_output=True,
        text=True,
        check=False,
    )


def _alembic(scratch: Scratch, ini: str, *args: str, expect_success: bool = True):
    result = _run(scratch, "alembic", "-c", str(ROOT / ini), *args)
    if expect_success:
        assert (
            result.returncode == 0
        ), f"alembic {ini} {' '.join(args)} failed:\n{result.stderr[-3000:]}"
    return result


def _drift(metadata):
    def compare(connection) -> list:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        return [
            change
            for change in compare_metadata(context, metadata)
            if not (change[0] == "remove_table" and change[1].name == "alembic_version")
        ]

    return compare


async def _schema_drift(url: str, metadata) -> list:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(_drift(metadata))
    finally:
        await engine.dispose()


async def test_application_migrations_build_the_model_schema_from_an_empty_database(
    scratch: Scratch,
) -> None:
    _alembic(scratch, "alembic.ini", "upgrade", "head")
    _alembic(scratch, "alembic.ini", "downgrade", "base")
    _alembic(scratch, "alembic.ini", "upgrade", "head")
    drift = await _schema_drift(scratch.app, Base.metadata)
    assert drift == [], f"schema produced by migrations differs from pfa.models: {drift}"

    connection = await asyncpg.connect(_dsn(scratch.app))
    try:
        leftovers = await connection.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'oauth%'"
        )
    finally:
        await connection.close()
    assert leftovers == []  # the application database holds no OAuth table


async def test_oauth_migrations_build_the_oauth_schema_from_an_empty_database(
    scratch: Scratch,
) -> None:
    _alembic(scratch, "alembic_oauth.ini", "upgrade", "head")
    _alembic(scratch, "alembic_oauth.ini", "downgrade", "base")
    _alembic(scratch, "alembic_oauth.ini", "upgrade", "head")
    drift = await _schema_drift(scratch.oauth, OAuthBase.metadata)
    assert drift == [], f"OAuth schema differs from pfa.oauth.models: {drift}"

    connection = await asyncpg.connect(_dsn(scratch.oauth))
    try:
        tables = {
            row["tablename"]
            for row in await connection.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
        runtime_role = urlsplit(scratch.env()["PFA_OAUTH_DATABASE_URL"]).username
        granted = await connection.fetchval(
            "SELECT bool_and(has_table_privilege($1, 'public.' || t, 'SELECT,INSERT,UPDATE,DELETE')) "
            "FROM unnest($2::text[]) AS t",
            runtime_role,
            list(OAUTH_TABLES),
        )
    finally:
        await connection.close()
    assert tables == {*OAUTH_TABLES, "alembic_version"}
    assert granted is True


async def test_development_seed_runs_on_fresh_databases_and_is_idempotent(
    scratch: Scratch,
) -> None:
    """`make seed-dev` (and `make e2e`, which needs the SPA OAuth client it registers)
    must work right after `make migrate` on empty databases."""
    migrated = _run(scratch, "pfa.scripts.migrate")
    assert migrated.returncode == 0, migrated.stderr[-3000:]
    for attempt in (1, 2):
        result = _run(scratch, "pfa.scripts.seed_dev")
        assert (
            result.returncode == 0
        ), f"seed run {attempt} failed:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}"

    app = await asyncpg.connect(_dsn(scratch.app))
    oauth = await asyncpg.connect(_dsn(scratch.oauth))
    try:
        documents = await app.fetchval("SELECT count(*) FROM documents")
        clients = await oauth.fetchval(
            "SELECT count(*) FROM oauth_clients WHERE client_id = $1",
            os.environ.get("PFA_SPA_CLIENT_ID", "pfa-web-test"),
        )
    finally:
        await app.close()
        await oauth.close()
    assert documents == 4  # an invoice and an expense in each of the two demo PFAs
    assert clients == 1


# ---------------------------------------------------------------------------
# Moving the OAuth rows of an existing installation
# ---------------------------------------------------------------------------


async def _insert_oauth_rows(url: str, *, client_id: str = "legacy-spa") -> dict[str, uuid.UUID]:
    """A realistic set of OAuth rows, as an installation at revision 0015 holds them."""
    ids = {name: uuid.uuid4() for name in ("client", "tx", "code", "session", "rt1", "rt2")}
    subject = uuid.uuid4()
    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)
    connection = await asyncpg.connect(_dsn(url))
    try:
        await connection.execute(
            """
            INSERT INTO oauth_clients (id, client_id, client_type, token_endpoint_auth_method,
                environment, enabled, require_pkce, grant_types, response_types,
                allowed_scopes, allowed_pkce_methods)
            VALUES ($1, $2, 'public', 'none', 'test', true, true, $3, $4, $5, $6)
            """,
            ids["client"],
            client_id,
            json.dumps(["authorization_code", "refresh_token"]),
            json.dumps(["code"]),
            json.dumps(["pfa.read"]),
            json.dumps(["S256"]),
        )
        await connection.execute(
            "INSERT INTO oauth_redirect_uris (client_id, exact_uri) VALUES ($1, 'https://spa.example/cb')",
            ids["client"],
        )
        await connection.execute(
            "INSERT INTO oauth_origins (client_id, origin) VALUES ($1, 'https://spa.example')",
            ids["client"],
        )
        await connection.execute(
            """
            INSERT INTO oauth_authorization_transactions (id, client_id, redirect_uri, scope, state,
                code_challenge, code_challenge_method, proof_hash, expires_at, attempt_count,
                authenticated_user_id)
            VALUES ($1, $2, 'https://spa.example/cb', 'pfa.read', 'state-0123456789abcdef',
                    $3, 'S256', $4, $5, 2, $6)
            """,
            ids["tx"],
            ids["client"],
            "c" * 43,
            secrets.token_hex(32),
            now,
            subject,
        )
        await connection.execute(
            """
            INSERT INTO oauth_authorization_codes (id, code_hash, user_id, client_id, transaction_id,
                redirect_uri, code_challenge, code_challenge_method, scope, expires_at, used_at,
                session_id)
            VALUES ($1, $2, $3, $4, $5, 'https://spa.example/cb', $6, 'S256', 'pfa.read', $7, $7, $8)
            """,
            ids["code"],
            secrets.token_hex(32),
            subject,
            ids["client"],
            ids["tx"],
            "c" * 43,
            now,
            ids["session"],
        )
        await connection.execute(
            """
            INSERT INTO oauth_sessions (id, user_id, client_id, scope, absolute_expires_at)
            VALUES ($1, $2, $3, 'pfa.read', $4)
            """,
            ids["session"],
            subject,
            ids["client"],
            now + timedelta(days=7),
        )
        # a rotation chain: rt2 replaced rt1
        for token, parent in ((ids["rt1"], None), (ids["rt2"], ids["rt1"])):
            await connection.execute(
                """
                INSERT INTO oauth_refresh_tokens (id, token_hash, jti, user_id, session_id, parent_id,
                    expires_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                token,
                secrets.token_hex(32),
                uuid.uuid4(),
                subject,
                ids["session"],
                parent,
                now + timedelta(days=7),
            )
    finally:
        await connection.close()
    return ids


async def _counts(url: str) -> dict[str, int]:
    connection = await asyncpg.connect(_dsn(url))
    try:
        return {
            table: await connection.fetchval(f"SELECT count(*) FROM {table}")
            for table in OAUTH_TABLES
        }
    finally:
        await connection.close()


async def test_existing_oauth_rows_are_moved_without_loss(scratch: Scratch) -> None:
    _alembic(scratch, "alembic.ini", "upgrade", "0015_oauth2_detach_users")
    ids = await _insert_oauth_rows(scratch.app)
    before = await _counts(scratch.app)

    migrated = _run(scratch, "pfa.scripts.migrate")
    assert migrated.returncode == 0, migrated.stderr[-3000:]
    assert "moved and verified" in migrated.stdout

    assert await _counts(scratch.oauth) == before
    oauth = await asyncpg.connect(_dsn(scratch.oauth))
    app = await asyncpg.connect(_dsn(scratch.app))
    try:
        parent = await oauth.fetchval(
            "SELECT parent_id FROM oauth_refresh_tokens WHERE id = $1", ids["rt2"]
        )
        scopes = await oauth.fetchval(
            "SELECT allowed_scopes FROM oauth_clients WHERE id = $1", ids["client"]
        )
        attempts = await oauth.fetchval(
            "SELECT attempt_count FROM oauth_authorization_transactions WHERE id = $1", ids["tx"]
        )
        left = await app.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'oauth%'"
        )
        version = await app.fetchval("SELECT version_num FROM alembic_version")
    finally:
        await oauth.close()
        await app.close()
    assert parent == ids["rt1"]  # the rotation chain survives
    assert json.loads(scopes) == ["pfa.read"]
    assert attempts == 2
    assert left == []
    assert version == "0016_move_oauth_tables_out"

    # idempotent: a second run has nothing to do and changes nothing
    again = _run(scratch, "pfa.scripts.migrate")
    assert again.returncode == 0, again.stderr[-3000:]
    assert await _counts(scratch.oauth) == before


async def test_moved_rows_attach_to_a_client_already_registered_in_the_oauth_database(
    scratch: Scratch,
) -> None:
    """A client seeded in the OAuth database first is kept; moved rows point to it."""
    _alembic(scratch, "alembic.ini", "upgrade", "0015_oauth2_detach_users")
    _alembic(scratch, "alembic_oauth.ini", "upgrade", "head")
    legacy = await _insert_oauth_rows(scratch.app, client_id="shared-spa")
    existing = await _insert_oauth_rows(scratch.oauth, client_id="shared-spa")

    migrated = _run(scratch, "pfa.scripts.migrate")
    assert migrated.returncode == 0, migrated.stderr[-3000:]

    oauth = await asyncpg.connect(_dsn(scratch.oauth))
    try:
        clients = await oauth.fetch("SELECT id FROM oauth_clients WHERE client_id = 'shared-spa'")
        session_client = await oauth.fetchval(
            "SELECT client_id FROM oauth_sessions WHERE id = $1", legacy["session"]
        )
    finally:
        await oauth.close()
    assert [row["id"] for row in clients] == [existing["client"]]
    assert session_client == existing["client"]


async def test_the_application_refuses_to_drop_unmoved_oauth_rows(scratch: Scratch) -> None:
    _alembic(scratch, "alembic.ini", "upgrade", "0015_oauth2_detach_users")
    await _insert_oauth_rows(scratch.app)

    refused = _alembic(scratch, "alembic.ini", "upgrade", "head", expect_success=False)
    assert refused.returncode != 0
    assert "OAuthDataNotMoved" in refused.stderr
    assert (await _counts(scratch.app))["oauth_sessions"] == 1  # nothing was dropped
