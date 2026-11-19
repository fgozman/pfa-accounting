"""Run the browser suite against a real stack (integration harness, root).

This is the **integration** entry point: it wires the two independent projects
together for a full-product end-to-end run, starting, in order:

* the isolated test Compose PostgreSQL project (separate project, volume and port);
* migrations against the test database;
* the idempotent development seed (it registers the SPA OAuth client and its exact
  callback URI, without which no browser login can succeed);
* the API in ``PFA_ENV=test`` with the deterministic fake ANAF driver;
* the durable worker (so e-mail capture and job processing are real);
* the SPA dev server;

then runs the frontend's Playwright suite (extra arguments are passed through,
e.g. ``python scripts/e2e.py app.spec.ts --project=desktop-chromium``) and tears everything down again,
without touching the development database or volume.  Neither project knows about
the other; only this harness does.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
FRONTEND_ROOT = REPO_ROOT / "frontend"
TEST_PROJECT = "pfa-accounting-test"
TEST_PORT = os.environ.get("TEST_POSTGRES_PORT", "5433")
API_PORT = "8010"
SPA_PORT = "5180"


def test_env(e2e_api: str, e2e_spa: str) -> dict[str, str]:
    env = os.environ.copy()
    db = env.get("TEST_POSTGRES_DB", "pfa_test")
    owner_user = env.get("TEST_POSTGRES_OWNER_USER", "pfa_test_owner")
    owner_password = env.get("TEST_POSTGRES_OWNER_PASSWORD", "test_owner_password")
    app_user = env.get("TEST_POSTGRES_APP_USER", "pfa_test_app")
    app_password = env.get("TEST_POSTGRES_APP_PASSWORD", "test_app_password")
    env.update(
        {
            "PFA_ENV": "test",
            "PFA_DATABASE_URL": env.get(
                "PFA_TEST_DATABASE_URL",
                f"postgresql+asyncpg://{app_user}:{app_password}@127.0.0.1:{TEST_PORT}/{db}",
            ),
            "PFA_MIGRATION_DATABASE_URL": env.get(
                "PFA_TEST_MIGRATION_DATABASE_URL",
                f"postgresql+asyncpg://{owner_user}:{owner_password}@127.0.0.1:{TEST_PORT}/{db}",
            ),
            "PFA_APP_USER": app_user,
            "PFA_KEY_DIR": str(BACKEND_ROOT / ".run" / "e2e-keys"),
            "PFA_KEY_ACTIVE_KID": "e2e-2026-01",
            "PFA_OBJECT_STORAGE_DIR": str(BACKEND_ROOT / ".run" / "e2e-objects"),
            "PFA_EMAIL_DIR": str(BACKEND_ROOT / ".run" / "e2e-emails"),
            "PFA_ANAF_DRIVER": "fake",
            "PFA_AUTHORIZATION_ORIGIN": e2e_api,
            "PFA_SPA_ORIGIN": e2e_spa,
            "PFA_SPA_CALLBACK_URI": f"{e2e_spa}/auth/callback",
            "PFA_SPA_CLIENT_ID": "pfa-web-e2e",
            "PFA_ARGON2_TIME_COST": "1",
            "PFA_ARGON2_MEMORY_COST": "8192",
            "PFA_ARGON2_PARALLELISM": "1",
            # the SPA reads these at build/dev time
            "VITE_SPA_CLIENT_ID": "pfa-web-e2e",
            "VITE_SPA_CALLBACK_URI": f"{e2e_spa}/auth/callback",
            "VITE_API_BASE_URL": e2e_api,
            "VITE_AUTHORIZATION_ORIGIN": e2e_api,
            "E2E_BASE_URL": e2e_spa,
            "E2E_API_ORIGIN": e2e_api,
            "E2E_EMAIL_DIR": str(BACKEND_ROOT / ".run" / "e2e-emails"),
        }
    )
    return env


def run(command: list[str], *, env: dict[str, str], cwd: Path, check: bool = True) -> int:
    result = subprocess.run(command, cwd=cwd, env=env, check=False)
    if check and result.returncode != 0:
        raise SystemExit(result.returncode)
    return result.returncode


def wait_for(url: str, *, attempts: int = 60, label: str = "") -> None:
    import urllib.error
    import urllib.request

    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status < 500:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    raise SystemExit(f"timed out waiting for {label or url}")


def main() -> int:
    os.environ.setdefault("PFA_ENV", "test")
    if os.environ.get("PFA_ENV") == "production":
        print("Refusing to run e2e with PFA_ENV=production", file=sys.stderr)
        return 2

    e2e_api = f"http://127.0.0.1:{API_PORT}"
    e2e_spa = f"http://127.0.0.1:{SPA_PORT}"
    env = test_env(e2e_api, e2e_spa)

    # isolated project, database, volume and port
    run(
        [
            "docker",
            "compose",
            "-f",
            str(BACKEND_ROOT / "compose.test.yaml"),
            "-p",
            TEST_PROJECT,
            "up",
            "-d",
            "--wait",
        ],
        env=env,
        cwd=BACKEND_ROOT,
    )
    if os.environ.get("PFA_E2E_FRESH", "1") == "1":
        # deterministic start: drop and recreate the schema
        run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                str(BACKEND_ROOT / "alembic.ini"),
                "downgrade",
                "base",
            ],
            env=env,
            cwd=BACKEND_ROOT,
        )
    run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(BACKEND_ROOT / "alembic.ini"),
            "upgrade",
            "head",
        ],
        env=env,
        cwd=BACKEND_ROOT,
    )

    # the SPA can only sign in once its OAuth client and exact callback URI are
    # registered; a fresh schema has none, so seed (idempotent, refuses production)
    run([sys.executable, "-m", "pfa.scripts.seed_dev"], env=env, cwd=BACKEND_ROOT)

    email_dir = Path(env["E2E_EMAIL_DIR"])
    if email_dir.exists():
        shutil.rmtree(email_dir)
    email_dir.mkdir(parents=True, exist_ok=True)

    processes: list[subprocess.Popen] = []
    try:
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "pfa.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    API_PORT,
                ],
                cwd=BACKEND_ROOT,
                env=env,
            )
        )
        wait_for(f"{e2e_api}/healthz", label="the API")
        processes.append(
            subprocess.Popen([sys.executable, "-m", "pfa.worker"], cwd=BACKEND_ROOT, env=env)
        )
        processes.append(
            subprocess.Popen(
                ["npm", "run", "dev", "--", "--port", SPA_PORT, "--strictPort"],
                cwd=FRONTEND_ROOT,
                env=env,
            )
        )
        wait_for(e2e_spa, label="the SPA")

        return run(["npx", "playwright", "test", *sys.argv[1:]], env=env, cwd=FRONTEND_ROOT)
    finally:
        for process in processes:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                process.kill()
        print("e2e stack stopped; the test volume is kept for inspection")
        print(f"(remove it with: docker compose -f backend/compose.test.yaml -p {TEST_PROJECT} down -v)")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
