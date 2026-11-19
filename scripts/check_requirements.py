"""Feature-coverage traceability check (integration tool, repository root).

Runs against the shared ``tests/requirements.yaml`` and both projects:

* ``backend/`` — pytest node ids and the Alembic migrations;
* ``frontend/`` — component and Playwright test files.

Fails when:

* the requirements file is missing or malformed;
* an ``implemented`` requirement has no mapped test;
* a mapped backend test node id does not exist;
* a mapped frontend/e2e file does not exist;
* the PostgreSQL migrations do not apply cleanly to the target database.

Coverage percentages supplement this mapping; they do not prove completeness.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
FRONTEND_ROOT = REPO_ROOT / "frontend"
REQUIREMENTS = REPO_ROOT / "tests" / "requirements.yaml"
VALID_STATUS = {"implemented", "partial", "external", "planned"}


def load_requirements() -> dict:
    try:
        import yaml  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        # Minimal fallback parser is not worth maintaining: require PyYAML.
        print(
            "PyYAML is required to check tests/requirements.yaml (pip install pyyaml)",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
    return yaml.safe_load(REQUIREMENTS.read_text(encoding="utf-8"))


def collect_pytest_nodes() -> set[str]:
    """Collect backend node ids and normalise them to repository-relative form.

    pytest reports ``tests/test_x.py::name`` when run from ``backend/``; the
    requirements file uses the repository-relative ``backend/tests/...`` form.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    nodes: set[str] = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if re.match(r"^[a-zA-Z_][\w/]*\.py::", line):
            nodes.add(f"backend/{line.split(' ', 1)[0]}")
    return nodes


def main() -> int:
    if not REQUIREMENTS.exists():
        print("tests/requirements.yaml is missing", file=sys.stderr)
        return 1

    data = load_requirements()
    nodes = collect_pytest_nodes()
    problems: list[str] = []
    checked = 0

    _debug_nodes = nodes
    def check_tests(owner: str, tests: list[str] | None) -> int:
        count = 0
        with open("/tmp/check_debug.txt", "a") as fh:
            fh.write(f"{owner}: nodes={len(_debug_nodes)}\n")
        for test in tests or []:
            count += 1
            if "::" in test:
                if test not in nodes:
                    problems.append(f"{owner}: test node not found: {test}")
                continue
            candidates = [
                BACKEND_ROOT / "tests" / Path(test).name,
                REPO_ROOT / test,
                FRONTEND_ROOT / test,
            ]
            if not any(candidate.exists() for candidate in candidates):
                problems.append(f"{owner}: test file not found: {test}")
        return count

    for name, spec in (data.get("requirements") or {}).items():
        status = spec.get("status", "planned")
        if status not in VALID_STATUS:
            problems.append(f"{name}: unknown status {status!r}")
        tests = spec.get("tests") or []
        checked += check_tests(name, tests)
        if status == "implemented" and not tests:
            problems.append(f"{name}: marked implemented but has no mapped tests")
        if status == "partial" and not (spec.get("gaps") or []):
            problems.append(f"{name}: marked partial but no gaps are documented")
        if status == "external" and not (spec.get("gaps") or []):
            problems.append(f"{name}: marked external but no reason is documented")

    for name, spec in (data.get("acceptance_scenarios") or {}).items():
        checked += check_tests(name, spec.get("tests"))
        if not (spec.get("tests") or []):
            problems.append(f"{name}: acceptance scenario has no mapped tests")

    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    print(
        f"checked {len(data.get('requirements') or {})} requirements and "
        f"{len(data.get('acceptance_scenarios') or {})} acceptance scenarios, "
        f"{checked} test references"
    )
    if problems:
        return 1
    if os.environ.get("PFA_SKIP_MIGRATION_CHECK") != "1":
        migrate = subprocess.run(
            # both databases (application and OAuth), in the backend's safe order
            [sys.executable, "-m", "pfa.scripts.migrate"],
            cwd=BACKEND_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if migrate.returncode != 0:
            print("FAIL migrations do not apply:\n" + migrate.stderr[-2000:], file=sys.stderr)
            return 1
        print("migrations apply cleanly")
    print("requirements check OK")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
