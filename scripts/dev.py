"""Start both projects for local development (integration harness, root).

Each project is independently runnable — ``make -C backend dev`` and
``make -C frontend dev`` — and this script only wires them together and derives
the SPA's public ``VITE_*`` configuration from the backend settings so the two
origins cannot drift apart in local development.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from pfa.config import get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
FRONTEND_ROOT = REPO_ROOT / "frontend"


def spa_env(env: dict[str, str]) -> dict[str, str]:
    """Public SPA configuration, taken from the backend settings.

    The SPA is a separate project and cannot read the Python settings, so the
    authorization origin, public client id and exact callback URI are injected
    here.  The mapping itself lives in the backend
    (``Settings.spa_public_config``) so the contract has a single definition;
    without it the SPA falls back to its own origin and the login button
    navigates to itself instead of the authorization service.
    """
    from pfa.config import get_settings

    spa = env.copy()
    spa.update(get_settings().spa_public_config)
    return spa


def main() -> int:
    env = os.environ.copy()
    web = spa_env(env)
    print(
        "SPA config -> "
        f"API base {web['VITE_API_BASE_URL']}, "
        f"authorization origin {web['VITE_AUTHORIZATION_ORIGIN']}, "
        f"client {web['VITE_SPA_CLIENT_ID']}, "
        f"callback {web['VITE_SPA_CALLBACK_URI']}",
        flush=True,
    )
    print(f"SPA      -> {web['VITE_SPA_CALLBACK_URI'].removesuffix('/auth/callback')}", flush=True)
    print(
        f"Mail     -> {Path(get_settings().email_dir).resolve()}  "
        "(`make mail` shows the latest captured link)",
        flush=True,
    )

    processes: list[subprocess.Popen] = [
        subprocess.Popen([sys.executable, "-m", "pfa.scripts.dev"], cwd=BACKEND_ROOT, env=env),
        subprocess.Popen(["npm", "run", "dev"], cwd=FRONTEND_ROOT, env=web),
    ]

    def shutdown(*_args) -> None:
        for process in processes:
            if process.poll() is None:
                process.terminate()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        while any(process.poll() is None for process in processes):
            time.sleep(0.5)
    except KeyboardInterrupt:  # pragma: no cover
        shutdown()
    finally:
        shutdown()
        for process in processes:
            if process.poll() is None:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    process.kill()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
