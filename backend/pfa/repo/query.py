"""Raw-SQL data access layer.

Design rules
------------
1. **Every statement is written out at the call site.**  There is no query builder:
   each ``INSERT``/``SELECT``/``UPDATE`` is literal SQL in the service module, so
   what is sent to PostgreSQL is exactly what is reviewed and grepped.
2. **Columns are always listed explicitly.**  No ``SELECT *`` in application
   queries, so adding a column cannot silently change a payload or a cost profile.
3. **Values are always bound.**  Dynamic input only ever enters through named
   parameters; string composition is limited to SQL *fragments* built from a fixed
   whitelist (predicates and clauses), never from user data.
4. **Types are explicit.**  ``jsonb``/``uuid``/``interval``/array parameters are
   cast in the SQL, because the driver must be told what to send.
5. **Rows are mappings with attribute access** (:class:`Row`), so call sites can
   read ``document.number`` without an ORM identity map.  A row is a snapshot, not
   a live object: it is never mutated, and writes are explicit ``UPDATE``
   statements that ``RETURNING`` the new state.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Row:
    """An immutable result row with mapping and attribute access.

    A row is a **snapshot**, not a live entity: assigning to a column raises
    ``FrozenInstanceError`` (``cannot assign to field 'x'``).  Persisting a change
    means running an explicit ``UPDATE ... RETURNING`` statement and using the row
    it returns, which keeps the write visible at the call site.
    """

    _data: Mapping[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __getattr__(self, name: str) -> Any:
        try:
            return self._data[name]
        except KeyError as exc:  # pragma: no cover - programming error
            raise AttributeError(
                f"{name!r} is not a column of this query result; available columns: "
                f"{', '.join(sorted(self._data))}"
            ) from exc

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def keys(self) -> Iterable[str]:
        return self._data.keys()

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)

    def replace(self, **changes: Any) -> Row:
        """A copy with some columns replaced (used to compose responses)."""
        merged = dict(self._data)
        merged.update(changes)
        return Row(merged)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        shown = ", ".join(f"{key}={value!r}" for key, value in list(self._data.items())[:6])
        return f"Row({shown}{', …' if len(self._data) > 6 else ''})"


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


async def one(session: AsyncSession, sql: str, **params: Any) -> Row | None:
    """The first row, or ``None``."""
    result = await session.execute(text(sql), _bind(params))
    row = result.mappings().first()
    return Row(dict(row)) if row is not None else None


async def all_rows(session: AsyncSession, sql: str, **params: Any) -> list[Row]:
    """All rows."""
    result = await session.execute(text(sql), _bind(params))
    return [Row(dict(row)) for row in result.mappings().all()]


async def val(session: AsyncSession, sql: str, *, default: Any = None, **params: Any) -> Any:
    """A single scalar value (``COUNT``, ``SUM``, an id, a boolean)."""
    result = await session.execute(text(sql), _bind(params))
    value = result.scalar()
    return default if value is None else value


async def col(session: AsyncSession, sql: str, **params: Any) -> list[Any]:
    """One column from every row."""
    result = await session.execute(text(sql), _bind(params))
    return list(result.scalars().all())


async def exists(session: AsyncSession, sql: str, **params: Any) -> bool:
    return bool(await val(session, sql, **params))


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


async def returning(session: AsyncSession, sql: str, **params: Any) -> Row:
    """Run an ``INSERT``/``UPDATE``/``DELETE`` that has a ``RETURNING`` clause.

    A missing row is a programming error: the statement was written expecting to
    affect something, so failing loudly beats returning ``None`` to a caller that
    would dereference it later.
    """
    result = await session.execute(text(sql), _bind(params))
    row = result.mappings().first()
    if row is None:
        raise LookupError("statement returned no row; the targeted row does not exist")
    return Row(dict(row))


async def returning_optional(session: AsyncSession, sql: str, **params: Any) -> Row | None:
    """Like :func:`returning`, for statements that may legitimately match nothing."""
    result = await session.execute(text(sql), _bind(params))
    row = result.mappings().first()
    return Row(dict(row)) if row is not None else None


async def run(session: AsyncSession, sql: str, **params: Any) -> int:
    """Run a statement without ``RETURNING``; returns the affected row count."""
    result = await session.execute(text(sql), _bind(params))
    return int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# Dynamic fragment helpers (structure only, never values)
# ---------------------------------------------------------------------------


def where(*conditions: str | None) -> str:
    """Join non-empty predicates into a ``WHERE`` clause."""
    present = [condition for condition in conditions if condition]
    return f"WHERE {' AND '.join(present)}" if present else ""


def and_(*conditions: str | None) -> str:
    return " AND ".join(condition for condition in conditions if condition)


def order_by(*clauses: str) -> str:
    present = [clause for clause in clauses if clause]
    return f"ORDER BY {', '.join(present)}" if present else ""


def limit_clause(limit: int, *, maximum: int = 200) -> str:
    """Bound a ``LIMIT`` so an unbounded list can never be requested."""
    bounded = min(max(int(limit), 1), maximum)
    return f"LIMIT {bounded}"


def uuid_list(values: Sequence[Any]) -> list[str]:
    """Normalise a list of identifiers for ``= ANY(CAST(:ids AS uuid[]))``."""
    return [str(value) for value in values]


def jsonb(value: Any) -> str | None:
    """Serialise a parameter destined for a ``jsonb`` column (always ``CAST``)."""
    if value is None:
        return None
    return json.dumps(value, default=str, separators=(",", ":"))


def _bind(params: Mapping[str, Any]) -> dict[str, Any]:
    """Drop unused placeholders and keep parameter names explicit.

    SQLAlchemy raises when a statement contains a placeholder that was not bound,
    which catches typos; here we only normalise ``Row`` values that callers pass
    through from a previous query.
    """
    bound: dict[str, Any] = {}
    for key, value in params.items():
        bound[key] = value.as_dict() if isinstance(value, Row) else value
    return bound


__all__ = [
    "Row",
    "all_rows",
    "and_",
    "col",
    "exists",
    "jsonb",
    "limit_clause",
    "one",
    "order_by",
    "returning",
    "returning_optional",
    "run",
    "uuid_list",
    "val",
    "where",
]
