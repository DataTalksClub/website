"""Published tour editorial copy, independent of the catalogue's release."""

from django.db.models import F

from content.models import ContentDocument

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
