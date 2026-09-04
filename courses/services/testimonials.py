"""Read testimonials for the public pages that show them, and import the reviewed set.

The six homepage testimonials are real quotes from named members, checked into
``courses/homepage_testimonials.json`` with the public post each one is taken
from.  They arrive through :func:`import_homepage_testimonials`, never through a
migration: a migration describes the shape of the database, and re-running it on
a database an editor has since curated would either fight the editor or refuse.
An import keyed on the source link can be replayed, and leaves anything an
editor has added alone.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from django.db import DatabaseError, transaction

from courses.models import Testimonial, TestimonialPlacement

logger = logging.getLogger(__name__)

REVIEWED_TESTIMONIALS_PATH = Path(__file__).resolve().parents[1] / "homepage_testimonials.json"

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


def load_reviewed_homepage_testimonials(path: Path | None = None) -> tuple[dict[str, str], ...]:
    """Parse and validate the checked reviewed set without touching the database."""

    source = path or REVIEWED_TESTIMONIALS_PATH
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
        parsed.append({field: entry[field] for field in _REQUIRED_FIELDS})
    links = [entry["source_url"] for entry in parsed]
    if len(set(links)) != len(links):
        raise TestimonialImportError("reviewed_testimonial_source_url_duplicated")
    return tuple(parsed)


@transaction.atomic
def import_homepage_testimonials(path: Path | None = None) -> TestimonialImportReport:
    """Apply the reviewed homepage set, keyed on the public post it came from.

    Replaying writes nothing.  Rows an editor added by hand are untouched: this
    only claims the source links the reviewed file names.
    """

    entries = load_reviewed_homepage_testimonials(path)
    created = updated = 0
    for position, entry in enumerate(entries):
        values = {
            "course": None,
            "name": entry["name"],
            "attribution": entry["attribution"],
            "quote": entry["quote"],
            "portrait_asset_key": entry["portrait_asset_key"],
            "position": position,
            "published": True,
        }
        existing = Testimonial.objects.filter(
            placement=TestimonialPlacement.HOMEPAGE,
            source_url=entry["source_url"],
        ).first()
        if existing is None:
            Testimonial.objects.create(
                placement=TestimonialPlacement.HOMEPAGE,
                source_url=entry["source_url"],
                **values,
            )
            created += 1
            continue
        changed = {
            field: value
            for field, value in values.items()
            if getattr(existing, "course_id" if field == "course" else field) != value
        }
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
