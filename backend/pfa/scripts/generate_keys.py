"""Generate (or rotate) the JWT signing key outside PostgreSQL.

    python -m pfa.scripts.generate_keys            # create if missing
    python -m pfa.scripts.generate_keys --rotate    # new key, old one retired
                                                    # after the rotation overlap

Private material is written with mode 0600 and never leaves the key directory.
Production uses a managed secret/key service instead of this script.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta

from pfa.config import get_settings
from pfa.security.keys import KeyStore, KeyStoreError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage development signing keys")
    parser.add_argument(
        "--rotate",
        action="store_true",
        help="create a new active key and retire the previous ones after the overlap",
    )
    parser.add_argument("--kid", default=None, help="key id (default: configured kid)")
    args = parser.parse_args(argv)

    settings = get_settings()
    if settings.is_production:
        print(
            "Refusing to write local signing keys with PFA_ENV=production; "
            "use the managed key service.",
            file=sys.stderr,
        )
        return 2

    directory = settings.key_dir
    kid = args.kid or settings.key_active_kid
    try:
        existing = KeyStore.load(directory, settings.key_active_kid)
    except KeyStoreError:
        existing = None

    if existing is not None and not args.rotate:
        print(f"Key store already present at {directory} (active kid: {existing.active_kid})")
        return 0

    if existing is not None and args.rotate:
        now = datetime.now(tz=UTC)
        new_kid = args.kid or f"{now.strftime('%Y-%m-%d')}-{int(now.timestamp()) % 100000}"
        store = KeyStore.generate(
            directory,
            new_kid,
            retire_kids=[existing.active_kid],
            now=now,
            overlap=timedelta(hours=settings.key_rotation_overlap_hours),
        )
        print(
            f"Rotated: active kid {store.active_kid}; "
            f"{existing.active_kid} remains valid for verification for "
            f"{settings.key_rotation_overlap_hours}h"
        )
        return 0

    store = KeyStore.generate(directory, kid)
    print(f"Created signing key {store.active_kid} in {directory}")
    print("Public JWKS is served from /.well-known/jwks.json")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
