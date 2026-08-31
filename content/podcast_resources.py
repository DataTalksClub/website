"""Safe, idempotent normalization for podcast show-note resources.

The source repository stores resources as ``title``/``url`` pairs.  The checked
podcast model stores the normalized resource contract as well, so consumers do
not need to rediscover whether a destination is internal or external.  This
module validates both shapes without mutating the source record.  Internal
episode links are resolved against the checked podcast catalogue so an unknown
target can be omitted instead of becoming an invented URL.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .podcast_routes import podcast_canonical_path, podcast_public_id

PODCAST_RESOURCE_SCHEMA_VERSION = 1
EXTERNAL_RESOURCE_TARGET = "_blank"
EXTERNAL_RESOURCE_REL = "noopener noreferrer"
PROJECTED_RESOURCE_FIELDS = frozenset({"is_external", "target", "rel"})

_WHITESPACE = re.compile(r"\s+")
_SAFE_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
_FLAT_PATH = re.compile(r"/podcast/(?P<slug>[a-z0-9][a-z0-9_-]*)\.html\Z")
_NUMBERED_FLAT_PATH = re.compile(
    r"/podcast/s(?P<season>[0-9]+)e(?P<episode>[0-9]+)-"
    r"(?P<slug>[a-z0-9][a-z0-9_-]*)\.html\Z"
)
_HIERARCHICAL_PATH = re.compile(
    r"/podcast/s(?P<season>[0-9]+)e(?P<episode>[0-9]+)/"
    r"(?P<slug>[a-z0-9][a-z0-9_-]*)\Z"
)
_BRAND_SPELLINGS = (
    (re.compile(r"\blinkedi\b", re.IGNORECASE), "LinkedIn"),
    (re.compile(r"\blinkedin\b", re.IGNORECASE), "LinkedIn"),
    (re.compile(r"\bgithub\b", re.IGNORECASE), "GitHub"),
    (re.compile(r"\byoutube\b", re.IGNORECASE), "YouTube"),
)
_INTERNAL_HOSTS = frozenset({"datatalks.club", "www.datatalks.club"})


class PodcastResourceError(ValueError):
    """A source resource cannot be represented safely in the public contract."""


@dataclass(frozen=True, slots=True)
class NormalizedPodcastResource:
    """The render-ready, source-ordered show-note resource contract."""

    title: str
    url: str
    is_external: bool
    target: str
    rel: str

    @property
    def label(self) -> str:
        """Expose the UI vocabulary without duplicating source ``title`` data."""

        return self.title

    def as_dict(self) -> dict[str, Any]:
        """Return the stable JSON shape used by a rebuilt public projection."""

        return {
            "title": self.title,
            "url": self.url,
            "is_external": self.is_external,
            "target": self.target,
            "rel": self.rel,
        }


def normalize_resource_title(value: Any) -> str:
    """Trim source whitespace and normalize unambiguous platform spellings."""

    if not isinstance(value, str) or not value.strip():
        raise PodcastResourceError("podcast resource title is required")
    title = _WHITESPACE.sub(" ", value.strip())
    for pattern, replacement in _BRAND_SPELLINGS:
        title = pattern.sub(replacement, title)
    if len(title) > 500:
        raise PodcastResourceError("podcast resource title is too long")
    return title


def _record_path(record: Mapping[str, Any]) -> str:
    slug = record.get("slug")
    season = record.get("season")
    episode = record.get("episode")
    if (
        not isinstance(slug, str)
        or _SAFE_SLUG.fullmatch(slug) is None
        or isinstance(season, bool)
        or not isinstance(season, int)
        or season < 1
        or isinstance(episode, bool)
        or not isinstance(episode, int)
        or episode < 1
    ):
        return ""

    reviewed_path = podcast_canonical_path(slug)
    if reviewed_path.startswith("/podcast/") and ".html" not in reviewed_path:
        return reviewed_path
    return f"/podcast/{podcast_public_id(season=season, episode=episode)}/{slug}"


def _record_aliases(record: Mapping[str, Any]) -> tuple[str, ...]:
    slug = record.get("slug")
    season = record.get("season")
    episode = record.get("episode")
    canonical = _record_path(record)
    if not canonical or not isinstance(slug, str):
        return ()
    aliases = {
        canonical,
        f"/podcast/{slug}.html",
    }
    if isinstance(season, int) and not isinstance(season, bool) and isinstance(episode, int):
        aliases.add(f"/podcast/s{season:02d}e{episode:02d}-{slug}.html")
        aliases.add(f"/podcast/s{season}e{episode}-{slug}.html")
    public_path = record.get("public_path")
    if isinstance(public_path, str):
        aliases.add(public_path)
    return tuple(aliases)


def _episode_index(
    records: Sequence[Mapping[str, Any]] | None,
) -> tuple[dict[str, Mapping[str, Any]], dict[tuple[int, int], Mapping[str, Any]]]:
    by_path: dict[str, Mapping[str, Any]] = {}
    by_number: dict[tuple[int, int], Mapping[str, Any]] = {}
    if records is None:
        return by_path, by_number
    for record in records:
        if not isinstance(record, Mapping):
            continue
        for path in _record_aliases(record):
            by_path[path] = record
        season = record.get("season")
        episode = record.get("episode")
        if (
            isinstance(season, int)
            and not isinstance(season, bool)
            and season >= 1
            and isinstance(episode, int)
            and not isinstance(episode, bool)
            and episode >= 1
        ):
            key = (season, episode)
            if key in by_number:
                by_number[key] = {}
            else:
                by_number[key] = record
    return by_path, by_number


def _resource_url(
    value: Any,
    *,
    records: Sequence[Mapping[str, Any]] | None,
) -> tuple[str, bool]:
    if not isinstance(value, str) or not value.strip():
        raise PodcastResourceError("podcast resource URL is required")
    candidate = value.strip()
    if any(
        ord(character) < 0x20 or ord(character) == 0x7F or character == "\\"
        for character in candidate
    ):
        raise PodcastResourceError("podcast resource URL contains unsafe characters")
    try:
        parsed = urlsplit(candidate)
        hostname = (parsed.hostname or "").casefold()
        has_credentials = parsed.username is not None or parsed.password is not None
        port = parsed.port
    except (TypeError, ValueError) as error:
        raise PodcastResourceError("podcast resource URL is invalid") from error

    has_query_or_fragment = "?" in candidate or "#" in candidate
    internal_path = ""
    if parsed.scheme == "" and parsed.netloc == "":
        if not candidate.startswith("/") or has_query_or_fragment:
            raise PodcastResourceError("podcast internal resource URL is invalid")
        internal_path = parsed.path
    elif parsed.scheme in {"http", "https"} and hostname in _INTERNAL_HOSTS and not has_credentials:
        if port is not None or has_query_or_fragment:
            raise PodcastResourceError("podcast internal resource URL is invalid")
        internal_path = parsed.path
    else:
        if parsed.scheme != "https" or not hostname or has_credentials or port is not None:
            raise PodcastResourceError("podcast resource URL must be an HTTPS address")
        return candidate, True

    if not internal_path.startswith("/podcast/"):
        raise PodcastResourceError("podcast internal resource URL must target an episode")
    by_path, by_number = _episode_index(records)
    target = by_path.get(internal_path)
    if target is None:
        numbered = _NUMBERED_FLAT_PATH.fullmatch(internal_path)
        hierarchical = _HIERARCHICAL_PATH.fullmatch(internal_path)
        match = numbered or hierarchical
        if match is not None:
            key = (int(match.group("season")), int(match.group("episode")))
            target = by_number.get(key)
            if not target:
                raise PodcastResourceError("podcast internal resource target is unknown")
    if target is None:
        # A flat-looking path is not allowed to pass through even if the full
        # catalogue was not available to resolve it.
        if _FLAT_PATH.fullmatch(internal_path) is not None:
            raise PodcastResourceError("podcast internal resource target is unknown")
        raise PodcastResourceError("podcast internal resource URL is invalid")
    canonical = _record_path(target)
    if not canonical or ".html" in canonical:
        raise PodcastResourceError("podcast internal resource target is invalid")
    return canonical, False


def normalize_podcast_resource(
    value: Mapping[str, Any],
    *,
    records: Sequence[Mapping[str, Any]] | None = None,
) -> NormalizedPodcastResource:
    """Normalize one resource, raising for a value that must not be rendered."""

    if not isinstance(value, Mapping):
        raise PodcastResourceError("podcast resource must be a mapping")
    projected_fields = PROJECTED_RESOURCE_FIELDS.intersection(value)
    if projected_fields and projected_fields != PROJECTED_RESOURCE_FIELDS:
        raise PodcastResourceError("podcast resource metadata is incomplete")
    title_value = value.get("title")
    label_value = value.get("label")
    if title_value is None:
        title_value = label_value
    elif label_value is not None and label_value != title_value:
        raise PodcastResourceError("podcast resource title and label disagree")
    title = normalize_resource_title(title_value)
    url, is_external = _resource_url(value.get("url"), records=records)
    normalized = NormalizedPodcastResource(
        title=title,
        url=url,
        is_external=is_external,
        target=EXTERNAL_RESOURCE_TARGET if is_external else "",
        rel=EXTERNAL_RESOURCE_REL if is_external else "",
    )
    if projected_fields:
        if (
            value.get("is_external") != normalized.is_external
            or value.get("target") != normalized.target
            or value.get("rel") != normalized.rel
        ):
            raise PodcastResourceError("podcast resource metadata disagrees with URL")
    return normalized


def normalize_podcast_resources(
    value: Any,
    *,
    records: Sequence[Mapping[str, Any]] | None = None,
    strict: bool = True,
) -> tuple[NormalizedPodcastResource, ...]:
    """Normalize source-ordered resources without mutating the input.

    Strict callers (the projection builder) fail on malformed source data.  The
    public composition path uses ``strict=False`` so one stale/unsafe resource
    cannot make an otherwise useful episode page unsafe or unavailable.
    """

    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        if strict:
            raise PodcastResourceError("podcast resources must be a list")
        return ()
    prepared: list[NormalizedPodcastResource] = []
    for resource in value:
        try:
            prepared.append(normalize_podcast_resource(resource, records=records))
        except PodcastResourceError:
            if strict:
                raise
    return tuple(prepared)
