"""A small checked-in stand-in for the real course public projection.

``courses.services.local_course_seed.load_projected_courses`` reads the real
reviewed public projection (``courses.json``) to cross-check the pinned local
seed catalogue against it -- the same drift guard
``scripts/build_public_projection.py`` runs at build time.  That file now lives
outside this repository, at ``~/prod/dtc-data/content-staging/`` (see
``_docs/architecture/database-only-content.md``), so it is not available in CI
or a fresh checkout.

``courses/tests/fixtures/public_projection_courses.json`` is not invented data:
it is exactly what production exported for the twelve editions
``scripts/production_like_course_specs.json`` pins (already checked into this
repository), copied in once. Every test that exercises the seed's real
cross-check against the projection -- rather than one that only needs *some*
courses to exist -- patches ``PUBLIC_PROJECTION_PATH`` to it with the helper
below, so the check stays real without needing the external tree.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

FIXTURE_PROJECTION_PATH = (
    Path(__file__).resolve().parents[1]
    / "courses"
    / "tests"
    / "fixtures"
    / "public_projection_courses.json"
)


def patch_public_projection_path() -> mock._patch[Path]:
    """A ``mock.patch`` an affected test module starts in ``setUpModule``."""

    return mock.patch(
        "courses.services.local_course_seed.PUBLIC_PROJECTION_PATH", FIXTURE_PROJECTION_PATH
    )
