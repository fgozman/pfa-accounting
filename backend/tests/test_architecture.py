"""Architecture rules that keep the OAuth 2.0 authorization server separable.

These are static checks (imports, SQL text, model metadata); they need no database.

* :mod:`pfa.oauth` depends only on shared infrastructure, never on the application;
* the application reaches the authorization server only through the composition
  root (:mod:`pfa.main`, :mod:`pfa.wiring`) — never by importing :mod:`pfa.oauth`;
* the ``oauth_*`` tables have their own metadata (their own database); no SQL
  statement and no foreign key crosses the boundary, and the server never opens the
  application database;
* HTTP handlers do not read the database: no ``pfa.db``, repositories, SQLAlchemy
  or table models in route modules.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import pfa.models  # noqa: F401  (registers every mapped class on Base.metadata)
from pfa.models.base import Base
from pfa.oauth.models import OAuthBase

PACKAGE = Path(__file__).resolve().parents[1] / "pfa"

#: infrastructure shared by both services; it must not depend on either of them
SHARED_INFRASTRUCTURE = (
    "pfa.__version__",
    "pfa.config",
    "pfa.errors",
    "pfa.clock",
    "pfa.db",
    "pfa.http",
    "pfa.observability",
    "pfa.security",
    "pfa.repo.query",
    "pfa.models.base",
)

#: what the authorization server may import besides itself
OAUTH_ALLOWED_IMPORTS = ("pfa.oauth", *SHARED_INFRASTRUCTURE)

#: the only application modules allowed to import pfa.oauth
COMPOSITION_ROOTS = {
    "pfa.main",  # mounts the authorization server's router, loads its keys
    "pfa.wiring",  # in-process adapters for both sides' ports
}
SCRIPTS_PREFIX = "pfa.scripts."  # deployment/dev tooling (client registration, seed)
#: the one-off migration tool that copies OAuth rows between the two databases
DATA_MOVE_SCRIPT = "pfa.scripts.move_oauth_data"
#: the application database handle; the authorization server has its own
APPLICATION_DATABASE = ("pfa.db.get_database", "pfa.db.override_database", "pfa.db.dispose_database")

#: what HTTP handlers must not import: they get units of work from pfa.api.deps
ROUTE_FORBIDDEN_IMPORTS = ("pfa.db", "pfa.repo", "sqlalchemy", "pfa.oauth")
#: value types (enums) shared with request models are fine; table models are not
ROUTE_ALLOWED_MODEL_MODULES = ("pfa.models.base",)


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE.parent).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _imports(path: Path) -> list[str]:
    """Every module imported anywhere in the file, including function-level imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _module_name(path).rsplit(".", 1)[0]
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import
                base = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module or ""
            found.append(module)
            # `from pfa import oauth` / `from pfa.services import accounts`
            found.extend(f"{module}.{alias.name}" for alias in node.names)
    return found


def _matches(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in prefixes)


def _is_pfa_module(module: str) -> bool:
    return module == "pfa" or module.startswith("pfa.")


OAUTH_FILES = _python_files(PACKAGE / "oauth")
APPLICATION_FILES = [
    path for path in _python_files(PACKAGE) if (PACKAGE / "oauth") not in path.parents
]
ROUTE_FILES = _python_files(PACKAGE / "api" / "routes")


@pytest.mark.parametrize("path", OAUTH_FILES, ids=lambda path: path.name)
def test_authorization_server_depends_only_on_shared_infrastructure(path: Path) -> None:
    offending = sorted(
        {
            module
            for module in _imports(path)
            if _is_pfa_module(module)
            and module != "pfa"  # `from pfa import clock` is checked as pfa.clock
            and not _matches(module, OAUTH_ALLOWED_IMPORTS)
            and not _matches(module.rsplit(".", 1)[0], OAUTH_ALLOWED_IMPORTS)
        }
    )
    assert offending == [], f"{_module_name(path)} imports application modules: {offending}"


@pytest.mark.parametrize("path", APPLICATION_FILES, ids=lambda path: _module_name(path))
def test_application_reaches_the_authorization_server_only_through_the_composition_root(
    path: Path,
) -> None:
    name = _module_name(path)
    if name in COMPOSITION_ROOTS or name.startswith(SCRIPTS_PREFIX):
        return
    offending = sorted({module for module in _imports(path) if _matches(module, ("pfa.oauth",))})
    assert offending == [], (
        f"{name} imports the authorization server directly ({offending}); use "
        "pfa.clients.authorization_server instead"
    )


@pytest.mark.parametrize("path", ROUTE_FILES, ids=lambda path: path.name)
def test_http_handlers_do_not_read_the_database(path: Path) -> None:
    imports = _imports(path)
    offending = sorted(
        {
            module
            for module in imports
            if _matches(module, ROUTE_FORBIDDEN_IMPORTS)
            or (
                _matches(module, ("pfa.models",))
                and module != "pfa.models"
                and not _matches(module, ROUTE_ALLOWED_MODEL_MODULES)
                and not _matches(module.rsplit(".", 1)[0], ROUTE_ALLOWED_MODEL_MODULES)
            )
        }
    )
    assert offending == [], (
        f"{_module_name(path)} reaches the database directly ({offending}); move the "
        "query into a service and take the unit of work from pfa.api.deps"
    )


SHARED_FILES = [
    path for path in _python_files(PACKAGE) if _matches(_module_name(path), SHARED_INFRASTRUCTURE)
]


@pytest.mark.parametrize("path", SHARED_FILES, ids=lambda path: _module_name(path))
def test_shared_infrastructure_depends_on_neither_service(path: Path) -> None:
    offending = sorted(
        {
            module
            for module in _imports(path)
            if _is_pfa_module(module)
            and module != "pfa"
            and not _matches(module, SHARED_INFRASTRUCTURE)
            and not _matches(module.rsplit(".", 1)[0], SHARED_INFRASTRUCTURE)
        }
    )
    assert offending == [], f"{_module_name(path)} imports a service module: {offending}"


def _oauth_tables() -> set[str]:
    return set(OAuthBase.metadata.tables)


def test_every_authorization_server_table_is_prefixed_and_has_its_own_metadata() -> None:
    tables = _oauth_tables()
    assert tables, "pfa.oauth.models defines no tables"
    assert all(name.startswith("oauth_") for name in tables), sorted(tables)
    # the application's metadata (its database) holds none of them, nor the prefix
    assert not any(name.startswith("oauth_") for name in Base.metadata.tables)


def test_no_foreign_key_leaves_the_oauth_database() -> None:
    oauth_tables = _oauth_tables()
    leaving = [
        f"{table.name}.{fk.parent.name} -> {fk.target_fullname}"
        for table in OAuthBase.metadata.tables.values()
        for fk in table.foreign_keys
        if fk.target_fullname.split(".")[0] not in oauth_tables
    ]
    assert leaving == []


@pytest.mark.parametrize("path", OAUTH_FILES, ids=lambda path: path.name)
def test_authorization_server_never_opens_the_application_database(path: Path) -> None:
    offending = sorted({m for m in _imports(path) if m in APPLICATION_DATABASE})
    assert offending == [], f"{_module_name(path)} uses the application database: {offending}"


def test_oauth_tables_are_queried_only_by_the_authorization_server() -> None:
    pattern = re.compile(r"\b(" + "|".join(sorted(_oauth_tables())) + r")\b")
    offending = [
        f"{_module_name(path)}: {match.group(0)}"
        for path in APPLICATION_FILES
        if _module_name(path) != DATA_MOVE_SCRIPT
        for match in pattern.finditer(path.read_text(encoding="utf-8"))
    ]
    assert offending == []


def test_authorization_server_never_queries_application_tables() -> None:
    oauth_tables = _oauth_tables()
    application_tables = sorted(name for name in Base.metadata.tables if name not in oauth_tables)
    pattern = re.compile(
        r"\b(?:FROM|JOIN|INTO|UPDATE|REFERENCES)\s+(?:public\.)?("
        + "|".join(application_tables)
        + r")\b",
        re.IGNORECASE,
    )
    offending = [
        f"{_module_name(path)}: {match.group(0)}"
        for path in OAUTH_FILES
        for match in pattern.finditer(path.read_text(encoding="utf-8"))
    ]
    assert offending == []
