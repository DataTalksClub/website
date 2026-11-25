"""The podcast index: one season per page, drawn as the /events timeline.

Every fact a card shows is derived here from records the database already
holds; nothing is typed in by hand:

* **date** — the episode's own ``published`` date.  The newest seasons were
  never given one in the content source, so for those the date is the start of
  the published podcast event that recorded the episode (matched by the
  event's season/episode tags, else by its exact title).
* **minutes** — the last timestamp of the episode's transcript, rounded.
* **guest role** — the opening clause of the guest's profile bio ("… is a
  Staff Data Engineer at …"); the people records carry no role field.
* **topics** — the wiki pages the knowledge graph files the episode under,
  strongest first (:func:`content.wiki_content.episode_topics`).
* **picture** — the matched event's banner (the one /events shows), else the
  first guest's portrait.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from events.queries import published_event_records

from . import catalogue, wiki_content
from .event_banners import event_banner_url
from .podcast_content import (
    Episode,
    Guest,
    PlatformLink,
    PodcastSeason,
    episode_view,
)
from .wiki_content import SharedConcept

#: The order the hub names listening destinations in.
HUB_LISTEN_ORDER: tuple[str, ...] = ("spotify", "apple", "youtube")
#: How many wiki topics a card names.
CARD_TOPICS = 3
#: A role longer than this is a sentence, not a role, and is left out.
ROLE_MAX_LENGTH = 60

# "<Name> is (a|an|the) <role>[, | based | who …]": the role is what follows the
# verb, up to the first clause that starts describing something else.
_ROLE = re.compile(r"\bis (?:an? |the )?(?P<role>[^;]+)")
_ROLE_END = re.compile(
    r",| based\b| who\b| whose\b| with\b| working\b| known\b| where\b| specializ"
    r"| dedicated\b| committed\b| focused\b| and has\b| and host\b| and co-?founder\b"
    r"| and author\b| and the\b| and an? \b| turned\b| from\b| currently\b| in \b| on \b"
    r"| for over\b"
)


def podcast_season_path(number: int, *, latest: int) -> str:
    """Return the public path of one season's page; the newest is the hub itself."""

    return "/podcast" if number == latest else f"/podcast/s/{number}/"


def guest_role(summary: str) -> str:
    """The short role a guest's bio opens with, or ``""`` when it has none."""

    first = summary.strip().split(". ", 1)[0]
    match = _ROLE.search(first)
    if match is None:
        return ""
    role = _ROLE_END.split(match["role"], maxsplit=1)[0].strip(" .")
    # "an experienced industry professional" describes, it does not name a role.
    if (
        not role
        or len(role) > ROLE_MAX_LENGTH
        or not role[0].isalpha()
        or role.casefold().startswith("experienced ")
    ):
        return ""
    return role[0].upper() + role[1:]


def _transcript_minutes(record: dict[str, Any]) -> int | None:
    seconds = [
        entry["sec"]
        for entry in record.get("transcript") or ()
        if isinstance(entry, dict)
        and isinstance(entry.get("sec"), (int, float))
        and not isinstance(entry.get("sec"), bool)
    ]
    return round(max(seconds) / 60) if seconds else None


@dataclass(frozen=True, slots=True)
class _EventIndex:
    by_number: dict[tuple[int, int], dict[str, Any]]
    by_title: dict[str, dict[str, Any]]

    def match(self, record: dict[str, Any]) -> dict[str, Any] | None:
        return self.by_number.get((record["season"], record["episode"])) or self.by_title.get(
            str(record.get("title", "")).strip().casefold()
        )


def _event_index() -> _EventIndex:
    by_number: dict[tuple[int, int], dict[str, Any]] = {}
    by_title: dict[str, dict[str, Any]] = {}
    ambiguous: set[str] = set()
    for event in published_event_records():
        if event.get("type") != "podcast":
            continue
        if event.get("season") and event.get("episode"):
            by_number.setdefault((event["season"], event["episode"]), event)
        title = str(event.get("title", "")).strip().casefold()
        if not title:
            continue
        if title in by_title:
            ambiguous.add(title)
        by_title[title] = event
    # Two podcast events with one title cannot say which recorded the episode.
    for title in ambiguous:
        del by_title[title]
    return _EventIndex(by_number=by_number, by_title=by_title)


@dataclass(frozen=True, slots=True)
class HubGuest:
    guest: Guest
    role: str


@dataclass(frozen=True, slots=True)
class HubEpisode:
    episode: Episode
    date: date | None
    minutes: int | None
    guests: tuple[HubGuest, ...]
    listen_links: tuple[PlatformLink, ...]
    topics: tuple[SharedConcept, ...]
    picture_url: str
    picture_is_portrait: bool

    @property
    def short_date(self) -> str:
        return f"{self.date:%b} {self.date.day}" if self.date else ""


@dataclass(frozen=True, slots=True)
class HubSeason:
    number: int
    episodes: tuple[HubEpisode, ...]

    @property
    def span(self) -> str:
        """``Aug – Oct 2026``, ``Dec 2025 – Feb 2026`` or ``Oct 2026``."""

        dates = [episode.date for episode in self.episodes if episode.date]
        if not dates:
            return ""
        first, last = min(dates), max(dates)
        if (first.year, first.month) == (last.year, last.month):
            return f"{last:%b %Y}"
        if first.year == last.year:
            return f"{first:%b} – {last:%b %Y}"
        return f"{first:%b %Y} – {last:%b %Y}"


def _hub_episode(record: dict[str, Any], episode: Episode, events: _EventIndex) -> HubEpisode:
    event = events.match(record)
    if episode.published:
        when: date | None = date.fromisoformat(episode.published[:10])
    elif event is not None:
        when = datetime.fromisoformat(event["starts_at"]).date()
    else:
        when = None
    by_provider = {link.provider: link for link in episode.platform_links}
    banner = event_banner_url(event) if event is not None else ""
    portrait = next((guest.image_path for guest in episode.guests if guest.media_available), "")
    return HubEpisode(
        episode=episode,
        date=when,
        minutes=_transcript_minutes(record),
        guests=tuple(
            HubGuest(guest=guest, role=guest_role(guest.summary)) for guest in episode.guests
        ),
        listen_links=tuple(by_provider[key] for key in HUB_LISTEN_ORDER if key in by_provider),
        topics=wiki_content.episode_topics(record["slug"], limit=CARD_TOPICS),
        picture_url=banner or portrait,
        picture_is_portrait=not banner and bool(portrait),
    )


def hub_season(season: PodcastSeason) -> HubSeason:
    """Compose one season's timeline group from its catalogue records."""

    events = _event_index()
    people = catalogue.people_by_slug()
    return HubSeason(
        number=season.number,
        episodes=tuple(
            _hub_episode(
                record,
                # The hub shows no show notes, so it reads no transcript and
                # resolves no cross-season resource (as ``season_episodes``).
                episode_view(record, people_by_slug=people, include_transcript=False),
                events,
            )
            for record in season.episodes
        ),
    )


def hub_episode(record: dict[str, Any]) -> HubEpisode:
    """Compose one episode's card exactly as the hub draws it (the homepage's latest)."""

    return _hub_episode(
        record,
        episode_view(record, people_by_slug=catalogue.people_by_slug(), include_transcript=False),
        _event_index(),
    )
