"""Export the versioned OpenAPI document.

This is the backend's published contract artifact (``backend/openapi.json``).
The frontend is a separate project that pins its own copy of this document to
generate TypeScript types; see ``frontend/README.md`` for the sync procedure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pfa.main import create_app


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) > 1:
        print("usage: python -m pfa.scripts.export_openapi [target]", file=sys.stderr)
        return 2
    if argv:
        target = Path(argv[0])
    else:
        target = Path(__file__).resolve().parents[2] / "openapi.json"
    app = create_app()
    document = app.openapi()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths = len(document.get("paths", {}))
    print(f"Wrote {target} ({paths} paths, version {document['info']['version']})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
