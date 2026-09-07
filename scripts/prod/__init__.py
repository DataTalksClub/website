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

CMP learner order
-----------------

The CMP export also carries the learners, and those three legs are even less
interchangeable.  :data:`CMP_LEARNER_ORDER` records them: ``import_cmp_content``,
then ``import_cmp_learners``, then ``import_cmp_learner_history``.

The history importer reconciles every foreign key it writes against what the first
two wrote -- cohorts, homework, questions, projects and criteria from the content
import, accounts from the learner import -- and it resolves its learners through the
accounts claims file ``import_cmp_learners`` leaves behind, which it is pointed at
with ``--user-claims-file``.  So a reordered run is not an error: it is a silent
partial import that reports success while skipping every row whose parent is not
there yet.  ``scripts/tests/test_prod_make_targets.py`` checks the Makefile runs the
two learner legs in this order.

Reachable from the Makefile, or excluded with a reason
------------------------------------------------------

Every module here is either invoked by a Makefile recipe or named in
:data:`MAKE_TARGET_EXCLUSIONS` with the reason it is deliberately reachable only by
a typed command.  ``scripts/tests/test_prod_make_targets.py`` checks the closure, so
the next entry point that lands cannot quietly become runbook-only prose that nothing
executes and nothing holds to an order.

Being excluded is a statement about *who may start the run*, not about safety: an
importer that reads attendee-level personal data or merges real accounts should cost
a deliberate command, not ride along inside a rebuild.
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

# The declared CMP learner order. The history importer reconciles against both
# of the legs before it and reads the claims file the second one writes.
CMP_LEARNER_ORDER = (
    "import_cmp_content",
    "import_cmp_learners",
    "import_cmp_learner_history",
)

# Modules that deliberately have no Makefile target: module name -> why. An entry
# here is a decision someone has to argue with, not an omission.
MAKE_TARGET_EXCLUSIONS: dict[str, str] = {
    "import_event_registrants": (
        "Reads attendee-level personal data and provider credentials, so it stays a "
        "deliberate, separately invoked run rather than something a local rebuild "
        "does on its way past. Same reason the Makefile records above "
        "production-prep-bootstrap for step 6."
    ),
    "import_mailchimp_event_tags": (
        "Needs Mailchimp provider credentials and reads subscriber-level personal "
        "data. Step 6, excluded for the reason above production-prep-bootstrap."
    ),
    "import_mailchimp_subscriptions": (
        "Needs Mailchimp provider credentials and reads subscriber-level personal "
        "data. Step 6, excluded for the reason above production-prep-bootstrap."
    ),
    "sync_content": (
        "Not decided yet. Its absence from the Makefile is recorded here rather "
        "than left silent; deciding whether it gets a target belongs to the ingest "
        "consolidation epic, DataTalksClub/website#310."
    ),
    "sync_public_media_hydrate": (
        "Not decided yet. The Makefile names it only in a comment, which is not a "
        "way to run it; deciding whether it gets a target belongs to the ingest "
        "consolidation epic, DataTalksClub/website#310."
    ),
    "sync_public_media_publish": (
        "Not decided yet. Its absence from the Makefile is recorded here rather "
        "than left silent; deciding whether it gets a target belongs to the ingest "
        "consolidation epic, DataTalksClub/website#310."
    ),
    "sync_public_media_verify": (
        "Not decided yet. Its absence from the Makefile is recorded here rather "
        "than left silent; deciding whether it gets a target belongs to the ingest "
        "consolidation epic, DataTalksClub/website#310."
    ),
}

__all__ = [
    "BOOTSTRAPPING_ENTRY_POINTS",
    "CMP_LEARNER_ORDER",
    "COURSE_CATALOGUE_ORDER",
    "MAKE_TARGET_EXCLUSIONS",
    "SYNC_MODELS",
]
