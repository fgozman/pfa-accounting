"""Raw-SQL repositories.

Each module in this package owns the SQL for one domain and returns
:class:`~pfa.repo.query.Row` snapshots.  No ORM session state, identity map or
lazy loading is involved: what a caller reads is exactly the columns the query
selected, and every write is an explicit statement that ``RETURNING`` the new
state.

The SQLAlchemy models in ``pfa.models`` describe the *schema* (the migrated schema
is checked against them by ``tests/test_migrations.py``); they are not used to run
queries.
"""

from pfa.repo.query import (
    Row,
    all_rows,
    and_,
    col,
    exists,
    jsonb,
    limit_clause,
    one,
    order_by,
    returning,
    returning_optional,
    run,
    uuid_list,
    val,
    where,
)

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
