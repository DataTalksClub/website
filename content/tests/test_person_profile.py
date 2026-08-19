"""The public person profile, rebuilt on design 5a (issue #179).

The page gathers everything one person did across the site.  These tests hold the
two promises that rebuild makes: every fact on the page is read from the checked
profile and from the record each contribution points at, and a profile with any
subset of those facts — no summary, no links, no work at all — still renders an
honest page instead of a padded one.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase
from django.utils.html import escape

from content.person_content import (
    ROWS_BEFORE_FOLD,
    SMALLEST_FOLD,
    Contribution,
    ContributionGroup,
    person_view,
)
from content.public_data import public_projection

# The profile with the widest body of work in the catalogue (63 links across all
# four kinds), and one that carries a portrait, a bio and links but no work.
RICH_SLUG = "alexeygrigorev"
SPARSE_SLUG = "aaronwishnick"


def profile(slug: str) -> dict[str, Any]:
    return public_projection()["people_by_slug"][slug]


class PersonCompositionTests(SimpleTestCase):
    def test_every_profile_composes_without_invention(self) -> None:
        for record in public_projection()["people"]:
            with self.subTest(slug=record["slug"]):
                person = person_view(record)
                self.assertEqual(person.name, record["title"])
                self.assertEqual(person.public_path, record["public_path"])
                self.assertEqual(person.contribution_total, len(record["relationships"]))
                self.assertEqual(
                    [link.url for link in person.links],
                    [link["url"] for link in record["links"]],
                )
                linked = {item.public_path for group in person.groups for item in group.items}
                self.assertEqual(
                    linked,
                    {relationship["public_path"] for relationship in record["relationships"]},
                )

    def test_contributions_are_grouped_by_what_they_are(self) -> None:
        person = person_view(profile(RICH_SLUG))
        self.assertEqual(
            [(group.key, group.count) for group in person.groups],
            [("podcast", 5), ("events", 50), ("blog", 7), ("books", 1)],
        )
        self.assertEqual(
            [group.heading for group in person.groups],
            ["Podcast episodes", "Events", "Articles", "Books"],
        )
        for group in person.groups:
            with self.subTest(group=group.key):
                prefix = f"/{'blog' if group.key == 'blog' else group.key}/"
                for item in group.items:
                    self.assertTrue(item.public_path.startswith(prefix), item.public_path)

    def test_each_group_is_ordered_newest_first_by_its_own_record(self) -> None:
        person = person_view(profile(RICH_SLUG))
        for group in person.groups:
            with self.subTest(group=group.key):
                dated = [item.date for item in group.items if item.date]
                self.assertEqual(dated, sorted(dated, reverse=True))
                # A record without a date keeps its place at the end rather than
                # being given one it does not have.
                undated = [index for index, item in enumerate(group.items) if not item.date]
                self.assertEqual(undated, list(range(len(dated), len(group.items))))

    def test_markers_and_dates_are_read_from_the_linked_record(self) -> None:
        projection = public_projection()
        person = person_view(profile(RICH_SLUG))
        groups = {group.key: group for group in person.groups}

        episode = groups["podcast"].items[0]
        source = projection["podcasts_by_path"][episode.public_path]
        self.assertEqual(
            episode.pill_label,
            f"Season {source['season']} · Episode {source['episode']}",
        )
        self.assertEqual(episode.pill_variant, "status-pill-mint")
        self.assertEqual(episode.date, source["published"][:10])
        self.assertEqual(episode.mark, "play")

        event = groups["events"].items[0]
        event_source = projection["events_by_path"][event.public_path]
        self.assertEqual(event.pill_label, event_source["type"])
        # A date is the row's rail, not a mark: only the podcast disc is one, and
        # the shared archive row writes the date itself from this one field.
        self.assertEqual(event.mark, "")
        self.assertRegex(event.date, r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn(event.date[:4], event_source["starts_at"])

        article = groups["blog"].items[0]
        article_source = projection["articles_by_path"][article.public_path]
        self.assertEqual(article.note, article_source["description"])
        self.assertEqual(article.date, article_source["published"][:10])
        self.assertEqual(article.pill_label, "")

        book = groups["books"].items[0]
        book_source = projection["books_by_path"][book.public_path]
        self.assertEqual(book.date, book_source["published"][:10])
        # A book's authors are source keys, not names: the row states no author.
        self.assertEqual(book.note, "")

    def test_a_future_event_says_upcoming_rather_than_relying_on_its_colour(self) -> None:
        record = profile(RICH_SLUG)
        before = datetime(2000, 1, 1, tzinfo=UTC)
        after = datetime(2100, 1, 1, tzinfo=UTC)

        upcoming = next(
            group for group in person_view(record, now=before).groups if group.key == "events"
        )
        self.assertTrue(all(item.state_label == "upcoming" for item in upcoming.items))
        self.assertTrue(all(item.pill_variant != "status-pill-wait" for item in upcoming.items))

        past = next(
            group for group in person_view(record, now=after).groups if group.key == "events"
        )
        self.assertTrue(all(item.state_label == "" for item in past.items))
        self.assertTrue(all(item.pill_variant == "status-pill-wait" for item in past.items))

        # Nothing else on the page ever carries the marker.
        for group in person_view(record, now=before).groups:
            if group.key != "events":
                self.assertTrue(all(item.state_label == "" for item in group.items))

    def test_an_episode_without_a_date_states_none(self) -> None:
        projection = public_projection()
        silent = next(
            record
            for record in projection["people"]
            if any(
                relationship["public_path"].startswith("/podcast/")
                and not projection["podcasts_by_path"][relationship["public_path"]]["published"]
                for relationship in record["relationships"]
            )
        )
        person = person_view(silent)
        undated = [
            item
            for group in person.groups
            if group.key == "podcast"
            for item in group.items
            if not projection["podcasts_by_path"][item.public_path]["published"]
        ]
        self.assertTrue(undated)
        for item in undated:
            self.assertEqual(item.date, "")

    def test_a_long_group_keeps_its_first_rows_and_folds_the_rest(self) -> None:
        """Fifty events is a wall, not a list; three is neither.

        A group shows ``ROWS_BEFORE_FOLD`` rows and folds the remainder, and only
        when there is a remainder worth a control — hiding one or two rows behind
        something the reader has to click is worse than showing them.
        """

        groups = {group.key: group for group in person_view(profile(RICH_SLUG)).groups}

        events = groups["events"]
        self.assertEqual(events.count, 50)
        self.assertEqual(len(events.visible_items), ROWS_BEFORE_FOLD)
        self.assertEqual(events.folded_count, 50 - ROWS_BEFORE_FOLD)
        self.assertEqual(events.fold_label, "Show 44 more events")
        self.assertEqual(events.fold_close_label, "Show fewer events")
        # The fold reorders nothing: it is the same list, cut in two.
        self.assertEqual((*events.visible_items, *events.folded_items), events.items)

        # Five episodes and seven articles are lists a reader can simply read.
        for key in ("podcast", "blog", "books"):
            with self.subTest(group=key):
                group = groups[key]
                self.assertEqual(group.folded_count, 0)
                self.assertEqual(group.folded_items, ())
                self.assertEqual(group.visible_items, group.items)
                self.assertEqual(group.fold_label, "")

    def test_a_group_folds_only_once_it_would_hide_more_than_a_couple_of_rows(self) -> None:
        def group_of(total: int) -> ContributionGroup:
            return ContributionGroup(
                key="events",
                heading="Events",
                noun="event",
                items=tuple(
                    Contribution(
                        role="speaker",
                        title=f"Event {index}",
                        public_path=f"/events/{index}/event",
                        date="2025-01-01",
                        pill_label="webinar",
                        pill_variant="status-pill-wait",
                        state_label="",
                        note="",
                        mark="",
                    )
                    for index in range(total)
                ),
            )

        for total in range(ROWS_BEFORE_FOLD + SMALLEST_FOLD):
            with self.subTest(total=total):
                self.assertEqual(group_of(total).folded_count, 0)
        folded = group_of(ROWS_BEFORE_FOLD + SMALLEST_FOLD)
        self.assertEqual(folded.folded_count, SMALLEST_FOLD)
        self.assertEqual(folded.fold_label, f"Show {SMALLEST_FOLD} more events")
        self.assertEqual(group_of(ROWS_BEFORE_FOLD + 1).fold_label, "")

    def test_counts_and_role_phrases_agree_with_the_rows(self) -> None:
        person = person_view(profile(RICH_SLUG))
        labels = {group.key: (group.count_label, group.role_phrase) for group in person.groups}
        self.assertEqual(labels["podcast"], ("5 episodes", "guest"))
        self.assertEqual(labels["events"], ("50 events", "speaker"))
        self.assertEqual(labels["books"], ("1 book", "author"))

    def test_a_profile_may_carry_any_subset_of_its_own_facts(self) -> None:
        sparse = person_view(profile(SPARSE_SLUG))
        self.assertEqual(sparse.groups, ())
        self.assertEqual(sparse.contribution_total, 0)
        self.assertEqual(sparse.roles, ())
        self.assertTrue(sparse.image_path)
        self.assertTrue(sparse.blocks)

        without_links = [
            person_view(record) for record in public_projection()["people"] if not record["links"]
        ]
        self.assertTrue(without_links)
        for person in without_links:
            self.assertEqual(person.links, ())

        # Only sixteen profiles carry a summary; the rest simply have none.
        summarised = [record for record in public_projection()["people"] if record["summary"]]
        self.assertEqual(len(summarised), 16)
        self.assertEqual(person_view(summarised[0]).summary, summarised[0]["summary"])

    def test_a_portrait_the_media_index_cannot_serve_is_not_shown(self) -> None:
        record = {**profile(SPARSE_SLUG), "media_available": False}
        self.assertEqual(person_view(record).image_path, "")

    def test_missing_or_invented_identity_fails_closed(self) -> None:
        record = profile(SPARSE_SLUG)
        cases: tuple[tuple[str, dict[str, Any]], ...] = (
            ("no name", {**record, "title": ""}),
            ("guessed path", {**record, "public_path": "/people/someone-else.html"}),
            ("unnamed link", {**record, "links": [{"label": "", "url": "https://example.com"}]}),
            (
                "link that is not an address",
                {**record, "links": [{"label": "X", "url": "ftp://x"}]},
            ),
            (
                "unknown collection",
                {
                    **record,
                    "relationships": [{"role": "guest", "label": "X", "public_path": "/x/y"}],
                },
            ),
            (
                "missing record",
                {
                    **record,
                    "relationships": [
                        {"role": "guest", "label": "X", "public_path": "/podcast/nothing.html"}
                    ],
                },
            ),
            (
                "unnamed contribution",
                {
                    **record,
                    "relationships": [
                        {
                            "role": "",
                            "label": "X",
                            "public_path": "/podcast/data-team-roles.html",
                        }
                    ],
                },
            ),
        )
        for name, broken in cases:
            with self.subTest(case=name):
                with self.assertRaises(ImproperlyConfigured):
                    person_view(broken)


class PersonPageTests(TestCase):
    def rendered(self, slug: str) -> str:
        response = self.client.get(profile(slug)["public_path"])
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_page_carries_one_inline_stylesheet_and_no_legacy_css(self) -> None:
        for slug in (RICH_SLUG, SPARSE_SLUG):
            with self.subTest(slug=slug):
                body = self.rendered(slug)
                self.assertIn("<style>", body)
                self.assertIn("--bubble:", body)
                self.assertEqual(re.findall(r'<link[^>]+rel="stylesheet"', body), [])
                for retired in (
                    "/static/courses.css",
                    "/static/core/site_shell.css",
                    "/static/core/accessibility.css",
                    "tailwindcss",
                    "fontawesome",
                ):
                    self.assertNotIn(retired, body)
                for leak in ("{#", "#}", "{%", "%}", "{{", "}}"):
                    self.assertNotIn(leak, body)

    def test_the_profile_keeps_its_portrait_roles_summary_and_external_links(self) -> None:
        record = profile(RICH_SLUG)
        response = self.client.get(record["public_path"])
        body = response.content.decode()

        self.assertContains(response, f'alt="Portrait of {escape(record["title"])}"', count=1)
        self.assertContains(response, f'src="{record["image_path"]}"')
        self.assertContains(response, f'<h1 id="person-heading">{escape(record["title"])}</h1>')
        for role in record["roles"]:
            self.assertIn(f'<li class="chip">{escape(role.title())}</li>', body)
        self.assertIn('aria-label="Community roles"', body)
        self.assertIn('aria-label="Public profile links"', body)
        profile_links = re.search(r'<nav class="person-links".*?</nav>', body, re.S)
        assert profile_links is not None
        nav = profile_links.group(0)
        for link in record["links"]:
            self.assertIn(f'href="{link["url"]}"', nav)
            self.assertIn(escape(link["label"]), nav)
        self.assertEqual(nav.count('target="_blank"'), len(record["links"]))
        self.assertEqual(nav.count('rel="noopener noreferrer"'), len(record["links"]))
        self.assertEqual(nav.count("(opens in a new tab)"), len(record["links"]))
        # The profile's own words survive the rebuild.
        for block in record["blocks"]:
            self.assertIn(escape(block["text"]), body)

    def test_every_contribution_keeps_its_link_role_and_marker(self) -> None:
        record = profile(RICH_SLUG)
        body = self.rendered(RICH_SLUG)
        person = person_view(record)

        for relationship in record["relationships"]:
            self.assertIn(f'href="{relationship["public_path"]}"', body)
            self.assertIn(escape(relationship["label"]), body)
        for role in {relationship["role"] for relationship in record["relationships"]}:
            self.assertIn(f'<p class="mono-label">{escape(role)}</p>', body)

        for group in person.groups:
            self.assertIn(f'<h2 id="{group.anchor}-heading">{escape(group.heading)}</h2>', body)
            self.assertIn(f"person-rows-{group.key}", body)
        # Every contribution is the site's shared archive row — the same row the
        # blog, the books archive and the podcast index draw — and the rows
        # behind a group's fold are in the page too, not fetched on demand.
        self.assertEqual(
            body.count('class="list-row archive-row person-row"'),
            len(record["relationships"]),
        )
        self.assertEqual(body.count('class="play-disc"'), 5)
        # Each dated row leads with the shared two-line date rail, and an undated
        # one gives that column back to the card.
        dated = [item for group in person.groups for item in group.items if item.date]
        self.assertEqual(body.count('class="mono-note archive-date date-rail"'), len(dated))
        self.assertNotIn('class="when"', body)
        self.assertIn('class="status-pill status-pill-mint"', body)
        self.assertIn('class="stat-tiles person-stats"', body)
        # Design 5a bands, one per kind of work, and no invented catalogue link.
        # Every band after the hero takes the content ground, so a profile with
        # four kinds of work still reads as one page.
        self.assertEqual(
            body.count('class="band band-lavender person-contributions"'),
            len(person.groups),
        )
        self.assertNotIn('<section class="band band-mint', body)
        self.assertNotIn('<section class="band band-cream person-contributions', body)
        self.assertNotIn('href="/people"', body)

    def test_a_long_group_offers_one_control_that_says_what_it_is_holding(self) -> None:
        """The fold is a <details>: no script, and it opens with JavaScript off."""

        body = self.rendered(RICH_SLUG)
        person = person_view(profile(RICH_SLUG))
        folded = [group for group in person.groups if group.folded_count]
        self.assertEqual([group.key for group in folded], ["events"])

        self.assertEqual(body.count('<details class="row-fold"'), 1)
        self.assertIn('id="person-events-more"', body)
        self.assertIn('<span class="row-fold-open">Show 44 more events</span>', body)
        self.assertIn('<span class="row-fold-close">Show fewer events</span>', body)
        # It is a control the browser owns: no script, and no ARIA restating what
        # <details> already announces.
        fold = re.search(r'<details class="row-fold".*?</summary>', body, re.S)
        assert fold is not None
        self.assertNotIn("aria-expanded", fold.group(0))
        self.assertNotIn("onclick", fold.group(0))

        # The first rows stay in view; only the remainder are inside the fold.
        opening, _, remainder = body.partition('<details class="row-fold"')
        events_band = opening.rpartition("person-rows-events")[2]
        self.assertEqual(events_band.count('class="list-row archive-row person-row"'), 6)
        self.assertEqual(
            remainder.partition("</details>")[0].count('class="list-row archive-row person-row"'),
            44,
        )
        # A short group offers no control at all.
        self.assertNotIn("Show 4 more episodes", body)

    def test_a_profile_without_work_says_so_instead_of_padding_the_page(self) -> None:
        record = profile(SPARSE_SLUG)
        body = self.rendered(SPARSE_SLUG)

        self.assertIn("No podcast episode, event, article or book on DataTalks.Club lists", body)
        self.assertIn(escape(record["title"]), body)
        self.assertNotIn('class="stat-tiles person-stats"', body)
        self.assertNotIn('class="row-list', body)
        self.assertNotIn('aria-label="Community roles"', body)
        self.assertNotIn('class="person-summary"', body)
        self.assertIn('id="person-contributions-heading"', body)

    def test_the_page_keeps_its_canonical_identity_and_structured_data(self) -> None:
        record = profile(RICH_SLUG)
        response = self.client.get(record["public_path"])
        body = response.content.decode()

        self.assertIn(
            f'<link rel="canonical" href="https://datatalks.club{record["public_path"]}">',
            body,
        )
        self.assertIn('<meta property="og:type" content="profile">', body)
        self.assertIn(
            f'<meta property="og:image" content="https://datatalks.club{record["image_path"]}">',
            body,
        )
        self.assertIn('"@type": "Person"', body)
        for link in record["links"]:
            self.assertIn(link["url"], body)
        self.assertIn(f"<title>{escape(record['title'])} — DataTalks.Club</title>", body)

    def test_an_apostrophe_in_a_profile_title_is_escaped_in_the_heading_and_portrait(
        self,
    ) -> None:
        record = profile("elleobrien")
        self.assertNotEqual(escape(record["title"]), record["title"])
        response = self.client.get(record["public_path"])

        self.assertContains(response, f'alt="Portrait of {escape(record["title"])}"', count=1)
        self.assertContains(response, f'<h1 id="person-heading">{escape(record["title"])}</h1>')
        self.assertContains(response, f"<title>{escape(record['title'])} — DataTalks.Club</title>")

    def test_the_profile_is_one_h1_and_labelled_bands(self) -> None:
        body = self.rendered(RICH_SLUG)
        self.assertEqual(len(re.findall(r"<h1\b", body)), 1)
        for section in re.findall(r"<section class=\"band[^\"]*\"([^>]*)>", body):
            self.assertIn("aria-labelledby", section)

    def test_an_unknown_profile_is_still_a_404(self) -> None:
        self.assertEqual(self.client.get("/people/__nobody__.html").status_code, 404)
