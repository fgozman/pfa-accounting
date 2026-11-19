"""Move the OAuth rows from the application database to the OAuth database.

    python -m pfa.scripts.move_oauth_data

One-off deployment step, run by ``make migrate`` between migrating the OAuth
database and migrating the application database (whose revision
``0016_move_oauth_tables_out`` drops the old tables and refuses while they hold
rows).

Guarantees:

* **nothing is lost**: the source tables are locked against writes (``EXCLUSIVE``)
  for the whole move, every row is copied in foreign-key order, every copied row is
  verified in the target, and only then are the source tables emptied;
* **idempotent**: rows already present are skipped; once the source is empty, or
  the tables are gone, the script does nothing;
* **registrations are reconciled**: an OAuth client that already exists in the
  target (same ``client_id`` + ``environment``, e.g. seeded there first) is kept,
  and the moved rows are re-pointed to it.

Runs with the owner (migration) credentials of both databases.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import Sequence

import asyncpg

from pfa.config import Settings, get_settings

#: copy order: parents before children
TABLES = (
    "oauth_clients",
    "oauth_redirect_uris",
    "oauth_origins",
    "oauth_authorization_transactions",
    "oauth_authorization_codes",
    "oauth_sessions",
    "oauth_refresh_tokens",
)
#: tables whose ``client_id`` column references ``oauth_clients.id``
CLIENT_CHILDREN = {
    "oauth_redirect_uris",
    "oauth_origins",
    "oauth_authorization_transactions",
    "oauth_authorization_codes",
    "oauth_sessions",
}
#: rows identified by a natural key rather than by id (registrations)
NATURAL_KEYS = {
    "oauth_clients": ("client_id", "environment"),
    "oauth_redirect_uris": ("client_id", "exact_uri"),
    "oauth_origins": ("client_id", "origin"),
}


class MoveError(RuntimeError):
    pass


def asyncpg_dsn(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _existing_tables(connection: asyncpg.Connection) -> list[str]:
    present = []
    for table in TABLES:
        if await connection.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{table}"):
            present.append(table)
    return present


async def _insert(connection: asyncpg.Connection, table: str, rows: Sequence[dict]) -> None:
    if not rows:
        return
    columns = list(rows[0].keys())
    placeholders = ", ".join(f"${index}" for index in range(1, len(columns) + 1))
    statement = (
        f'INSERT INTO public."{table}" ({", ".join(columns)}) VALUES ({placeholders}) '
        "ON CONFLICT DO NOTHING"
    )
    await connection.executemany(statement, [tuple(row[c] for c in columns) for row in rows])


async def _verify(connection: asyncpg.Connection, table: str, rows: Sequence[dict]) -> None:
    """Every moved row must now be in the target (by natural key or by id)."""
    if not rows:
        return
    if table in NATURAL_KEYS:
        first, second = NATURAL_KEYS[table]
        found = {
            (record[first], record[second])
            for record in await connection.fetch(f'SELECT {first}, {second} FROM public."{table}"')
        }
        missing = [row for row in rows if (row[first], row[second]) not in found]
    else:
        ids = [row["id"] for row in rows]
        present = {
            record["id"]
            for record in await connection.fetch(
                f'SELECT id FROM public."{table}" WHERE id = ANY($1::uuid[])', ids
            )
        }
        missing = [row for row in rows if row["id"] not in present]
    if missing:
        raise MoveError(f"{table}: {len(missing)} row(s) missing in the OAuth database after copy")


async def move(source_url: str, target_url: str) -> dict[str, int]:
    """Move every OAuth row; returns the number of source rows per table."""
    source = await asyncpg.connect(asyncpg_dsn(source_url))
    target = await asyncpg.connect(asyncpg_dsn(target_url))
    try:
        present = await _existing_tables(source)
        if not present:
            return {}
        missing_target = set(present) - set(await _existing_tables(target))
        if missing_target:
            raise MoveError(
                f"the OAuth database is not migrated (missing {sorted(missing_target)}); "
                "run `alembic -c alembic_oauth.ini upgrade head` first"
            )

        async with source.transaction():
            table_list = ", ".join(f'public."{table}"' for table in present)
            # writers wait until the move commits; readers are not blocked
            await source.execute(f"LOCK TABLE {table_list} IN EXCLUSIVE MODE")
            rows = {
                table: [
                    dict(record) for record in await source.fetch(f'SELECT * FROM public."{table}"')
                ]
                for table in present
            }
            counts = {table: len(records) for table, records in rows.items()}
            if not any(counts.values()):
                return counts

            async with target.transaction():
                client_ids = await _copy_clients(target, rows.get("oauth_clients", []))
                for table in present:
                    if table == "oauth_clients":
                        continue
                    records = rows[table]
                    if table in CLIENT_CHILDREN:
                        records = [
                            {**row, "client_id": client_ids.get(row["client_id"], row["client_id"])}
                            for row in records
                        ]
                        rows[table] = records
                    if table == "oauth_refresh_tokens":
                        await _copy_refresh_tokens(target, records)
                    else:
                        await _insert(target, table, records)
                for table in present:
                    await _verify(target, table, rows[table])

            # the target is committed and verified: only now empty the source
            await source.execute(f"TRUNCATE {table_list}")
            return counts
    finally:
        await source.close()
        await target.close()


async def _copy_clients(
    target: asyncpg.Connection, clients: Sequence[dict]
) -> dict[uuid.UUID, uuid.UUID]:
    """Insert clients; map each source id to the id the target actually holds."""
    await _insert(target, "oauth_clients", clients)
    existing = {
        (record["client_id"], record["environment"]): record["id"]
        for record in await target.fetch(
            "SELECT id, client_id, environment FROM public.oauth_clients"
        )
    }
    return {row["id"]: existing[(row["client_id"], row["environment"])] for row in clients}


async def _copy_refresh_tokens(target: asyncpg.Connection, tokens: Sequence[dict]) -> None:
    """Two phases, because of the self-reference of the rotation chain."""
    await _insert(target, "oauth_refresh_tokens", [{**row, "parent_id": None} for row in tokens])
    chained = [(row["parent_id"], row["id"]) for row in tokens if row["parent_id"] is not None]
    if chained:
        await target.executemany(
            "UPDATE public.oauth_refresh_tokens SET parent_id = $1 WHERE id = $2 AND parent_id IS NULL",
            chained,
        )


def run(settings: Settings | None = None) -> dict[str, int]:
    settings = settings or get_settings()
    return asyncio.run(move(settings.migration_database_url, settings.oauth_migration_database_url))


def main() -> int:
    try:
        counts = run()
    except MoveError as exc:
        print(f"move_oauth_data: {exc}", file=sys.stderr)
        return 1
    if not counts:
        print("move_oauth_data: no OAuth tables in the application database, nothing to move")
    elif not any(counts.values()):
        print("move_oauth_data: OAuth tables are empty, nothing to move")
    else:
        summary = ", ".join(f"{table}={count}" for table, count in counts.items() if count)
        print(f"move_oauth_data: moved and verified {summary}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
