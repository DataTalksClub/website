"""Entry points for imports that read real production data.

Everything under this package touches a genuine upstream source: a CMP production
export, an event registration archive, a course repository.  Nothing that invents rows
belongs here, and nothing here may import a seeder -- the split is by *what data a
module touches*, so a reader can tell from the path whether a script is safe to point
at a scratch database.

Which database a run writes
---------------------------

Not this package's business to decide per module: ``scripts.prod.target`` owns it, and
every entry point that writes rows takes its selection from there.  ``--database PATH``
is a local SQLite file and ``--deployment-target NAME`` is a reviewed deployed one.
There is no default and no ambient way in, and a deployed write additionally needs
``--allow-production-write NAME`` naming the same target.  Read that package before
changing how anything here is configured.

Two sync models, said by the filename
-------------------------------------

``sync_*``
    **Git-synchronized.** Upstream keeps changing and we re-run this whenever it
    does.  Re-running is the normal case, not the exception.

``import_*``
    **One-time.** Frozen history, read once at migration.  Re-running is still
    safe -- every write is keyed on a natural key -- but nothing upstream is
    going to move.

So a person opening this directory can tell at a glance which scripts they would
ever run twice.  Each module also declares ``SYNC_MODEL`` and
``scripts/tests/test_prod_conventions.py`` checks that the declaration and the
filename agree, so the convention cannot quietly rot.

The course-repository curriculum is git-synchronized and has exactly one ingestion
path, ``content_sync.course_repository_ingest``, shared by the signed push webhook and
``sync_course_repositories`` below.  Registration (``sync_course_repository_sources``)
and pulling (``sync_course_repositories``) are the only entry points here for it --
neither invents a second way in; both call the same registration and ingestion
functions the webhook path calls.  Registered ``ContentSource`` rows say which
repositories exist.

Bootstrapping, and why the order matters
----------------------------------------

An importer either **bootstraps** -- it can populate a database that has no prior rows
of its own domain -- or it **reconciles**: it matches upstream rows against rows that
are already present and writes nothing where it finds no match.

Running a reconciling importer first is not an error.  It is a *silent no-op*, and
that is the trap: ``import_cmp_content`` reported success while writing nothing,
because it only updated cohorts that already existed, so the only way to get data in
was to run a placeholder seeder first and let the real import overwrite the invented
copy.  A production ingest that depends on fake rows existing first is backwards, so
that importer now creates the cohort and its family from the reviewed catalogue.

:data:`BOOTSTRAPPING_ENTRY_POINTS` records which modules bootstrap.  It is checked by
``scripts/tests/test_prod_conventions.py``, so a module that gains or loses the ability is
a deliberate edit rather than a surprise.

Course catalogue order
----------------------

Three sources write cohorts, and they are not interchangeable:

1. ``import_legacy_zoomcamp`` -- the frozen pre-2024 editions.  Nothing else has them.
2. ``sync_course_repositories`` -- the git-synchronized upstream.  It owns module and
   unit curricula.
3. ``import_cmp_content`` -- reconciles CMP's titles, homework, questions, projects
   and criteria against what the first two wrote.

CMP runs **last** because it reconciles.  The reverse order happened to work only
while no cohort was described by both CMP and a repository; the first time one is,
a CMP-first rebuild refuses on a homework slug collision.
"""

from __future__ import annotations

SYNC_MODELS = ("git-synchronized", "one-time")

# Modules that can populate a database with no prior rows of their own domain.
BOOTSTRAPPING_ENTRY_POINTS = frozenset(
    {
        "import_cmp_content",
        "import_cmp_learners",
        "import_docs",
        "import_faq",
        "import_legacy_zoomcamp",
        "import_public_content",
        "import_sponsors",
        "import_testimonials",
        "sync_content",
        "sync_course_repositories",
        "sync_course_repository_sources",
    }
)

# The declared course-catalogue order. The reconciler goes last.
COURSE_CATALOGUE_ORDER = (
    "import_legacy_zoomcamp",
    "sync_course_repositories",
    "import_cmp_content",
)

__all__ = ["BOOTSTRAPPING_ENTRY_POINTS", "COURSE_CATALOGUE_ORDER", "SYNC_MODELS"]
