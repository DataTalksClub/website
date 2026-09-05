"""Editorial composition for the redesigned public person page.

The person page renders one profile in the design system (issue #179).  Every
fact it shows — the portrait, the roles, the external profile links, and every
contribution with its date and marker — is read from the checked public profile
and from the record each contribution points at.  A record that cannot supply a
fact fails loudly here instead of reaching the page as an invention.

A profile links to work of four kinds (podcast episodes, events, articles and
books), and it carries no ordering of its own: the relationships arrive grouped
by role, in source order.  The page therefore groups them by what they are and
orders each group by the date the linked record itself carries, newest first, so
that the most recent work is the first a reader sees.

Deliberate omissions, because no profile carries the field: a job title, an
employer, a location, a pronoun, and any "member since" date.  A profile may also
have any subset of everything it does carry — 422 of the 438 profiles have no
summary at all, six link to no work, and a handful have no external links — so
each part of the page appears only when the profile actually has it, and the page
says plainly when there is no linked work rather than padding the space.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from events.queries import published_event_records

from .public_data import public_projection

# Events are shown in the same timezone the events hub and the event pages use.
SITE_TIMEZONE = ZoneInfo("Europe/Berlin")

# One group per kind of work a profile can link to, in the order the page offers
# them: the path prefix the relationship carries, the index its record lives in,
# the heading, and the noun the group counts in.  Each group used to name its own
# band (cream, mint, lavender, cream), which made one page look like four; the
# ground is now the site-wide content lavender and the group is named by its
# heading and its row marks.  See "Which ground a band takes" in
# _docs/design/design-system.md.
CONTRIBUTION_GROUPS: tuple[tuple[str, str, str, str], ...] = (
    ("podcast", "podcasts_by_path", "Podcast episodes", "episode"),
    # The events index is built from the database, not read from the catalogue.
    ("events", "", "Events", "event"),
    ("blog", "articles_by_path", "Articles", "article"),
    ("books", "books_by_path", "Books", "book"),
)

# How many rows of a group stay in view, and how many it takes to be worth
# folding the rest away.  Six is about one screen of rows under a band heading at
# a laptop height, and it is more than all but ten of the 615 groups in the
# catalogue carry, so almost every profile never sees the control at all.  The
# second number keeps it that way: hiding one or two rows behind something the
# reader has to click is worse than showing them, so a group folds only when
# there are at least three rows to fold.  Today exactly two groups in the whole
# catalogue do — one profile's fifty events, another's twenty articles — which
# are the two lists that actually run off the page.
ROWS_BEFORE_FOLD = 6
SMALLEST_FOLD = 3


@dataclass(frozen=True, slots=True)
class ProfileLink:
    """One external profile address the person publishes."""

    label: str
    url: str


@dataclass(frozen=True, slots=True)
class Contribution:
    """One linked piece of work, prepared as a design system list row."""

    role: str
    title: str
    public_path: str
    # The ISO date the row's rail carries.  It stays empty for the seventeen
    # catalogue entries that carry no date at all, and the row then drops its
    # rail rather than leaving an empty column.  The shared archive row writes
    # the date itself, day above year, so the profile no longer prepares a second
    # rendering of it.
    date: str
    pill_label: str
    pill_variant: str
    # "upcoming" on an event that has not happened yet, and empty on everything
    # else: the profile mixes past and future work in one list, so the state is
    # carried by a word rather than by the pill's colour alone.
    state_label: str
    note: str
    # The card's leading mark: "play" for the podcast disc, empty for everything
    # else.  A date is not a mark — it is the row's rail.
    mark: str


@dataclass(frozen=True, slots=True)
class ContributionGroup:
    """One kind of work, with the rows the page draws for it."""

    key: str
    heading: str
    noun: str
    items: tuple[Contribution, ...]

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def anchor(self) -> str:
        return f"person-{self.key}"

    @property
    def noun_label(self) -> str:
        """The group's noun, agreeing with how many rows it holds."""

        return self._noun_for(self.count)

    def _noun_for(self, total: int) -> str:
        return self.noun if total == 1 else f"{self.noun}s"

    @property
    def folded_count(self) -> int:
        """How many rows this group keeps behind its "show more" control.

        A group that is not long enough to be a wall folds nothing: see
        ``ROWS_BEFORE_FOLD`` and ``SMALLEST_FOLD``.
        """

        hidden = self.count - ROWS_BEFORE_FOLD
        return hidden if hidden >= SMALLEST_FOLD else 0

    @property
    def visible_items(self) -> tuple[Contribution, ...]:
        """The rows the page shows without asking the reader for anything."""

        return self.items[:ROWS_BEFORE_FOLD] if self.folded_count else self.items

    @property
    def folded_items(self) -> tuple[Contribution, ...]:
        """The rest of the rows, in the same order, behind the fold."""

        return self.items[ROWS_BEFORE_FOLD:] if self.folded_count else ()

    @property
    def fold_label(self) -> str:
        """Name the control by what it is holding: ``Show 44 more events``."""

        hidden = self.folded_count
        if not hidden:
            return ""
        return f"Show {hidden} more {self._noun_for(hidden)}"

    @property
    def fold_close_label(self) -> str:
        """And name it again by what it will do next, once it is open."""

        if not self.folded_count:
            return ""
        return f"Show fewer {self._noun_for(self.count)}"

    @property
    def count_label(self) -> str:
        """Count a group the way the page writes it: 1 event, or 50 events."""

        return f"{self.count} {self.noun_label}"

    @property
    def role_phrase(self) -> str:
        """Name the roles this group's rows actually carry, in the order they appear.

        Today every group is single-role (a guest on episodes, a speaker at events,
        an author of articles and books), but the profile states a role per link, so
        a group that mixes them names both instead of picking one.
        """

        roles: list[str] = []
        for item in self.items:
            if item.role not in roles:
                roles.append(item.role)
        if len(roles) < 2:
            return roles[0] if roles else ""
        return f"{', '.join(roles[:-1])} and {roles[-1]}"


@dataclass(frozen=True, slots=True)
class Person:
    """One profile, prepared for the page that renders it."""

    name: str
    public_path: str
    summary: str
    blocks: tuple[dict[str, Any], ...]
    roles: tuple[str, ...]
    links: tuple[ProfileLink, ...]
    image_path: str
    groups: tuple[ContributionGroup, ...]

    @property
    def contribution_total(self) -> int:
        return sum(group.count for group in self.groups)


def _required_text(record: dict[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ImproperlyConfigured(f"Public profile {field} must be a non-empty string.")
    return value


def person_public_path(record: dict[str, Any]) -> str:
    """Return the checked canonical profile path, never one built from a name."""

    slug = record.get("slug")
    public_path = record.get("public_path")
    if (
        not isinstance(slug, str)
        or not slug
        or not isinstance(public_path, str)
        or public_path != f"/people/{slug}.html"
    ):
        raise ImproperlyConfigured("Public profile canonical path is invalid.")
    return public_path


def checked_day(value: str) -> str:
    """Return the calendar day a record publishes, or fail on one it cannot.

    The row draws the date itself, so this no longer renders it; what it still
    does is refuse a date the catalogue got wrong rather than passing a string
    through to the page.  A record with no date at all is not an error: seventeen
    podcast episodes have none, and their rows drop the rail.
    """

    if not value:
        return ""
    try:
        published = date.fromisoformat(value[:10])
    except ValueError as error:
        raise ImproperlyConfigured("Public record publication date is invalid.") from error
    return published.isoformat()


def _links(record: dict[str, Any]) -> tuple[ProfileLink, ...]:
    prepared: list[ProfileLink] = []
    for link in record.get("links", ()):
        if not isinstance(link, dict):
            raise ImproperlyConfigured("Public profile link must be a mapping.")
        label = link.get("label")
        url = link.get("url")
        if not isinstance(label, str) or not label.strip():
            raise ImproperlyConfigured("Public profile link must be named.")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            raise ImproperlyConfigured("Public profile link must be a web address.")
        prepared.append(ProfileLink(label=label, url=url))
    return tuple(prepared)


def _roles(record: dict[str, Any]) -> tuple[str, ...]:
    roles: list[str] = []
    for role in record.get("roles", ()):
        if not isinstance(role, str) or not role.strip():
            raise ImproperlyConfigured("Public profile role must be a non-empty string.")
        roles.append(role)
    return tuple(roles)


def _event_contribution(
    role: str,
    label: str,
    public_path: str,
    event: dict[str, Any],
    *,
    now: datetime,
) -> Contribution:
    """One event, as an archive row: the day it runs on the rail, and its type pill."""

    starts_at = event.get("starts_at")
    if not isinstance(starts_at, str) or not starts_at:
        raise ImproperlyConfigured("Public event start is missing.")
    try:
        starts = datetime.fromisoformat(starts_at)
    except ValueError as error:
        raise ImproperlyConfigured("Public event start is invalid.") from error
    local_start = starts.astimezone(SITE_TIMEZONE)
    event_type = _required_text(event, "type")
    # The same three states the events hub distinguishes, so an event reads the
    # same wherever the reader meets it.
    past = starts < now
    if past:
        variant = "status-pill-wait"
    elif event_type.casefold() == "podcast":
        variant = "status-pill-mint"
    else:
        variant = "status-pill-open"
    return Contribution(
        role=role,
        title=label,
        public_path=public_path,
        date=local_start.date().isoformat(),
        pill_label=event_type,
        pill_variant=variant,
        state_label="" if past else "upcoming",
        note="",
        mark="",
    )


def _podcast_contribution(
    role: str,
    label: str,
    public_path: str,
    episode: dict[str, Any],
) -> Contribution:
    season = episode.get("season")
    number = episode.get("episode")
    if not isinstance(season, int) or isinstance(season, bool) or season < 1:
        raise ImproperlyConfigured("Public podcast season must be a positive integer.")
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise ImproperlyConfigured("Public podcast episode must be a positive integer.")
    published = str(episode.get("published") or "")
    return Contribution(
        role=role,
        title=label,
        public_path=public_path,
        date=checked_day(published),
        pill_label=f"Season {season} · Episode {number}",
        pill_variant="status-pill-mint",
        state_label="",
        note="",
        mark="play",
    )


def _published_contribution(
    role: str,
    label: str,
    public_path: str,
    record: dict[str, Any],
    *,
    note: str,
) -> Contribution:
    published = str(record.get("published") or "")
    return Contribution(
        role=role,
        title=label,
        public_path=public_path,
        date=checked_day(published),
        pill_label="",
        pill_variant="",
        state_label="",
        note=note,
        mark="",
    )


def _contribution(
    relationship: dict[str, Any],
    prefix: str,
    record: dict[str, Any],
    *,
    now: datetime,
) -> Contribution:
    role = relationship.get("role")
    if not isinstance(role, str) or not role.strip():
        raise ImproperlyConfigured("Public profile contribution role is missing.")
    label = _required_text(relationship, "label")
    public_path = str(relationship["public_path"])
    if prefix == "events":
        return _event_contribution(role, label, public_path, record, now=now)
    if prefix == "podcast":
        return _podcast_contribution(role, label, public_path, record)
    # An article carries its own one-line description; a book's authors are
    # source keys rather than names, so a book row states its date and no more.
    note = str(record.get("description") or "") if prefix == "blog" else ""
    return _published_contribution(role, label, public_path, record, note=note)


def _groups(record: dict[str, Any], *, now: datetime) -> tuple[ContributionGroup, ...]:
    projection = public_projection()
    # Events are database rows rather than a projected collection, so their index
    # is built here from the published records instead of read out of the
    # catalogue alongside the others.
    event_index = {record["public_path"]: record for record in published_event_records()}
    collected: dict[str, list[Contribution]] = {key: [] for key, *_ in CONTRIBUTION_GROUPS}
    indexes = {key: index for key, index, *_ in CONTRIBUTION_GROUPS}
    for relationship in record.get("relationships", ()):
        if not isinstance(relationship, dict):
            raise ImproperlyConfigured("Public profile contribution must be a mapping.")
        public_path = relationship.get("public_path")
        if not isinstance(public_path, str) or not public_path.startswith("/"):
            raise ImproperlyConfigured("Public profile contribution link must be a site path.")
        prefix = public_path.split("/")[1]
        index = indexes.get(prefix)
        if index is None:
            raise ImproperlyConfigured(f"Public profile links to unknown collection: {prefix}.")
        linked = (event_index if prefix == "events" else projection[index]).get(public_path)
        if linked is None:
            # The profile names work whose record is not published -- an event
            # whose content has not been ingested, an article retired upstream.
            # A profile lists the work a reader can actually reach, so the row is
            # dropped rather than the page being taken down over it.
            continue
        collected[prefix].append(_contribution(relationship, prefix, linked, now=now))

    groups: list[ContributionGroup] = []
    for key, _index, heading, noun in CONTRIBUTION_GROUPS:
        items = collected[key]
        if not items:
            continue
        # Newest first, by the date the linked record itself carries.  A record
        # without a date keeps its source order at the end rather than being
        # given a date it does not have.
        items.sort(key=lambda item: item.title.casefold())
        items.sort(key=lambda item: (item.date != "", item.date), reverse=True)
        groups.append(
            ContributionGroup(
                key=key,
                heading=heading,
                noun=noun,
                items=tuple(items),
            )
        )
    return tuple(groups)


def person_view(record: dict[str, Any], *, now: datetime | None = None) -> Person:
    """Return one profile as the value the person page renders."""

    current = now or timezone.now()
    if timezone.is_naive(current):
        current = timezone.make_aware(current)
    image_path = str(record.get("image_path") or "")
    return Person(
        name=_required_text(record, "title"),
        public_path=person_public_path(record),
        summary=str(record.get("summary") or ""),
        blocks=tuple(record.get("blocks", ())),
        roles=_roles(record),
        links=_links(record),
        # A portrait the media index cannot serve is not shown at all; the page
        # never invents a stand-in face for a person.
        image_path=image_path if record.get("media_available") else "",
        groups=_groups(record, now=current),
    )
