"""Stable course-family convergence shared by the repair migration and its tests.

This module is deliberately self-contained.  A migration replays against historical
models long after the reviewed catalogue has moved on, so the identity rule is
frozen here rather than read from ``courses.course_family_catalog``.  A current test
asserts the two still agree.

Why the repair exists: the curriculum importer keyed the family row on the
repository's own course slug, so ``DataTalksClub/ai-dev-tools-zoomcamp`` created a
second ``ai-dev-tools-zoomcamp`` family beside the published ``ai-dev-tools`` one
and orphaned the 2026 cohort under it.  The importer now normalizes to the
published slug, but a succeeded import run replays instead of re-applying, so an
existing database only converges here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Frozen when the AI Dev Tools families were merged.  A course repository may append
# this suffix to the published course slug because the repository is named after the
# Zoomcamp brand; two family slugs differing only by it are always one course.
REPOSITORY_FAMILY_SUFFIX = "-zoomcamp"

# Source-managed family columns.  The surviving row must keep the values the importer
# wrote, otherwise the merged family would silently fall back to the sparser seeded
# copy for as long as the succeeded import run replays.
_ADOPTED_FIELDS = (
    "title",
    "description",
    "outcome",
    "github_repo_url",
    "docs_url",
    "faq_document_url",
    "social_media_hashtag",
    "visible",
    "source_stable_id",
    "source_content_id",
    "source_path",
    "source_commit_sha",
    "source_checksum",
)
_RELEASED_SOURCE_FIELDS = (
    "source_stable_id",
    "source_content_id",
    "source_path",
    "source_commit_sha",
    "source_checksum",
)


class CourseFamilyMergeError(RuntimeError):
    """A duplicate family pair that a human, not a migration, must resolve."""


@dataclass(frozen=True)
class MergedFamily:
    """One repaired course family."""

    canonical_slug: str
    removed_slugs: tuple[str, ...]
    moved_cohort_slugs: tuple[str, ...]
    renamed_cohort_slugs: tuple[tuple[str, str], ...]


def family_identity(family_slug: str) -> str:
    """Return the identity two family slugs must share to be the same course."""

    return family_slug.removesuffix(REPOSITORY_FAMILY_SUFFIX) or family_slug


def duplicate_family_identities(family_slugs) -> dict[str, tuple[str, ...]]:
    """Group family slugs that collapse to the same course identity."""

    grouped: dict[str, list[str]] = {}
    for slug in family_slugs:
        grouped.setdefault(family_identity(slug), []).append(slug)
    return {
        identity: tuple(sorted(slugs))
        for identity, slugs in sorted(grouped.items())
        if len(set(slugs)) > 1
    }


def merge_duplicate_course_families(
    course_model: Any,
    cohort_model: Any,
) -> tuple[MergedFamily, ...]:
    """Collapse every set of family rows that share one course identity.

    The repair is idempotent: a database with one family per course is untouched.
    """

    families = {row.slug: row for row in course_model.objects.all()}
    merged: list[MergedFamily] = []
    for identity, slugs in duplicate_family_identities(families).items():
        canonical = families.get(identity)
        if canonical is None:
            raise CourseFamilyMergeError(
                f"no published family row for identity {identity!r}; found {list(slugs)}"
            )
        duplicates = [families[slug] for slug in slugs if slug != identity]
        merged.append(_merge_into(canonical, duplicates, cohort_model))
    return tuple(merged)


def _merge_into(canonical: Any, duplicates: list[Any], cohort_model: Any) -> MergedFamily:
    moved: list[str] = []
    renamed: list[tuple[str, str]] = []
    for duplicate in duplicates:
        _adopt_source_identity(canonical, duplicate)
        for cohort in cohort_model.objects.filter(course=duplicate).order_by("slug"):
            _assert_mergeable(canonical, cohort, cohort_model)
            new_slug = _canonical_cohort_slug(canonical.slug, duplicate.slug, cohort.slug)
            if new_slug != cohort.slug:
                if cohort_model.objects.filter(slug=new_slug).exclude(pk=cohort.pk).exists():
                    raise CourseFamilyMergeError(
                        f"cohort slug {new_slug!r} already exists; cannot rename "
                        f"{cohort.slug!r} while merging into {canonical.slug!r}"
                    )
                renamed.append((cohort.slug, new_slug))
                cohort.slug = new_slug
            moved.append(new_slug)
            cohort.course = canonical
            cohort.save(update_fields=["course", "slug"])
    canonical.save()
    removed_slugs = tuple(duplicate.slug for duplicate in duplicates)
    for duplicate in duplicates:
        duplicate.delete()
    return MergedFamily(
        canonical_slug=canonical.slug,
        removed_slugs=removed_slugs,
        moved_cohort_slugs=tuple(moved),
        renamed_cohort_slugs=tuple(renamed),
    )


def _adopt_source_identity(canonical: Any, duplicate: Any) -> None:
    """Move the imported family's source identity onto the published row."""

    if duplicate.source_stable_id is None:
        return
    if canonical.source_stable_id not in (None, "", duplicate.source_stable_id):
        raise CourseFamilyMergeError(
            f"family {canonical.slug!r} is owned by source {canonical.source_stable_id!r} "
            f"but {duplicate.slug!r} is owned by {duplicate.source_stable_id!r}"
        )
    for field in _ADOPTED_FIELDS:
        setattr(canonical, field, getattr(duplicate, field))
    # No two rows may hold one source identity, not even between the two saves,
    # so release it from the row that is about to be deleted.
    for field in _RELEASED_SOURCE_FIELDS:
        setattr(duplicate, field, None)
    duplicate.save()


def _assert_mergeable(canonical: Any, cohort: Any, cohort_model: Any) -> None:
    """Refuse rather than discard a cohort the canonical family already holds."""

    siblings = cohort_model.objects.filter(course=canonical)
    if siblings.filter(identifier=cohort.identifier).exists():
        raise CourseFamilyMergeError(
            f"family {canonical.slug!r} already has cohort identifier "
            f"{cohort.identifier!r}; resolve {cohort.slug!r} by hand"
        )
    if siblings.filter(year=cohort.year).exists():
        raise CourseFamilyMergeError(
            f"family {canonical.slug!r} already has a {cohort.year} cohort; "
            f"resolve {cohort.slug!r} by hand"
        )


def _canonical_cohort_slug(canonical_slug: str, duplicate_slug: str, cohort_slug: str) -> str:
    """Re-prefix a cohort slug that carried the duplicate family's slug."""

    prefix = f"{duplicate_slug}-"
    if cohort_slug.startswith(prefix):
        return f"{canonical_slug}-{cohort_slug.removeprefix(prefix)}"
    return cohort_slug
