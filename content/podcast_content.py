"""Editorial composition for the redesigned public podcast surfaces.

The podcast index and the episode page render the "6d" mockup from
DataTalksClub/website#179 in the design 5a system.  Every fact those pages show
(titles, paths, season and episode numbers, guests, publication dates, listening
links) is read from the checked public catalogue here, and a record that cannot
supply a fact fails loudly instead of rendering an invented one.

Deliberate omissions, because the catalogue has no such field: an episode
duration, a global episode number, and a show-level subscription address.  The
mockup shows all three; the pages leave them out rather than guess.  There is no
podcast feed anywhere in this repository either, so the index points a reader who
wants to subscribe at the platforms the episodes themselves link to, named from
the records rather than hard-coded (:func:`listening_platform_phrase`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from django.core.exceptions import ImproperlyConfigured

from .public_data import podcast_public_path

# Listening destinations, in the order the pages offer them, with the platform
# dot the design system reserves for each.  A link key outside this map is still
# offered, with a neutral dot and its own name.
PLATFORM_LABELS: dict[str, tuple[str, str]] = {
    "apple": ("Apple Podcasts", "dot-bubble"),
    "spotify": ("Spotify", "dot-green"),
    "youtube": ("YouTube", "dot-clay"),
    "spotify_for_creators": ("Spotify for Creators", "dot-gold"),
}
PLATFORM_ORDER: tuple[str, ...] = ("apple", "spotify", "youtube", "spotify_for_creators")
# What the play control opens, most watchable first.
WATCH_PREFERENCE: tuple[str, ...] = ("youtube", "spotify", "apple", "spotify_for_creators")
YOUTUBE_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
SPOTIFY_CREATOR_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,255}$")
SPOTIFY_EPISODE_ID = re.compile(r"^[A-Za-z0-9]{22}$")


@dataclass(frozen=True, slots=True)
class Guest:
    """One named guest, linked to their profile when the catalogue has one."""

    name: str
    public_path: str


@dataclass(frozen=True, slots=True)
class PlatformLink:
    """One listening destination, with the design system's platform dot."""

    provider: str
    label: str
    url: str
    dot: str

@dataclass(frozen=True, slots=True)
class VideoEmbed:
    """A validated YouTube player derived from the episode's source URL."""

    provider: str
    video_id: str

    @property
    def embed_url(self) -> str:
        if self.provider != "youtube" or not YOUTUBE_VIDEO_ID.fullmatch(self.video_id):
            raise ImproperlyConfigured("Public podcast video identity is invalid.")
        return f"https://www.youtube-nocookie.com/embed/{self.video_id}?enablejsapi=1&rel=0"

    @property
    def media_id(self) -> str:
        return self.video_id

    @property
    def action_label(self) -> str:
        return "Watch"

    @property
    def media_label(self) -> str:
        return "Video"

    @property
    def provider_label(self) -> str:
        return "YouTube"


@dataclass(frozen=True, slots=True)
class SpotifyEmbed:
    """A Spotify player derived from an allowlisted source URL."""

    provider: str
    media_id: str
    embed_url: str

    @property
    def action_label(self) -> str:
        return "Listen to"

    @property
    def media_label(self) -> str:
        return "Audio"

    @property
    def provider_label(self) -> str:
        return "Spotify"



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
    video: VideoEmbed | None = None
    spotify: SpotifyEmbed | None = None

    @property
    def season_episode(self) -> str:
        """The catalogue's own numbering, as both pages write it."""

        return f"Season {self.season} · Episode {self.episode}"

    @property
    def guest_names(self) -> str:
        return " and ".join(guest.name for guest in self.guests)

    @property
    def player(self) -> VideoEmbed | SpotifyEmbed | None:
        """Return the preferred validated player, with Spotify as the fallback."""

        return self.video or self.spotify


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


def _safe_external_url(value: Any, *, field: str, https_only: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ImproperlyConfigured(f"Public podcast {field} must be a URL.")
    value = value.strip()
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ({"https"} if https_only else {"http", "https"})
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or any(character in value for character in "\x00\r\n")
    ):
        suffix = "https address" if https_only else "web address"
        raise ImproperlyConfigured(f"Public podcast {field} must be an {suffix}.")
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
        prepared.append(PlatformLink(provider=key, label=label, url=url, dot=dot))
    return tuple(prepared)


def podcast_platform_links(
    records: tuple[dict[str, Any], ...] | list[dict[str, Any]],
) -> tuple[PlatformLink, ...]:
    """Return the checked show-level platform records without page-specific lookups."""

    prepared: list[PlatformLink] = []
    seen: set[str] = set()
    expected_fields = {"provider", "label", "url", "dot"}
    for record in records:
        if not isinstance(record, dict) or set(record) != expected_fields:
            raise ImproperlyConfigured("Public podcast platform record shape is invalid.")
        provider = record["provider"]
        label = record["label"]
        url = record["url"]
        dot = record["dot"]
        if (
            not isinstance(provider, str)
            or provider not in PLATFORM_ORDER
            or provider in seen
            or not isinstance(label, str)
            or not label.strip()
            or not isinstance(url, str)
            or not url.startswith("https://")
            or not isinstance(dot, str)
            or re.fullmatch(r"dot-[a-z0-9-]+", dot) is None
        ):
            raise ImproperlyConfigured("Public podcast platform record is invalid.")
        seen.add(provider)
        prepared.append(PlatformLink(provider=provider, label=label.strip(), url=url, dot=dot))
    return tuple(prepared)


def _watch_destination(record: dict[str, Any]) -> tuple[str, str]:
    """Return the address the play control opens, and the platform it names."""

    links = record.get("links") or {}
    for key in WATCH_PREFERENCE:
        url = links.get(key)
        if url:
            return str(url), PLATFORM_LABELS[key][0]
    return "", ""


def _youtube_video_id(url: str) -> str:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").casefold().removeprefix("www.")
    candidate = ""
    if hostname == "youtu.be":
        candidate = parsed.path.strip("/").split("/", 1)[0]
    elif hostname in {"youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            values = dict(parse_qsl(parsed.query, keep_blank_values=True))
            candidate = values.get("v", "")
        else:
            prefix, separator, suffix = parsed.path.strip("/").partition("/")
            if separator and prefix in {"embed", "live", "shorts"}:
                candidate = suffix.split("/", 1)[0]
    return candidate if YOUTUBE_VIDEO_ID.fullmatch(candidate) else ""


def _video_embed(record: dict[str, Any]) -> VideoEmbed | None:
    links = record.get("links") or {}
    youtube_url = links.get("youtube") if isinstance(links, dict) else None
    if not isinstance(youtube_url, str) or not youtube_url.strip():
        return None
    safe_youtube_url = _safe_external_url(youtube_url, field="YouTube player link", https_only=True)
    source_video_id = _youtube_video_id(safe_youtube_url)
    raw_video = record.get("video")
    if raw_video is None:
        return VideoEmbed(provider="youtube", video_id=source_video_id) if source_video_id else None
    if not isinstance(raw_video, dict):
        raise ImproperlyConfigured("Public podcast video must be a mapping.")
    provider = raw_video.get("provider")
    video_id = raw_video.get("id")
    if (
        provider != "youtube"
        or not isinstance(video_id, str)
        or not YOUTUBE_VIDEO_ID.fullmatch(video_id)
    ):
        raise ImproperlyConfigured("Public podcast video identity is invalid.")
    if source_video_id != video_id:
        return None
    return VideoEmbed(provider=provider, video_id=video_id)


def _spotify_creator_embed(url: str) -> SpotifyEmbed | None:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").casefold().removeprefix("www.")
    if parsed.scheme != "https" or hostname != "creators.spotify.com":
        return None
    parts = parsed.path.strip("/").split("/")
    if len(parts) not in {5, 6} or parts[0] != "pod":
        return None
    if parts[1] not in {"profile", "show"} or parts[3] != "episodes":
        return None
    if any(SPOTIFY_CREATOR_SEGMENT.fullmatch(part) is None for part in parts[1:]):
        return None
    episode_key = parts[4]
    return SpotifyEmbed(
        provider="spotify",
        media_id=episode_key,
        embed_url=(
            f"https://creators.spotify.com/pod/{parts[1]}/{parts[2]}/embed/episodes/{episode_key}"
        ),
    )


def _spotify_open_embed(url: str) -> SpotifyEmbed | None:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").casefold().removeprefix("www.")
    parts = parsed.path.strip("/").split("/")
    if (
        parsed.scheme != "https"
        or hostname != "open.spotify.com"
        or len(parts) != 2
        or parts[0] != "episode"
        or not SPOTIFY_EPISODE_ID.fullmatch(parts[1])
    ):
        return None
    episode_id = parts[1]
    return SpotifyEmbed(
        provider="spotify",
        media_id=episode_id,
        embed_url=f"https://open.spotify.com/embed/episode/{episode_id}",
    )


def _spotify_embed(record: dict[str, Any]) -> SpotifyEmbed | None:
    links = record.get("links") or {}
    if not isinstance(links, dict):
        return None
    for key, parser in (
        ("spotify_for_creators", _spotify_creator_embed),
        ("anchor", _spotify_creator_embed),
        ("spotify", _spotify_open_embed),
    ):
        value = links.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            safe_url = _safe_external_url(value, field="Spotify player link", https_only=True)
        except ImproperlyConfigured:
            continue
        embed = parser(safe_url)
        if embed is not None:
            return embed
    return None


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
        video=_video_embed(record),
        spotify=_spotify_embed(record),
    )


def listening_platform_phrase(episodes: tuple[Episode, ...]) -> str:
    """Name the platforms every one of these episodes can be followed on.

    The catalogue has no show-level feed address, so the index cannot offer a
    subscribe button and must not invent one.  What it can say truthfully is where
    the episodes themselves lead, and a platform earns its place in that sentence
    only when every episode on the page carries it.
    """

    if not episodes:
        return ""
    shared = set.intersection(
        *({link.label for link in episode.platform_links} for episode in episodes)
    )
    labels = [
        PLATFORM_LABELS[key][0] for key in PLATFORM_ORDER if PLATFORM_LABELS[key][0] in shared
    ]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


def season_episodes(records: tuple[dict[str, Any], ...]) -> tuple[Episode, ...]:
    """Return one season's episodes in the order the catalogue already ordered them."""

    if not records:
        raise ImproperlyConfigured("Public podcast season must not be empty.")
    return tuple(episode_view(record) for record in records)
