"""New code in an excluded tree must be opted into the quality gates (BE-16).

``pyproject.toml`` excludes the adopted app trees from ruff and silences mypy
for them with per-package ``ignore_errors`` overrides -- the reviewed adoption
baseline.  That directory-wide history must not silently exempt future code:
every Python file under an excluded tree is either listed in the frozen
baseline manifest (``ci/adoption_baseline.txt``, the reviewed state at the
BE-16 decision) or named in ``scripts/ci.py``'s quality-gate input lists, where
lint, format, and typecheck actually reach it (strictly, via the
``ignore_errors = false`` override block in pyproject.toml).

Adding a file under ``accounts/`` without adding it to the quality script fails
this test with the exact file to change.  Removing or gutting the lists, or
deleting baseline entries to dodge the gate, also fails.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from scripts.ci import ADOPTION_INTEGRATION_PYTHON, PRODUCTION_IMPORT_PYTHON

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "ci" / "adoption_baseline.txt"
PYPROJECT = ROOT / "pyproject.toml"

#: The ADOPTION_INTEGRATION_PYTHON / PRODUCTION_IMPORT_PYTHON entries as they
#: stood at the BE-16 decision (git 39844ab8), reviewed under the per-package
#: mypy exemptions.  Everything added to the lists afterwards is new opt-in
#: code and must be strict.
HISTORICAL_OPT_INS = frozenset(
    {
        "accounts/managers.py",
        "accounts/tests/test_user.py",
        "api/auth.py",
        "api/models.py",
        "api/tests/test_admin_health.py",
        "scripts/build_local_review_db.py",
        "scripts/capture_screenshots.py",
        "scripts/check_database_portability.py",
        "scripts/render_course_platform_inventory.py",
        "scripts/verify_course_platform_adoption.py",
        "scripts/sync_course_platform.py",
        "scripts/prepare_course_platform_source.py",
        "scripts/prod",
        "courses/services/cmp_content_import.py",
        "courses/services/cmp_learner_history_import.py",
    }
)

#: The ruff ``exclude`` globs that name whole app trees.  ``*/migrations/*``
#: is excluded globally and holds no hand-written code, so it is out of scope.
TREE_EXCLUDE_GLOB_PREFIXES = (
    "accounts/",
    "api/",
    "studio_courses/",
    "course_management/",
    "courses/",
    "data/",
    "e2e/",
    "scripts/",
)


def quality_script_opt_ins() -> set[str]:
    """Return the files explicitly included by the quality script."""

    return set((*ADOPTION_INTEGRATION_PYTHON, *PRODUCTION_IMPORT_PYTHON))


def excluded_tree_files() -> set[str]:
    """Every repository Python file under a ruff-excluded app tree."""

    files: set[str] = set()
    for prefix in TREE_EXCLUDE_GLOB_PREFIXES:
        for path in (ROOT / prefix).rglob("*.py"):
            if "/migrations/" in path.as_posix():
                continue
            files.add(path.relative_to(ROOT).as_posix())
    return files


def baseline_entries() -> set[str]:
    lines = BASELINE.read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")}


def strict_mypy_modules() -> set[str]:
    """The modules the later pyproject override checks with ignore_errors off."""

    document = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    overrides = document["tool"]["mypy"]["overrides"]
    strict: set[str] = set()
    for override in overrides:
        if override.get("ignore_errors") is False:
            strict.update(override.get("module", []))
    return strict


def test_every_excluded_tree_file_is_baseline_or_opted_in() -> None:
    opt_ins = quality_script_opt_ins()
    covered = baseline_entries() | opt_ins
    unaccounted = sorted(excluded_tree_files() - covered)

    assert not unaccounted, (
        "New Python files under an excluded app tree must be added to one of "
        "scripts/ci.py so lint, format, and "
        "typecheck reach them (audit BE-16), or, only for a file that is "
        "historical adopted source rather than new integration code, to "
        f"ci/adoption_baseline.txt. Unaccounted files: {unaccounted}"
    )


def test_opt_in_entries_exist_and_are_outside_the_baseline() -> None:
    opt_ins = quality_script_opt_ins()

    missing = sorted(
        # ``scripts/prod`` is a directory entry; ruff and mypy walk it.
        entry
        for entry in opt_ins
        if not (ROOT / entry).exists()
    )
    assert not missing, f"opt-in entries no longer on disk: {missing}"

    stale = sorted(opt_ins & baseline_entries())
    assert not stale, (
        "a file may not be both a reviewed baseline exemption and a gate "
        f"opt-in; remove it from ci/adoption_baseline.txt: {stale}"
    )


def test_the_baseline_stays_inside_the_excluded_trees() -> None:
    prefixes = tuple(TREE_EXCLUDE_GLOB_PREFIXES)
    outside = sorted(
        entry
        for entry in baseline_entries()
        if not entry.startswith(prefixes) or "/migrations/" in entry
    )
    assert not outside, f"baseline entries outside the excluded trees: {outside}"


def test_opted_in_app_modules_are_typechecked_strictly() -> None:
    """A gate opt-in must not inherit its package's ignore_errors override."""

    strict = strict_mypy_modules()
    unenforced: list[str] = []
    for entry in sorted(quality_script_opt_ins()):
        posix = Path(entry).as_posix()
        if not posix.startswith(TREE_EXCLUDE_GLOB_PREFIXES):
            continue
        dotted = posix.removesuffix(".py").replace("/", ".").replace(".__init__", "")
        # The pre-BE-16 entries below (verbatim from the adopted baseline at the
        # BE-16 decision) were reviewed under the per-package exemptions and
        # are not retroactively strict; everything opted in afterwards must be
        # named in the override block.
        if dotted not in strict and posix not in HISTORICAL_OPT_INS:
            unenforced.append(dotted)

    assert not unenforced, (
        "opted-in modules must be named in the pyproject.toml "
        f"ignore_errors = false override block: {unenforced}"
    )
