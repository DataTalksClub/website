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
  episode's generated card picture (``content.podcast_pictures``).  A guest's raw
  portrait is never the card's picture.
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
    ordered_podcasts,
)
from .podcast_pictures import Variant, picture_path, slugs_with
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
    #: Episodes with a stored generated card picture.
    card_slugs: frozenset[str] = frozenset()

    def match(self, record: dict[str, Any]) -> dict[str, Any] | None:
        return self.by_number.get((record["season"], record["episode"])) or self.by_title.get(
            str(record.get("title", "")).strip().casefold()
        )


def event_index() -> _EventIndex:
    """Index the published podcast events and stored card pictures (two queries)."""

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
    return _EventIndex(by_number=by_number, by_title=by_title, card_slugs=slugs_with(Variant.CARD))


def episode_picture_url(record: dict[str, Any], events: _EventIndex) -> str:
    """The one square picture every view draws for an episode.

    The matched event's banner (the one /events shows), else the episode's
    generated card picture, else ``""``.  The hub card, the episode hero, the
    related timeline, wiki blocks, person and home cards all read this, so an
    episode looks the same wherever it appears.
    """

    event = events.match(record)
    banner = event_banner_url(event) if event is not None else ""
    if banner:
        return banner
    return picture_path(record["slug"], Variant.CARD) if record["slug"] in events.card_slugs else ""


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
    return HubEpisode(
        episode=episode,
        date=when,
        minutes=_transcript_minutes(record),
        guests=tuple(
            HubGuest(guest=guest, role=guest_role(guest.summary)) for guest in episode.guests
        ),
        listen_links=tuple(by_provider[key] for key in HUB_LISTEN_ORDER if key in by_provider),
        topics=wiki_content.episode_topics(record["slug"], limit=CARD_TOPICS),
        picture_url=episode_picture_url(record, events),
    )


def hub_season(season: PodcastSeason) -> HubSeason:
    """Compose one season's timeline group from its catalogue records."""

    events = event_index()
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
        event_index(),
    )


def topic_episodes(wiki_path: str) -> tuple[HubEpisode, ...]:
    """Every episode about one wiki page, as hub cards, most relevant first.

    Relevance is the episode's link weight to the page in the knowledge graph
    (:func:`content.wiki_content.topic_episode_weights`); equal weights fall to
    the newer episode in the podcast's own order.
    """

    weights = wiki_content.topic_episode_weights(wiki_path)
    if not weights:
        return ()
    records = [record for record in ordered_podcasts() if record.get("slug") in weights]
    newest_first = {record["slug"]: index for index, record in enumerate(records)}
    records.sort(key=lambda record: (-weights[record["slug"]], newest_first[record["slug"]]))
    return hub_episodes(records)


def hub_episodes(
    records: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    *,
    events: _EventIndex | None = None,
) -> tuple[HubEpisode, ...]:
    """Compose several episodes as hub cards, in the order given."""

    events = events if events is not None else event_index()
    people = catalogue.people_by_slug()
    return tuple(
        _hub_episode(
            record,
            episode_view(record, people_by_slug=people, include_transcript=False),
            events,
        )
        for record in records
    )


@dataclass(frozen=True, slots=True)
class GuestProfileLink:
    """One of a guest's own profile links, with the mark the page draws for it."""

    label: str
    url: str
    icon: str


@dataclass(frozen=True, slots=True)
class GuestCard:
    """One guest as the episode page's "About the guest" card draws them."""

    guest: Guest
    role: str
    links: tuple[GuestProfileLink, ...]


#: Profile hosts the page draws a brand mark for; any other link is a website.
_LINK_ICONS: tuple[tuple[str, str], ...] = (
    ("linkedin.com", "linkedin"),
    ("x.com", "x"),
    ("twitter.com", "x"),
    ("github.com", "github"),
    ("youtube.com", "youtube"),
)


def _link_icon(url: str) -> str:
    host = re.sub(r"^https?://", "", url).split("/", 1)[0].casefold()
    for domain, icon in _LINK_ICONS:
        if host == domain or host.endswith("." + domain):
            return icon
    return "website"


def guest_cards(guests: tuple[Guest, ...]) -> tuple[GuestCard, ...]:
    """Each guest with their role line and their links' marks."""

    return tuple(
        GuestCard(
            guest=guest,
            role=guest_role(guest.summary),
            links=tuple(
                GuestProfileLink(label=link.label, url=link.url, icon=_link_icon(link.url))
                for link in guest.profile_links
            ),
        )
        for guest in guests
    )
