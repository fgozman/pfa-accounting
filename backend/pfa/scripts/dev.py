"""Start the API and the durable worker together (``make dev``).

This is the **backend** development entry point.  It deliberately knows nothing
about the SPA: the frontend is a separate project with its own ``make dev`` /
``npm run dev``.  Run both side by side when developing the full product.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from pfa.config import get_settings


def listen_address(origin: str) -> tuple[str, int]:
    """Host and port uvicorn should bind, derived from the public origin."""
    parts = urlsplit(origin)
    return parts.hostname or "127.0.0.1", parts.port or 8000


def main() -> int:
    backend = Path(__file__).resolve().parents[2]
    settings = get_settings()
    host, port = listen_address(settings.authorization_origin)
    processes: list[subprocess.Popen] = []

    print(f"API      -> http://{host}:{port}  (docs: /docs)", flush=True)
    print("Worker   -> durable outbox / jobs / scheduled reconciliation", flush=True)

    processes.append(
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "pfa.main:app",
                "--reload",
                "--host",
                host,
                "--port",
                str(port),
            ],
            cwd=backend,
        )
    )
    processes.append(
        subprocess.Popen([sys.executable, "-m", "pfa.worker"], cwd=backend)
    )

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
