"""Run the Python test suite against the isolated test database.

    python -m pfa.scripts.run_tests [pytest args...]

Guarantees:
* the target database name must be explicitly configured as a test database
  (see ``tests/conftest.py``), so a mistyped URL cannot wipe development data;
* both databases (application and OAuth) are migrated before the suite runs;
* pytest is executed in-process with the calling environment.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

DEFAULT_TEST_PORT = "5433"


def default_test_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    port = env.get("TEST_POSTGRES_PORT", DEFAULT_TEST_PORT)
    db = env.get("TEST_POSTGRES_DB", "pfa_test")
    oauth_db = env.get("TEST_POSTGRES_OAUTH_DB", "pfa_oauth_test")
    owner_user = env.get("TEST_POSTGRES_OWNER_USER", "pfa_test_owner")
    owner_password = env.get("TEST_POSTGRES_OWNER_PASSWORD", "test_owner_password")
    app_user = env.get("TEST_POSTGRES_APP_USER", "pfa_test_app")
    app_password = env.get("TEST_POSTGRES_APP_PASSWORD", "test_app_password")
    env.setdefault("PFA_ENV", "test")
    env.setdefault(
        "PFA_DATABASE_URL",
        f"postgresql+asyncpg://{app_user}:{app_password}@127.0.0.1:{port}/{db}",
    )
    env.setdefault(
        "PFA_MIGRATION_DATABASE_URL",
        f"postgresql+asyncpg://{owner_user}:{owner_password}@127.0.0.1:{port}/{db}",
    )
    env.setdefault(
        "PFA_OAUTH_DATABASE_URL",
        f"postgresql+asyncpg://{app_user}:{app_password}@127.0.0.1:{port}/{oauth_db}",
    )
    env.setdefault(
        "PFA_OAUTH_MIGRATION_DATABASE_URL",
        f"postgresql+asyncpg://{owner_user}:{owner_password}@127.0.0.1:{port}/{oauth_db}",
    )
    env.setdefault("PFA_APP_USER", app_user)
    env.setdefault("PFA_KEY_DIR", str(root / ".run" / "test-keys"))
    env.setdefault("PFA_KEY_ACTIVE_KID", "test-2026-01")
    env.setdefault("PFA_OBJECT_STORAGE_DIR", str(root / ".run" / "test-objects"))
    env.setdefault("PFA_EMAIL_DIR", str(root / ".run" / "test-emails"))
    env.setdefault("PFA_ANAF_DRIVER", "fake")
    env.setdefault("PFA_AUTHORIZATION_ORIGIN", "http://127.0.0.1:8000")
    env.setdefault("PFA_SPA_ORIGIN", "http://127.0.0.1:5173")
    env.setdefault("PFA_SPA_CALLBACK_URI", "http://127.0.0.1:5173/auth/callback")
    env.setdefault("PFA_SPA_CLIENT_ID", "pfa-web-test")
    env.setdefault("PFA_ARGON2_TIME_COST", "1")
    env.setdefault("PFA_ARGON2_MEMORY_COST", "8192")
    env.setdefault("PFA_ARGON2_PARALLELISM", "1")
    return env


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[2]
    argv = list(argv if argv is not None else sys.argv[1:])
    env = default_test_env(root)

    migrate = subprocess.run(
        [sys.executable, "-m", "pfa.scripts.migrate"],
        cwd=root,
        env=env,
        check=False,
    )
    if migrate.returncode != 0:
        print(
            "Migrations failed. Is the isolated test database running?\n"
            "  make test-db-up",
            file=sys.stderr,
        )
        return migrate.returncode

    marker = root / ".run"
    marker.mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, "-m", "pytest", *argv],
        cwd=root,
        env=env,
        check=False,
    ).returncode


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
