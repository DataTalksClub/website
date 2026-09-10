"""Mechanical derivation of a course family's slug/title from one edition.

There is no reviewed identity table here on purpose.  Every course repository's
``course.yaml`` already declares its own canonical family slug directly (``de-zoomcamp``,
``ml-zoomcamp``, ``llm-zoomcamp``, ``mlops-zoomcamp``, ``sma-zoomcamp``,
``ai-dev-tools-zoomcamp`` all keep whatever their repository declares), so the real
course-repository import path (``curriculum_import.py``) uses that slug as-is and needs
no normalization step at all.

What genuinely needs deriving is a *legacy edition slug* (``"de-zoomcamp-2022"``,
``"ai-dev-tools-2025"``) down to a family and a year, for the handful of importers that
still speak that older shape (CMP exports, the pinned local-dev fixture).  That is a
mechanical string operation -- strip the trailing ``-YYYY`` -- not a fact that needs
curating, so it lives here as a pure function. A caller that knows about a specific
source's own irregular slugs (for example CMP's ``ai-dev-tools-2025``, which omits the
``-zoomcamp`` its real family carries) is expected to correct for that itself, right next
to the one-time script that reads that source -- see ``scripts/prod/import_cmp_content.py``
-- rather than this module guessing on its behalf.
"""

from __future__ import annotations

import re

_EDITION_SLUG = re.compile(r"^(?P<family>.+)-(?P<year>\d{4})$")


class UnparseableEditionSlug(ValueError):
    """A slug that does not end in ``-<family>-YYYY`` and cannot be split."""


def family_and_year_from_edition_slug(edition_slug: str) -> tuple[str, int]:
    """Split a legacy ``<family>-<year>`` edition slug into its two parts.

    Raises :class:`UnparseableEditionSlug` rather than guessing when the slug does not
    end in a four-digit year -- a caller that wants a value for that case must supply one
    itself instead of accepting an invented family.
    """

    match = _EDITION_SLUG.match(edition_slug)
    if match is None:
        raise UnparseableEditionSlug(edition_slug)
    return match.group("family"), int(match.group("year"))


def family_title_from_edition_title(edition_title: str) -> str:
    """Strip a trailing year off one edition's own title to get the family's.

    Mirrors ``Cohort.save()``'s fallback derivation: the family title is read from real
    source data (the edition's own title), never from a curated table.
    """

    return re.sub(r"\s+\d{4}$", "", edition_title).strip()
