"""Read testimonials for the public pages that show them, and import the reviewed set.

The homepage testimonials are real quotes from named members, checked into
``courses/homepage_testimonials.json`` with the public post each one is taken
from.  An entry may also carry a ``course`` field -- the slug of a course
family -- which routes it to that course's page instead of the homepage.  They
arrive through :func:`import_homepage_testimonials`, never through a
migration: a migration describes the shape of the database, and re-running it on
a database an editor has since curated would either fight the editor or refuse.
An import keyed on the placement and the source link can be replayed, and
leaves anything an editor has added alone.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import DatabaseError, transaction

from courses.models import Course, Testimonial, TestimonialPlacement

logger = logging.getLogger(__name__)


_REQUIRED_FIELDS = ("name", "attribution", "quote", "source_url", "portrait_asset_key")


class TestimonialImportError(ValueError):
    """The reviewed testimonial file is missing, malformed, or ambiguous."""


@dataclass(frozen=True, slots=True)
class TestimonialImportReport:
    total: int
    created: int
    updated: int

    @property
    def replayed(self) -> bool:
        return self.created == 0 and self.updated == 0


def load_reviewed_homepage_testimonials(source: Path) -> tuple[dict[str, str], ...]:
    """Parse and validate a reviewed set without touching the database.

    The caller supplies the location. Testimonials are database rows; a reviewed
    file is one-time ingestion input, and where that input sits is a fact about
    the ingest rather than about this module.

    An entry's ``course`` field is optional and defaults to ``""``, which means
    the homepage.  A non-empty value is a course family slug, checked against
    the database at import time -- not here, since this function never touches
    the database.
    """

    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TestimonialImportError("reviewed_testimonials_unavailable") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise TestimonialImportError("reviewed_testimonials_schema_invalid")
    entries = payload.get("testimonials")
    if not isinstance(entries, list) or not entries:
        raise TestimonialImportError("reviewed_testimonials_empty")
    parsed: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict) or any(
            not isinstance(entry.get(field), str) for field in _REQUIRED_FIELDS
        ):
            raise TestimonialImportError("reviewed_testimonial_shape_invalid")
        if not entry["source_url"]:
            raise TestimonialImportError("reviewed_testimonial_source_url_missing")
        try:
            URLValidator(schemes=["https", "http"])(entry["source_url"])
        except ValidationError as error:
            raise TestimonialImportError("reviewed_testimonial_source_url_invalid") from error
        course = entry.get("course", "")
        if not isinstance(course, str):
            raise TestimonialImportError("reviewed_testimonial_shape_invalid")
        placement = entry.get("placement", "course" if course else "homepage")
        if placement not in TestimonialPlacement.values or (placement == "course") != bool(course):
            raise TestimonialImportError("reviewed_testimonial_placement_invalid")
        parsed.append(
            {
                **{field: entry[field] for field in _REQUIRED_FIELDS},
                "course": course,
                "placement": placement,
            }
        )
    links = [(entry["placement"], entry["course"], entry["source_url"]) for entry in parsed]
    if len(set(links)) != len(links):
        raise TestimonialImportError("reviewed_testimonial_source_url_duplicated")
    return tuple(parsed)


@transaction.atomic
def import_homepage_testimonials(path: Path) -> TestimonialImportReport:
    """Apply the reviewed set, keyed on its placement and the public post it came from.

    An entry with no ``course`` becomes a homepage row; one that names a course
    family slug becomes that family's course row, positioned among its own
    course's entries rather than the homepage's.  An unknown slug is refused
    rather than silently dropped, since a mistyped slug would otherwise leave a
    real quote unpublished with no error.

    Replaying writes nothing.  Rows an editor added by hand are untouched: this
    only claims the (placement, source link) pairs the reviewed file names.
    """

    entries = load_reviewed_homepage_testimonials(path)
    created = updated = 0
    positions: dict[str, int] = {}
    for entry in entries:
        course_slug = entry["course"]
        course = None
        placement = entry["placement"]
        if course_slug:
            course = Course.objects.filter(slug=course_slug).first()
            if course is None:
                raise TestimonialImportError(f"reviewed_testimonial_course_unknown:{course_slug}")
            placement = TestimonialPlacement.COURSE
        scope = f"{placement}:{course_slug}"
        position = positions.get(scope, 0)
        positions[scope] = position + 1

        values = {
            "course": course,
            "name": entry["name"],
            "attribution": entry["attribution"],
            "quote": entry["quote"],
            "portrait_asset_key": entry["portrait_asset_key"],
            "position": position,
            "published": True,
        }
        existing = Testimonial.objects.filter(
            placement=placement,
            source_url=entry["source_url"],
        ).first()
        if existing is None:
            Testimonial.objects.create(
                placement=placement,
                source_url=entry["source_url"],
                **values,
            )
            created += 1
            continue
        changed = {}
        for field, value in values.items():
            if field == "course":
                current, incoming = existing.course_id, course.pk if course else None
            else:
                current, incoming = getattr(existing, field), value
            if current != incoming:
                changed[field] = value
        if changed:
            Testimonial.objects.filter(pk=existing.pk).update(**changed)
            updated += 1
    return TestimonialImportReport(total=len(entries), created=created, updated=updated)


def homepage_testimonials() -> tuple[Testimonial, ...]:
    """Every published homepage testimonial, in the order an editor set.

    One query, no relation walk: a homepage testimonial carries no course, so
    nothing here can turn the anonymous, edge-cacheable homepage into an N+1.

    Returns nothing when the database is empty or unreachable.  ``/`` and
    ``/unified/`` must render on a container that has no database at all, and
    the template drops the whole band rather than showing an empty one.
    """

    try:
        return tuple(
            Testimonial.objects.filter(
                placement=TestimonialPlacement.HOMEPAGE,
                published=True,
            ).order_by("position", "id")
        )
    except DatabaseError:
        logger.warning("Testimonial read failed; rendering the homepage without the band.")
        return ()


def tour_testimonials() -> tuple[Testimonial, ...]:
    """The first three published tour stories, in editor order; no fallback."""
    return tuple(
        Testimonial.objects.filter(placement=TestimonialPlacement.TOUR, published=True)
        .exclude(source_url="")
        .order_by("position", "id")[:3]
    )
