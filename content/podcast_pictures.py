"""Where the generated podcast episode pictures are served (``PodcastEpisodePicture``).

An episode's picture is served at ``/podcast/<source slug>/<route>``:
``picture`` is the 1000 x 1000 card, ``poster`` the 1280 x 720 video poster and
``social`` the 1200 x 630 ``og:image``.
"""

from __future__ import annotations

from .models import PodcastEpisodePicture

Variant = PodcastEpisodePicture.Variant

#: The public route segment of each stored variant.
ROUTE_BY_VARIANT: dict[str, str] = {
    Variant.CARD: "picture",
    Variant.POSTER: "poster",
    Variant.SOCIAL: "social",
}
VARIANT_BY_ROUTE: dict[str, str] = {route: variant for variant, route in ROUTE_BY_VARIANT.items()}


def picture_path(slug: str, variant: str) -> str:
    return f"/podcast/{slug}/{ROUTE_BY_VARIANT[variant]}"


def slugs_with(variant: str) -> frozenset[str]:
    """Every episode slug that has a stored picture of ``variant`` (one query)."""

    return frozenset(
        PodcastEpisodePicture.objects.filter(variant=variant).values_list("episode_slug", flat=True)
    )


def stored_path(slug: str, variant: str) -> str:
    """The episode's picture path for ``variant``, or ``""`` when none is stored."""

    exists = PodcastEpisodePicture.objects.filter(episode_slug=slug, variant=variant).exists()
    return picture_path(slug, variant) if exists else ""
