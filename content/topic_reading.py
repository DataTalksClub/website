"""What a wiki topic or a podcast episode points a reader to, sorted by kind.

A wiki page's body links and an episode's knowledge-graph neighbours name
events, articles and books by public path.  They used to be drawn as one
"Read more" list of bare titles with a kind word in front ("Event  Tracking and
personalizing AI agents…"), which read as neither links nor a list a reader
could act on.  This sorts those paths into the site's own records, so each
kind is drawn with the card that kind already has everywhere else: an event
as the /events timeline card (picture, date, title), an article as the /blog
row and a book as the /books row.

A path the database publishes nothing for is dropped: a card needs a record.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from . import catalogue
from .event_content import EventDateGroup, event_groups, event_timeline_groups


@dataclass(frozen=True, slots=True)
class TopicReading:
    """The events, articles and books a page points to, each kind in its order."""

    events: tuple[EventDateGroup, ...] = ()
    articles: tuple[dict[str, Any], ...] = ()
    books: tuple[dict[str, Any], ...] = ()

    def __bool__(self) -> bool:
        return bool(self.events or self.articles or self.books)


def _title_key(title: str) -> str:
    return " ".join(title.split()).casefold()


def _event_paths_by_title(titles: Iterable[str]) -> list[str]:
    """The public paths of the published events with these titles.

    A wiki page's body names an event by its title only (the wiki source has no
    link for it), so the card is found by the title the event publishes.
    """

    wanted = [_title_key(title) for title in titles if title]
    if not wanted:
        return []
    groups = event_groups()
    by_title: dict[str, str] = {}
    for event in (*groups.upcoming, *groups.recent):
        by_title.setdefault(_title_key(str(event.get("title", ""))), event["public_path"])
    return [by_title[key] for key in wanted if key in by_title]


def topic_reading(paths: Iterable[str], *, event_titles: Iterable[str] = ()) -> TopicReading:
    """Sort public paths (and event titles) into the published events, articles
    and books they name."""

    wanted = list(dict.fromkeys(path.split("#", 1)[0] for path in paths if path))
    wanted.extend(path for path in _event_paths_by_title(event_titles) if path not in wanted)
    if not wanted:
        return TopicReading()
    articles_by_path = {record["public_path"]: record for record in catalogue.articles()}
    books_by_path: dict[str, dict[str, Any]] = {}
    for record in catalogue.books():
        books_by_path[record["public_path"]] = record
        # A body written before books moved to dateless addresses still names
        # the dated spelling, which is the book's source slug.
        books_by_path.setdefault(f"/books/{record['slug']}.html", record)
    articles = tuple(articles_by_path[path] for path in wanted if path in articles_by_path)
    books = {
        books_by_path[path]["public_path"]: books_by_path[path]
        for path in wanted
        if path in books_by_path
    }
    return TopicReading(
        events=event_timeline_groups(path for path in wanted if path.startswith("/events/")),
        articles=articles,
        books=tuple(books.values()),
    )
