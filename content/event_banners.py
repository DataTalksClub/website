"""Code-owned public artwork for the first DataTalks.Club event banner rollout.

The event projection intentionally stays source-backed and immutable.  Generated artwork is
website-owned presentation data, so it is mapped by the projection's stable event identity rather
than by a mutable title slug.  New generated assets can be added here without changing the event
content contract or reintroducing provider data into the public projection.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.templatetags.static import static

EVENT_BANNER_FILENAMES: dict[str, str] = {
    # Issue #312: representative 1000 x 1000 Luma-style artwork generated from the
    # DataTalks.Club social kit for a webinar, podcast, and workshop.
    "72ff7302-8567-4ba4-ab37-fa880807f073": "ai-dev-tools-zoomcamp-2026-course-launch.png",
    "018ac4d8-358f-43b9-b9f5-77b874da95e9": "engineering-your-own-ai-assistant.png",
    "75722536-79be-44b0-bde9-17e90dd3a967": "running-durable-agents-in-production.png",
}


def event_banner_url(event: Mapping[str, Any]) -> str:
    """Return the static artwork URL for an event, or an empty fallback value.

    The production build resolves the code-owned filename through the static manifest.  Local
    development can run before ``collectstatic`` has created that manifest, so it falls back to
    the stable unhashed URL instead of turning a public event page into a 500.
    """

    identity_id = event.get("identity_id")
    if not isinstance(identity_id, str):
        return ""
    filename = EVENT_BANNER_FILENAMES.get(identity_id)
    if not filename:
        return ""
    asset_path = f"core/event-banners/{filename}"
    try:
        return static(asset_path)
    except ValueError:
        return f"/static/{asset_path}"
