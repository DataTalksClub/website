"""Published About-page editorial copy, independent of the catalogue's release.

The record is still named for the tour the About page was published as: its
source id, content kind and key are what the editorial import writes, so they
stay as they are.  Only what a visitor sees changed -- the page's address,
title and links.
"""

import logging

from django.db import DatabaseError
from django.db.models import F

from content.models import ContentDocument

logger = logging.getLogger(__name__)

TOUR_SOURCE_ID = "dtc-tour-page"


def tour_page() -> dict:
    document = (
        ContentDocument.objects.filter(
            release__source__stable_id=TOUR_SOURCE_ID,
            release__source__enabled=True,
            release_id=F("release__source__active_release_id"),
            content_kind="tour_page",
            stable_key="tour",
            is_published=True,
        )
        .only("adapter_metadata")
        .first()
    )
    return document.adapter_metadata.get("record", {}) if document else {}


def homepage_tour_page() -> dict:
    """The same record for the homepage's About band, or nothing without a database.

    The homepage is also the rendered page of the container liveness gate
    (``/unified/``), so it has to answer while the database is unreachable: the
    outage costs it the band, never the page.  The About page keeps the strict
    read above -- a page that is only this record has nothing to fall back to.
    """

    try:
        return tour_page()
    except DatabaseError:
        logger.warning("About record read failed; rendering the homepage without its About band.")
        return {}
