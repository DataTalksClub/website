"""Editorial composition for the redesigned public podcast surfaces.

The podcast index and the episode page render the "6d" mockup from
DataTalksClub/website#179 in the design 5a system.  Every fact those pages show
(titles, paths, season and episode numbers, guests, publication dates, listening
links) is read from the checked public catalogue here, and a record that cannot
supply a fact fails loudly instead of rendering an invented one.

Deliberate omissions, because the catalogue has no such field: an episode
duration, a global episode number, and a show-level subscription address.  The
mockup shows all three; the pages leave them out rather than guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from .public_data import podcast_public_path

# Listening destinations, in the order the pages offer them, with the platform
# dot the design system reserves for each.  A link key outside this map is still
# offered, with a neutral dot and its own name.
PLATFORM_LABELS: dict[str, tuple[str, str]] = {
    "apple": ("Apple Podcasts", "dot-bubble"),
    "spotify": ("Spotify", "dot-green"),
    "youtube": ("YouTube", "dot-clay"),
    "anchor": ("Anchor", "dot-gold"),
}
PLATFORM_ORDER: tuple[str, ...] = ("apple", "spotify", "youtube", "anchor")
# What the play control opens, most watchable first.
WATCH_PREFERENCE: tuple[str, ...] = ("youtube", "spotify", "apple", "anchor")


@dataclass(frozen=True, slots=True)
class Guest:
    """One named guest, linked to their profile when the catalogue has one."""

    name: str
    public_path: str


@dataclass(frozen=True, slots=True)
class PlatformLink:
    """One listening destination, with the design system's platform dot."""

    label: str
    url: str
    dot: str


@dataclass(frozen=True, slots=True)
class Episode:
    """One episode, prepared for both the index row and the episode page."""

    title: str
    description: str
    public_path: str
    season: int
    episode: int
    guests: tuple[Guest, ...]
    published: str
    published_display: str
    image_path: str
    media_available: bool
    platform_links: tuple[PlatformLink, ...]
    watch_url: str
    watch_label: str

    @property
    def season_episode(self) -> str:
        """The catalogue's own numbering, as both pages write it."""

        return f"Season {self.season} · Episode {self.episode}"

    @property
    def guest_names(self) -> str:
        return " and ".join(guest.name for guest in self.guests)


def _required_text(record: dict[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ImproperlyConfigured(f"Public podcast {field} must be a non-empty string.")
    return value


def _required_number(record: dict[str, Any], field: str) -> int:
    value = record.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ImproperlyConfigured(f"Public podcast {field} must be a positive integer.")
    return value


def _guests(record: dict[str, Any]) -> tuple[Guest, ...]:
    guests: list[Guest] = []
    for profile in record.get("guest_profiles", ()):
        name = profile.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ImproperlyConfigured("Public podcast guest must have a name.")
        public_path = profile.get("public_path") or ""
        if public_path and not str(public_path).startswith("/"):
            raise ImproperlyConfigured("Public podcast guest link must be a site path.")
        guests.append(Guest(name=name, public_path=str(public_path)))
    return tuple(guests)


def published_display(value: str) -> str:
    """Render a ``YYYY-MM-DD`` publication date the way the design writes dates.

    Seventeen catalogue entries carry no date at all; those pages simply do not
    show one, so an empty value stays empty instead of becoming a guess.
    """

    if not value:
        return ""
    try:
        published = date.fromisoformat(value[:10])
    except ValueError as error:
        raise ImproperlyConfigured("Public podcast publication date is invalid.") from error
    return f"{published:%b} {published.day}, {published:%Y}"


def _platform_links(record: dict[str, Any]) -> tuple[PlatformLink, ...]:
    links = record.get("links") or {}
    if not isinstance(links, dict):
        raise ImproperlyConfigured("Public podcast links must be a mapping.")
    known = [key for key in PLATFORM_ORDER if links.get(key)]
    extra = sorted(key for key in links if key not in PLATFORM_LABELS and links.get(key))
    prepared: list[PlatformLink] = []
    for key in (*known, *extra):
        url = str(links[key])
        if not url.startswith("https://"):
            raise ImproperlyConfigured("Public podcast listening link must be an https address.")
        label, dot = PLATFORM_LABELS.get(key, (key.replace("-", " ").title(), "dot-bubble"))
        prepared.append(PlatformLink(label=label, url=url, dot=dot))
    return tuple(prepared)


def _watch_destination(record: dict[str, Any]) -> tuple[str, str]:
    """Return the address the play control opens, and the platform it names."""

    links = record.get("links") or {}
    for key in WATCH_PREFERENCE:
        url = links.get(key)
        if url:
            return str(url), PLATFORM_LABELS[key][0]
    return "", ""


def episode_view(record: dict[str, Any]) -> Episode:
    """Return one catalogue record as the value both podcast templates render."""

    published = str(record.get("published") or "")
    watch_url, watch_label = _watch_destination(record)
    return Episode(
        title=_required_text(record, "title"),
        description=_required_text(record, "description"),
        # The catalogue's checked `/podcast/<slug>.html` identity, never a link the
        # page invents from a title.
        public_path=podcast_public_path(record),
        season=_required_number(record, "season"),
        episode=_required_number(record, "episode"),
        guests=_guests(record),
        published=published,
        published_display=published_display(published),
        image_path=str(record.get("image_path") or ""),
        media_available=bool(record.get("media_available")) and bool(record.get("image_path")),
        platform_links=_platform_links(record),
        watch_url=watch_url,
        watch_label=watch_label,
    )


def season_episodes(records: tuple[dict[str, Any], ...]) -> tuple[Episode, ...]:
    """Return one season's episodes in the order the catalogue already ordered them."""

    if not records:
        raise ImproperlyConfigured("Public podcast season must not be empty.")
    return tuple(episode_view(record) for record in records)
