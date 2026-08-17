"""One person, drawn the same way everywhere they are credited.

Two promises are held here.  The first is the shared chip itself: whatever names
a person — a projected author or guest credit, an event speaker, a composed
podcast guest — resolves to the same four facts, and each of them degrades on its
own rather than dropping the credit.

The second is the books archive, which is what sent this work: a book's authors
were a list of raw source keys, so the book page printed "By nikolaysmorchkov"
as plain unlinked text, the archive rows showed no author at all, and the page's
own structured data offered those keys to search engines as author *names*.
"""

from __future__ import annotations

import json
import re

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase
from django.utils.html import escape

from content.person_chip import person_chip, person_chips
from content.podcast_content import episode_view
from content.public_data import public_projection

# The book the owner reported: one author, who has a profile and a portrait.
REPORTED_BOOK = "20251006-software-development-at-rocket-speed"
# A book crediting two authors the community never hosted alongside one it did.
MIXED_BOOK = "20210208-ml-design-patterns"


def _match(pattern: str, body: str, group: int) -> str:
    found = re.search(pattern, body, re.S)
    assert found is not None, pattern
    return found.group(group)


class PersonChipResolutionTests(SimpleTestCase):
    def test_a_credit_with_a_key_gains_the_name_link_and_portrait_of_that_person(self) -> None:
        projection = public_projection()
        person = projection["people_by_slug"]["alexeygrigorev"]

        chip = person_chip({"key": "alexeygrigorev", "name": "Alexey Grigorev", "public_path": ""})

        self.assertEqual(chip.name, "Alexey Grigorev")
        self.assertEqual(chip.image_path, person["image_path"])
        self.assertTrue(chip.media_available)

    def test_a_credit_with_only_a_profile_path_still_finds_its_portrait(self) -> None:
        """A composed value — a podcast `Guest` — carries no source key."""

        projection = public_projection()
        person = projection["people_by_slug"]["alexeygrigorev"]

        chip = person_chip({"name": person["title"], "public_path": person["public_path"]})

        self.assertEqual(chip.public_path, person["public_path"])
        self.assertEqual(chip.image_path, person["image_path"])

    def test_a_credit_the_people_records_cannot_place_keeps_its_name_and_nothing_else(
        self,
    ) -> None:
        chip = person_chip({"key": "", "name": "Sara Robinson", "public_path": ""})

        self.assertEqual(chip.name, "Sara Robinson")
        self.assertEqual(chip.public_path, "")
        self.assertEqual(chip.image_path, "")
        self.assertFalse(chip.media_available)

    def test_a_nameless_credit_and_an_off_site_link_are_refused(self) -> None:
        with self.assertRaises(ImproperlyConfigured):
            person_chip({"key": "alexeygrigorev", "name": "", "public_path": ""})
        with self.assertRaises(ImproperlyConfigured):
            person_chip({"name": "Someone", "public_path": "https://example.com/"})

    def test_every_projected_credit_on_the_site_resolves_to_a_named_chip(self) -> None:
        """Nothing that names a person may reach a reader as a bare source key."""

        projection = public_projection()
        slugs = set(projection["people_by_slug"])
        credits = [
            *(
                credit
                for record in (*projection["articles"], *projection["books"])
                for credit in record["author_profiles"]
            ),
            *(credit for record in projection["podcasts"] for credit in record["guest_profiles"]),
            *(speaker for event in projection["events"] for speaker in event["speakers"]),
        ]

        for chip in person_chips(credits):
            self.assertTrue(chip.name)
            self.assertNotIn(chip.name, slugs)
            if chip.public_path:
                self.assertTrue(chip.public_path.startswith("/people/"))


class BookAuthorResolutionTests(SimpleTestCase):
    def test_every_book_author_becomes_a_name_and_keeps_its_profile_when_there_is_one(
        self,
    ) -> None:
        projection = public_projection()
        people = projection["people_by_slug"]

        for book in projection["books"]:
            with self.subTest(slug=book["slug"]):
                self.assertEqual(len(book["author_profiles"]), len(book["authors"]))
                for author, credit in zip(book["authors"], book["author_profiles"], strict=True):
                    person = people.get(author)
                    if person is None:
                        # A co-author the community never hosted: the source
                        # writes their name out, and it stays a name.
                        self.assertEqual(credit["name"], author)
                        self.assertEqual(credit["public_path"], "")
                    else:
                        self.assertEqual(credit["name"], person["title"])
                        self.assertEqual(credit["public_path"], person["public_path"])
                        self.assertNotEqual(credit["name"], author)

    def test_the_unresolved_book_authors_are_a_named_inventory_not_a_surprise(self) -> None:
        projection = public_projection()
        people = projection["people_by_slug"]

        unresolved = sorted(
            {
                author
                for book in projection["books"]
                for author in book["authors"]
                if author not in people
            }
        )

        self.assertEqual(
            unresolved,
            [
                "Ajay Uppili Arasanipalai",
                "Alfredo Deza",
                "Anita Kibunguchy-Grant",
                "Catherine Nelson",
                "Dipanjan Sarkar",
                "Evren Eryurek",
                "John Berryman",
                "Joseph Babcock",
                "Josh Perryman",
                "Justin Mullen",
                "Konrad Banachewicz",
                "Luca Massaron",
                "Max Irwin",
                "Sara Robinson",
                "Trey Grainger",
                "Valliappa Lakshmanan",
            ],
        )
        # Every one of them is already a written name, never a source key.
        for name in unresolved:
            self.assertRegex(name, r"^[A-Z]")
            self.assertIn(" ", name)


class BookPageBylineTests(TestCase):
    def book(self, slug: str) -> dict:
        return public_projection()["books_by_slug"][slug]

    def test_the_reported_book_names_its_author_and_links_to_the_profile(self) -> None:
        record = self.book(REPORTED_BOOK)
        body = self.client.get(record["public_path"]).content.decode()

        self.assertIn(
            '<a class="band-link person-chip-name" '
            'href="/people/nikolaysmorchkov.html">Nikolay Smorchkov</a>',
            body,
        )
        # The source key never reaches the reader as if it were a name.
        self.assertNotIn(">nikolaysmorchkov<", body)
        self.assertNotIn("By nikolaysmorchkov", body)

    def test_an_author_without_a_profile_is_named_but_not_linked(self) -> None:
        body = self.client.get(self.book(MIXED_BOOK)["public_path"]).content.decode()

        self.assertIn('<span class="person-chip-name">Valliappa Lakshmanan</span>', body)
        self.assertIn('<span class="person-chip-name">Sara Robinson</span>', body)
        self.assertIn(
            '<a class="band-link person-chip-name" '
            'href="/people/michaelmunn.html">Michael Munn</a>',
            body,
        )

    def test_the_books_structured_data_carries_names_rather_than_source_keys(self) -> None:
        record = self.book(REPORTED_BOOK)
        body = self.client.get(record["public_path"]).content.decode()
        payload = json.loads(
            _match(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', body, 1)
        )
        book = next(item for item in payload["@graph"] if item.get("@type") == "Book")

        self.assertEqual(
            book["author"],
            [
                {
                    "@type": "Person",
                    "name": "Nikolay Smorchkov",
                    "url": "https://datatalks.club/people/nikolaysmorchkov.html",
                }
            ],
        )

    def test_an_author_without_a_profile_is_named_in_structured_data_without_a_link(self) -> None:
        body = self.client.get(self.book(MIXED_BOOK)["public_path"]).content.decode()
        payload = json.loads(
            _match(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', body, 1)
        )
        book = next(item for item in payload["@graph"] if item.get("@type") == "Book")

        self.assertIn({"@type": "Person", "name": "Sara Robinson"}, book["author"])

    def test_the_books_archive_rows_credit_their_authors(self) -> None:
        record = self.book(REPORTED_BOOK)
        body = self.client.get("/books").content.decode()

        self.assertIn(escape(record["title"]), body)
        self.assertIn(
            '<a class="band-link person-chip-name" '
            'href="/people/nikolaysmorchkov.html">Nikolay Smorchkov</a>',
            body,
        )


class PersonChipRenderingTests(TestCase):
    def test_a_portrait_beside_its_own_name_says_nothing_to_a_screen_reader(self) -> None:
        """The name is the credit; a portrait that repeated it would be heard twice."""

        record = public_projection()["books_by_slug"][REPORTED_BOOK]
        body = self.client.get(record["public_path"]).content.decode()
        portrait = _match(r'<img\s+class="person-chip-portrait".*?>', body, 0)

        self.assertIn('alt=""', portrait)
        self.assertNotIn("Nikolay Smorchkov", portrait)
        # An intrinsic size, so the line cannot shift as the picture arrives.
        self.assertIn('width="96"', portrait)
        self.assertIn('height="96"', portrait)

    def test_a_credit_without_a_portrait_keeps_the_stand_in_disc(self) -> None:
        """Every profile carries a picture today; a credit with no profile does not.

        The one podcast guest the people records do not hold is exactly that
        case, so the episode still credits them — with the striped disc the
        design system reserves for a person it has no face for.
        """

        projection = public_projection()
        episode = next(
            record
            for record in projection["podcasts"]
            if any(not guest["public_path"] for guest in record["guest_profiles"])
        )
        guest = next(guest for guest in episode["guest_profiles"] if not guest["public_path"])
        body = self.client.get(episode["public_path"]).content.decode()

        self.assertIn(
            '<span class="avatar avatar-striped person-chip-portrait" aria-hidden="true">',
            body,
        )
        self.assertIn(f'<span class="person-chip-name">{escape(guest["name"])}</span>', body)

    def test_the_episode_page_draws_its_guest_with_the_shared_chip(self) -> None:
        projection = public_projection()
        episode = next(
            record
            for record in projection["podcasts"]
            if record["guest_profiles"] and record["guest_profiles"][0]["public_path"]
        )
        guest = episode_view(episode).guests[0]
        body = self.client.get(episode["public_path"]).content.decode()

        self.assertIn('<div class="guest-row person-chip-lead">', body)
        self.assertIn(
            f'<a class="band-link person-chip-name" '
            f'href="{guest.public_path}">{escape(guest.name)}</a>',
            body,
        )
        # The chip's own name is the way to the profile, so the row no longer
        # offers a second, unnamed "Profile" link beside it.
        self.assertNotIn(f'href="{guest.public_path}">Profile</a>', body)


class DesignSystemOwnershipTests(SimpleTestCase):
    def test_the_chip_is_a_shared_primitive_and_not_a_page_of_its_own(self) -> None:
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        partial = (root / "templates/core/_design_system.html").read_text(encoding="utf-8")

        for rule in (
            ".person-chip {",
            ".person-chip-portrait {",
            ".person-chip-name {",
            ".person-chip-lead .person-chip-portrait {",
            ".person-chips {",
            ".person-chips-label {",
        ):
            self.assertIn(rule, partial)

    def test_every_surface_that_credits_a_person_uses_the_one_partial(self) -> None:
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        # The archive-row surfaces credit their people through the shared row
        # (`public/_archive_row.html`), which draws the one chip partial itself;
        # the rest name the chip directly.  Either way there is one chip.
        through_the_row = (
            "templates/public/collection_hub.html",
            "templates/public/podcast_hub.html",
        )
        for template in (
            "templates/core/home.html",
            "templates/public/book_detail.html",
            "templates/public/collection_hub.html",
            "templates/public/event_detail.html",
            "templates/public/events.html",
            "templates/public/podcast_detail.html",
            "templates/public/podcast_hub.html",
        ):
            with self.subTest(template=template):
                source = (root / template).read_text(encoding="utf-8")
                if template in through_the_row:
                    self.assertIn('{% include "public/_archive_row.html"', source)
                    self.assertIn("row_credits=", source)
                    continue
                self.assertIn('{% include "public/_person_chip.html"', source)
                self.assertIn("|person_chip", source)

        row = (root / "templates/public/_archive_row.html").read_text(encoding="utf-8")
        self.assertIn('{% include "public/_person_chip.html"', row)
        self.assertIn("|person_chip", row)
