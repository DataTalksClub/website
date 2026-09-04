"""The one draft rule every source-file-based adapter and builder shares.

Extracted from ``content_sync/dtc_content/adapter.py:_is_draft`` -- today the
only implementation of this rule anywhere in the codebase -- per
``.tmp/content-ingest-design.md`` section 7. Course-repository ingestion
(``content_sync/course_repository_ingest.py``,
``content_sync/course_repository.py``) has no filename-based draft concept
at all: nothing there filters paths by a leading underscore, so there was
nothing to extract from that ingest path for this rule. See the report that
accompanied this extraction.

A leading underscore marks an unpublished draft. This is the source
repository's own convention, inherited from the Jekyll site that produced
it: a file whose name starts with ``_`` is never rendered, so it has no
public URL and no legacy route contract -- and cannot acquire one, since the
crawler that built the route inventory only ever saw published pages.

The rule is the file's *basename*, never any other path component. Jekyll
collection directories such as ``_wiki``, ``_questions``, ``_people`` and
``_posts`` are themselves underscore-prefixed and are not drafts; only a
file's own name is tested. ``_people/_template.md`` is a draft.
``_wiki/kafka.md`` is published. ``_wiki/_scratch.md`` would be a draft.

``scripts/build_public_projection.py`` already applies exactly this rule,
today only for ``_people()``. This module exists so every adapter and
builder shares the identical implementation instead of each carrying its own
copy; see ``content_sync/dtc_content/adapter.py:_is_draft`` for the one
existing use this module supersedes going forward (not repointed by this
change -- see the report).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath


def is_unpublished_source_file(path: PurePosixPath | Path | str) -> bool:
    """Unpublished draft: the file's basename starts with ``_``.

    Collection directories named ``_wiki`` / ``_questions`` / ``_people`` are
    not drafts -- only the basename is tested, never any other path
    component. Applies equally to media files: an underscore-prefixed image
    has no public path either.
    """

    return PurePosixPath(path).name.startswith("_")


__all__ = ["is_unpublished_source_file"]
