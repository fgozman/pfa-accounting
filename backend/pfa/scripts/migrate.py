"""Migrate both databases, in the only safe order.

    python -m pfa.scripts.migrate [--no-create]

1. make sure the OAuth database exists (created from the owner credentials of
   ``PFA_OAUTH_MIGRATION_DATABASE_URL`` when missing, outside production; in
   production it must be provisioned) and that the runtime role may connect;
2. ``alembic -c alembic_oauth.ini upgrade head`` (the authorization server's schema);
3. copy any OAuth rows still in the application database
   (:mod:`pfa.scripts.move_oauth_data`);
4. ``alembic -c alembic.ini upgrade head`` (the application's schema, whose
   revision 0016 drops the old OAuth tables).
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

import asyncpg
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from pfa.config import Settings, get_settings
from pfa.scripts import move_oauth_data

ROOT = Path(__file__).resolve().parents[2]
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _identifier(value: str | None, what: str) -> str:
    if not value or not IDENTIFIER.fullmatch(value):
        raise SystemExit(f"migrate: invalid {what}: {value!r}")
    return value


async def ensure_oauth_database(settings: Settings, *, create: bool) -> bool:
    """Create the OAuth database when missing; returns True when it was created."""
    owner = make_url(settings.oauth_migration_database_url)
    name = _identifier(owner.database, "OAuth database name")
    runtime_role = _identifier(make_url(settings.oauth_database_url).username, "OAuth runtime role")
    maintenance = owner.set(drivername="postgresql", database="postgres")
    connection = await asyncpg.connect(maintenance.render_as_string(hide_password=False))
    try:
        exists = await connection.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name)
        if not exists:
            if not create:
                raise SystemExit(
                    f"migrate: the OAuth database {name!r} does not exist; provision it "
                    "(owner: the migration role) and rerun"
                )
            await connection.execute(f'CREATE DATABASE "{name}"')
        await connection.execute(f'GRANT CONNECT ON DATABASE "{name}" TO "{runtime_role}"')
        return not exists
    finally:
        await connection.close()


def _alembic(ini: str) -> Config:
    return Config(str(ROOT / ini))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--no-create",
        action="store_true",
        help="never create the OAuth database (always the case in production)",
    )
    args = parser.parse_args(argv)
    settings = get_settings()

    created = asyncio.run(
        ensure_oauth_database(settings, create=not (args.no_create or settings.is_production))
    )
    if created:
        print(
            f"migrate: created the OAuth database {make_url(settings.oauth_database_url).database}"
        )
    command.upgrade(_alembic("alembic_oauth.ini"), "head")
    if move_oauth_data.main() != 0:
        return 1
    command.upgrade(_alembic("alembic.ini"), "head")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
