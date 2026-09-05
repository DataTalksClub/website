"""Editorial composition for the redesigned public podcast surfaces.

The podcast index and the episode page render the "6d" mockup from
DataTalksClub/website#179 in the design system.  Every fact those pages show
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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.core.exceptions import ImproperlyConfigured

from . import catalogue
from .podcast_resources import (
    PodcastResourceError,
    normalize_podcast_resource,
    normalize_podcast_resources,
)
from .podcast_routes import podcast_canonical_path
from .public_graph import safe_public_graph_url

# Listening destinations, in the order the pages offer them, with the platform
# dot the design system reserves for each.  A link key outside this map is still
# offered, with a neutral dot and its own name.
PLATFORM_LABELS: dict[str, tuple[str, str]] = {
    "apple": ("Apple Podcasts", "dot-bubble"),
    "spotify": ("Spotify", "dot-green"),
    "youtube": ("YouTube", "dot-clay"),
    "spotify_for_creators": ("Spotify for Creators", "dot-gold"),
}
PLATFORM_ORDER: tuple[str, ...] = (
    "apple",
    "spotify",
    "youtube",
    "spotify_for_creators",
)
# What the play control opens, most watchable first.
WATCH_PREFERENCE: tuple[str, ...] = (
    "youtube",
    "spotify",
    "apple",
    "spotify_for_creators",
)
YOUTUBE_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
SPOTIFY_CREATOR_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,255}$")
SPOTIFY_EPISODE_ID = re.compile(r"^[A-Za-z0-9]{22}$")


# How the show is numbered, and where an episode lives.  The catalogue stores
# episodes in its own order and the pages list them in the show's: season and
# episode descending, newest first within a tie.  That is a podcast fact, so it
# is decided here rather than by whatever the catalogue rows sort as.


@dataclass(frozen=True, slots=True)
class PodcastSeason:
    number: int
    episodes: tuple[dict[str, Any], ...]


def podcast_public_path(record: dict[str, Any]) -> str:
    """Return the checked canonical podcast detail path."""

    slug = record.get("slug")
    public_path = record.get("public_path")
    if (
        not isinstance(slug, str)
        or not slug
        or not isinstance(public_path, str)
        or public_path != podcast_canonical_path(slug)
    ):
        raise ImproperlyConfigured("Public podcast canonical path is invalid.")
    return public_path


def _podcast_number(record: dict[str, Any], field: str) -> int:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ImproperlyConfigured(f"Public podcast {field} must be a positive integer.")
    return value


def ordered_podcasts(
    records: tuple[dict[str, Any], ...] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Return the listing order without mutating the catalogue's own order."""

    selected = list(catalogue.podcasts() if records is None else records)
    for record in selected:
        _podcast_number(record, "season")
        _podcast_number(record, "episode")
        if not isinstance(record.get("published"), str) or not isinstance(record.get("slug"), str):
            raise ImproperlyConfigured("Public podcast ordering metadata is invalid.")

    # Stable sorts make the mixed direction contract explicit: season and episode
    # descending, then published descending, then slug ascending.
    selected.sort(key=lambda record: record["slug"])
    selected.sort(key=lambda record: record["published"], reverse=True)
    selected.sort(key=lambda record: record["episode"], reverse=True)
    selected.sort(key=lambda record: record["season"], reverse=True)
    return tuple(selected)


def podcast_seasons(
    records: tuple[dict[str, Any], ...] | None = None,
) -> tuple[PodcastSeason, ...]:
    ordered = ordered_podcasts(records)
    if not ordered:
        raise ImproperlyConfigured("Public podcast catalogue must not be empty.")

    seasons: list[PodcastSeason] = []
    for episode in ordered:
        season_number = episode["season"]
        if not seasons or seasons[-1].number != season_number:
            seasons.append(PodcastSeason(number=season_number, episodes=(episode,)))
            continue
        previous = seasons[-1]
        seasons[-1] = PodcastSeason(
            number=previous.number,
            episodes=(*previous.episodes, episode),
        )
    return tuple(seasons)


@dataclass(frozen=True, slots=True)
class Guest:
    """One named guest, linked to their profile when the catalogue has one."""

    name: str
    public_path: str
    image_path: str = ""
    media_available: bool = False
    summary: str = ""
    profile_links: tuple[ExternalLink, ...] = ()


@dataclass(frozen=True, slots=True)
class ExternalLink:
    """A safe external link drawn from a validated public record."""

    label: str
    url: str


@dataclass(frozen=True, slots=True)
class PlatformLink:
    """One listening destination, with the design system's platform dot."""

    provider: str
    label: str
    url: str
    dot: str


@dataclass(frozen=True, slots=True)
class EpisodeResource:
    """One source-ordered show-note resource with safe link semantics."""

    title: str
    url: str
    is_external: bool = True
    target: str = "_blank"
    rel: str = "noopener noreferrer"

    @property
    def label(self) -> str:
        """The accessible label used by the Show Notes renderer."""

        return self.title


@dataclass(frozen=True, slots=True)
class TimestampEntry:
    """One compact chapter marker derived from a source transcript section."""

    label: str
    seconds: int | float | None
    time: str
    fallback_url: str = ""

    @property
    def topic(self) -> str:
        """A semantic alias for callers that describe chapters as topics."""

        return self.label


@dataclass(frozen=True, slots=True)
class VideoEmbed:
    """A provider/identifier pair allowlisted by the projection builder."""

    provider: str
    video_id: str

    @property
    def embed_url(self) -> str:
        """Return the only embed URL this page is permitted to construct."""

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
    """A Spotify episode embed derived from an allowlisted source link."""

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

    @property
    def is_creator_embed(self) -> bool:
        """Identify the legacy Anchor player without exposing its outbound link."""

        return self.embed_url.startswith("https://creators.spotify.com/")


@dataclass(frozen=True, slots=True)
class TranscriptEntry:
    """One server-rendered transcript entry, optionally linked to a timestamp."""

    header: str = ""
    line: str = ""
    seconds: int | float | None = None
    time: str = ""
    who: str = ""
    anchor: str = ""
    fallback_url: str = ""


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
    resources: tuple[EpisodeResource, ...] = ()
    video: VideoEmbed | None = None
    spotify: SpotifyEmbed | None = None
    transcript: tuple[TranscriptEntry, ...] = ()
    timestamp_entries: tuple[TimestampEntry, ...] = ()

    @property
    def season_episode(self) -> str:
        """The catalogue's own numbering, as both pages write it."""

        return f"Season {self.season} · Episode {self.episode}"

    @property
    def guest_names(self) -> str:
        return " and ".join(guest.name for guest in self.guests)

    @property
    def links(self) -> tuple[EpisodeResource, ...]:
        """Show-note links owned by the podcast model.

        Listening destinations remain in ``platform_links``; this alias gives
        callers an explicit model-level name for the structured links mentioned
        in the episode's show notes while retaining the existing ``resources``
        field for template compatibility.
        """

        return self.resources

    @property
    def player(self) -> VideoEmbed | SpotifyEmbed | None:
        """Return the preferred validated player, with Spotify as the fallback."""

        return self.video or self.spotify

    @property
    def listener_platform_links(self) -> tuple[PlatformLink, ...]:
        """Return useful listener destinations, excluding the creator dashboard."""

        return tuple(
            link for link in self.platform_links if link.provider != "spotify_for_creators"
        )


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


def _safe_resource_url(
    value: Any,
    *,
    podcast_records: tuple[dict[str, Any], ...] | list[dict[str, Any]] | None = None,
) -> str:
    """Validate one resource URL for callers that need strict composition."""

    try:
        return normalize_podcast_resource(
            {"title": "Resource", "url": value},
            records=podcast_records,
        ).url
    except PodcastResourceError as error:
        raise ImproperlyConfigured("Public podcast resource URL is invalid.") from error


def _person_summary(person: dict[str, Any]) -> str:
    summary = person.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    paragraphs = [
        block.get("text", "").strip()
        for block in person.get("blocks", ())
        if isinstance(block, dict)
        and block.get("kind") in {"paragraph", "list_item"}
        and isinstance(block.get("text"), str)
        and block.get("text", "").strip()
    ]
    return " ".join(paragraphs)


def _safe_guest_public_path(value: Any) -> str:
    """Return one canonical profile path without query or fragment state."""

    public_path = safe_public_graph_url(value)
    if not public_path:
        return ""
    parsed = urlsplit(public_path)
    if parsed.query or parsed.fragment:
        return ""
    if re.fullmatch(r"/people/[a-z0-9][a-z0-9-]*\.html", parsed.path) is None:
        return ""
    return public_path


def _person_links(person: dict[str, Any]) -> tuple[ExternalLink, ...]:
    links = person.get("links", ())
    if not isinstance(links, (list, tuple)):
        raise ImproperlyConfigured("Public podcast guest profile links must be a list.")
    prepared: list[ExternalLink] = []
    for link in links:
        if not isinstance(link, dict):
            raise ImproperlyConfigured("Public podcast guest profile link must be a mapping.")
        label = link.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ImproperlyConfigured("Public podcast guest profile link must be named.")
        prepared.append(
            ExternalLink(
                label=label.strip(),
                url=_safe_external_url(link.get("url"), field="guest profile link"),
            )
        )
    return tuple(prepared)


def _guests(
    record: dict[str, Any], people_by_slug: dict[str, dict[str, Any]] | None = None
) -> tuple[Guest, ...]:
    guests: list[Guest] = []
    profiles = record.get("guest_profiles", ()) or ()
    if not isinstance(profiles, (list, tuple)):
        raise ImproperlyConfigured("Public podcast guest profiles must be a list.")
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ImproperlyConfigured("Public podcast guest must be a mapping.")
        key = profile.get("key", "")
        if key is not None and not isinstance(key, str):
            raise ImproperlyConfigured("Public podcast guest key must be a string.")
        name = profile.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ImproperlyConfigured("Public podcast guest must have a name.")
        public_path = _safe_guest_public_path(profile.get("public_path", ""))
        person = people_by_slug.get(key) if people_by_slug and key else None
        if person is not None:
            person_path = _safe_guest_public_path(person.get("public_path", ""))
            if public_path and person_path != public_path:
                raise ImproperlyConfigured("Public podcast guest profile path does not match.")
            public_path = person_path
        guests.append(
            Guest(
                name=name.strip(),
                public_path=str(public_path),
                image_path=(
                    str(person.get("image_path") or "")
                    if person is not None and person.get("media_available")
                    else ""
                ),
                media_available=bool(person and person.get("media_available"))
                and bool(person.get("image_path"))
                if person is not None
                else False,
                summary=_person_summary(person) if person is not None else "",
                profile_links=_person_links(person) if person is not None else (),
            )
        )
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
        url = _safe_external_url(links[key], field="listening link", https_only=True)
        label, dot = PLATFORM_LABELS.get(key, (key.replace("-", " ").title(), "dot-bubble"))
        prepared.append(PlatformLink(provider=key, label=label, url=url, dot=dot))
    return tuple(prepared)


def podcast_platform_links(
    records: tuple[dict[str, Any], ...] | list[dict[str, Any]],
) -> tuple[PlatformLink, ...]:
    """Compose the show-level destinations from the checked platform data records."""

    prepared: list[PlatformLink] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "key",
            "provider",
            "label",
            "title",
            "url",
            "dot",
        }:
            raise ImproperlyConfigured("Public podcast platform record is invalid.")
        key = record.get("key")
        provider = record.get("provider")
        label = record.get("label")
        title = record.get("title")
        url = record.get("url")
        dot = record.get("dot")
        if (
            not isinstance(key, str)
            or key != provider
            or not isinstance(provider, str)
            or provider not in PLATFORM_ORDER
            or provider in seen
            or not isinstance(label, str)
            or not label.strip()
            or title != label
            or not isinstance(dot, str)
            or re.fullmatch(r"dot-[a-z0-9-]+", dot) is None
        ):
            raise ImproperlyConfigured("Public podcast platform record is invalid.")
        seen.add(provider)
        prepared.append(
            PlatformLink(
                provider=provider,
                label=label.strip(),
                url=_safe_external_url(url, field="podcast platform link", https_only=True),
                dot=dot,
            )
        )
    return tuple(prepared)


def _watch_destination(record: dict[str, Any]) -> tuple[str, str]:
    """Return the address the play control opens, and the platform it names."""

    links = record.get("links") or {}
    for key in WATCH_PREFERENCE:
        url = links.get(key)
        if url:
            return (
                _safe_external_url(url, field="watch link", https_only=True),
                PLATFORM_LABELS[key][0],
            )
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
    raw_video = record.get("video")
    if raw_video is None:
        return None
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
    links = record.get("links") or {}
    youtube_url = links.get("youtube") if isinstance(links, dict) else None
    if not isinstance(youtube_url, str) or _youtube_video_id(youtube_url) != video_id:
        # A bounded test/preview record can remove the watch destination without
        # rewriting the optional embed field.  Treat that as the documented unavailable
        # state; the projection builder still rejects a valid source identity mismatch.
        return None
    return VideoEmbed(provider=provider, video_id=video_id)


def _spotify_creator_embed(url: str) -> SpotifyEmbed | None:
    """Turn a Spotify for Creators episode URL into its documented embed path."""

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
    """Turn a canonical open.spotify.com episode URL into Spotify's embed URL."""

    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").casefold().removeprefix("www.")
    parts = parsed.path.strip("/").split("/")
    if (
        parsed.scheme != "https"
        or hostname != "open.spotify.com"
        or len(parts) != 2
        or parts[0] != "episode"
        or SPOTIFY_EPISODE_ID.fullmatch(parts[1]) is None
    ):
        return None
    episode_id = parts[1]
    return SpotifyEmbed(
        provider="spotify",
        media_id=episode_id,
        embed_url=f"https://open.spotify.com/embed/episode/{episode_id}",
    )


def _spotify_embed(record: dict[str, Any]) -> SpotifyEmbed | None:
    """Return a validated Spotify player from the record's stored platform links."""

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
        safe_url = _safe_external_url(value, field="Spotify player link", https_only=True)
        embed = parser(safe_url)
        if embed is not None:
            return embed
    return None


def _resources(
    record: dict[str, Any],
    *,
    podcast_records: tuple[dict[str, Any], ...] | list[dict[str, Any]] | None = None,
) -> tuple[EpisodeResource, ...]:
    raw_resources = record.get("resources", ()) or ()
    normalized = normalize_podcast_resources(
        raw_resources,
        records=podcast_records,
        strict=False,
    )
    return tuple(
        EpisodeResource(
            title=resource.title,
            url=resource.url,
            is_external=resource.is_external,
            target=resource.target,
            rel=resource.rel,
        )
        for resource in normalized
    )


def _format_seconds(seconds: int | float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds_part:02d}" if hours else f"{minutes}:{seconds_part:02d}"


def _timestamp_fallback(url: str, seconds: int | float | None) -> str:
    if not url or seconds is None:
        return ""
    if _youtube_video_id(url) == "":
        # Spotify, Apple, and other validated destinations do not share YouTube's
        # seconds parameter.  They are still useful native fallbacks for a
        # timestamp row; the player enhancement remains available only when the
        # page has a validated YouTube identity.
        return url
    parsed = urlsplit(url)
    query = [
        (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "t"
    ]
    query.append(("t", str(max(0, int(seconds)))))
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def _transcript(record: dict[str, Any], watch_url: str) -> tuple[TranscriptEntry, ...]:
    raw_transcript = record.get("transcript", ()) or ()
    if not isinstance(raw_transcript, (list, tuple)):
        raise ImproperlyConfigured("Public podcast transcript must be a list.")
    entries: list[TranscriptEntry] = []
    for index, raw_entry in enumerate(raw_transcript):
        if not isinstance(raw_entry, dict):
            raise ImproperlyConfigured("Public podcast transcript entry must be a mapping.")
        header = raw_entry.get("header") or ""
        line = raw_entry.get("line") or ""
        if header and line:
            raise ImproperlyConfigured(
                "Public podcast transcript entry cannot be both header and line."
            )
        if header:
            if not isinstance(header, str) or not header.strip():
                raise ImproperlyConfigured("Public podcast transcript header is invalid.")
            entries.append(TranscriptEntry(header=header.strip()))
            continue
        if not isinstance(line, str) or not line.strip():
            raise ImproperlyConfigured("Public podcast transcript line is invalid.")
        seconds = raw_entry.get("sec")
        if seconds is not None and (
            isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0
        ):
            raise ImproperlyConfigured("Public podcast transcript timestamp is invalid.")
        raw_time = raw_entry.get("time") or ""
        if raw_time and (not isinstance(raw_time, str) or not raw_time.strip()):
            raise ImproperlyConfigured("Public podcast transcript time is invalid.")
        time = raw_time.strip() if isinstance(raw_time, str) else ""
        if not time and seconds is not None:
            time = _format_seconds(seconds)
        who = raw_entry.get("who") or ""
        if who and (not isinstance(who, str) or not who.strip()):
            raise ImproperlyConfigured("Public podcast transcript speaker is invalid.")
        entries.append(
            TranscriptEntry(
                line=line.strip(),
                seconds=seconds,
                time=time,
                who=who.strip() if isinstance(who, str) else "",
                anchor=f"transcript-entry-{index}",
                fallback_url=_timestamp_fallback(watch_url, seconds),
            )
        )
    return tuple(entries)


_TIMESTAMP_LABEL_MAX_LENGTH = 160


def _timestamp_entries(
    transcript: tuple[TranscriptEntry, ...],
) -> tuple[TimestampEntry, ...]:
    """Derive a compact chapter index from source section headers.

    A source header is a chapter label when its section contains a transcript
    line carrying a displayable time.  The first such line supplies the marker;
    this handles source sections whose opening paragraph is un-timestamped.
    This intentionally does not use transcript prose as a fallback label: a
    missing or malformed chapter marker stays missing rather than becoming a
    second copy of the transcript.
    """

    entries: list[TimestampEntry] = []
    for index, entry in enumerate(transcript):
        label = entry.header.strip()
        if not label or len(label) > _TIMESTAMP_LABEL_MAX_LENGTH:
            continue
        marker = None
        for candidate in transcript[index + 1 :]:
            if candidate.header:
                break
            if candidate.line and candidate.time and candidate.seconds is not None:
                marker = candidate
                break
        if marker is None:
            continue
        entries.append(
            TimestampEntry(
                label=" ".join(label.split()),
                seconds=marker.seconds,
                time=marker.time,
                fallback_url=marker.fallback_url,
            )
        )
    return tuple(entries)


def episode_view(
    record: dict[str, Any],
    *,
    people_by_slug: dict[str, dict[str, Any]] | None = None,
    resource_podcast_records: tuple[dict[str, Any], ...] | list[dict[str, Any]] | None = None,
) -> Episode:
    """Return one catalogue record as the value both podcast templates render."""

    published = str(record.get("published") or "")
    watch_url, watch_label = _watch_destination(record)
    transcript = _transcript(record, watch_url)
    return Episode(
        title=_required_text(record, "title"),
        description=_required_text(record, "description"),
        # The catalogue's checked canonical identity, never a link the page invents
        # from a title.
        public_path=podcast_public_path(record),
        season=_required_number(record, "season"),
        episode=_required_number(record, "episode"),
        guests=_guests(record, people_by_slug),
        published=published,
        published_display=published_display(published),
        image_path=str(record.get("image_path") or ""),
        media_available=bool(record.get("media_available")) and bool(record.get("image_path")),
        platform_links=_platform_links(record),
        watch_url=watch_url,
        watch_label=watch_label,
        resources=_resources(record, podcast_records=resource_podcast_records),
        video=_video_embed(record),
        spotify=_spotify_embed(record),
        transcript=transcript,
        timestamp_entries=_timestamp_entries(transcript),
    )


def episode_navigation(
    record: dict[str, Any],
    records: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    *,
    people_by_slug: dict[str, dict[str, Any]] | None = None,
) -> tuple[Episode | None, Episode | None, tuple[Episode, ...]]:
    """Return older/newer neighbours and up to three same-season related episodes."""

    season_records = tuple(
        item
        for item in ordered_podcasts(tuple(records))
        if item.get("season") == record.get("season")
    )
    current_slug = record.get("slug")
    try:
        index = next(
            index for index, item in enumerate(season_records) if item.get("slug") == current_slug
        )
    except StopIteration as error:
        raise ImproperlyConfigured(
            "Public podcast navigation record is not in the catalogue."
        ) from error

    previous = season_records[index + 1] if index + 1 < len(season_records) else None
    following = season_records[index - 1] if index else None
    related = tuple(item for item in season_records if item.get("slug") != current_slug)[:3]

    def compose(item: dict[str, Any] | None) -> Episode | None:
        return (
            episode_view(
                item,
                people_by_slug=people_by_slug,
                resource_podcast_records=records,
            )
            if item is not None
            else None
        )

    return (
        compose(previous),
        compose(following),
        tuple(
            episode_view(
                item,
                people_by_slug=people_by_slug,
                resource_podcast_records=records,
            )
            for item in related
        ),
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
        *({link.label for link in episode.listener_platform_links} for episode in episodes)
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
    # Season hubs do not render show-note resources, and this tuple contains only
    # one season, so it cannot resolve a legacy resource that names another season.
    # Detail/navigation composition receives the complete catalogue and performs
    # canonical resource normalization there.
    return tuple(episode_view(record) for record in records)
