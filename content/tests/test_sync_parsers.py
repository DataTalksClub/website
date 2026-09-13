from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from community_base.content_sync.checkout import ImmutableCheckout
from community_base.content_sync.media import media_store
from community_base.content_sync.models import ContentSource
from community_base.content_sync.parsers import get_parser
from django.test import TestCase

from content.models import SyncedDocument
from content.sync_parsers.base import ContentParserError


def _person_markdown() -> str:
    return (
        "---\n"
        "short: alexeygrigorev\n"
        'title: "Alexey Grigorev"\n'
        'picture: "images/authors/alexeygrigorev.jpg"\n'
        'bio_short: "Author of ML bookcamp."\n'
        "linkedin: alexeygrigorev\n"
        "---\n"
        "\n"
        "Author of ML bookcamp.\n"
    )


def _article_markdown() -> str:
    return (
        "---\n"
        "title: Hello World\n"
        "authors:\n"
        "- alexeygrigorev\n"
        "description: A first post.\n"
        "datepublished: '2024-03-07'\n"
        "image: images/posts/hello-world/cover.png\n"
        "---\n"
        "\n"
        "A first paragraph.\n"
        "\n"
        "## Section One\n"
        "\n"
        "A second paragraph.\n"
    )


def _write_tree(tree: dict[str, bytes]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="sync-parsers-test-"))
    for relative, payload in tree.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return root


class _CheckoutCase(TestCase):
    def checkout(self, tree: dict[str, bytes], commit: str = "a" * 40) -> ImmutableCheckout:
        return ImmutableCheckout(_write_tree(tree), commit_sha=commit)


def _source(slug: str) -> ContentSource:
    return ContentSource.objects.create(
        slug=slug,
        repo_name=f"DataTalksClub/{slug}",
        webhook_secret="test-secret",
        max_files=100,
    )


class PeopleParserTests(_CheckoutCase):
    def test_discover_upsert_update_and_delete(self) -> None:
        source = _source("dtc-main-site")
        parser = get_parser("people")
        tree = {
            "_people/_template.md": b"template\n",
            "_people/alexeygrigorev.md": _person_markdown().encode(),
            "images/authors/alexeygrigorev.jpg": b"jpeg-bytes",
        }
        with self.checkout(tree) as checkout:
            items = parser.discover(checkout, source)
            self.assertEqual([item.key for item in items], ["alexeygrigorev"])
            record = items[0].data["record"]
            self.assertEqual(record["public_path"], "/people/alexeygrigorev.html")
            self.assertEqual(
                record["provenance"]["revision"],
                "a" * 40,
            )
            result = parser.upsert(items[0], source, media_store())
        self.assertEqual(result.action, "created")
        stored = SyncedDocument.objects.get(source=source, content_kind="people")
        self.assertEqual(stored.slug, "alexeygrigorev")
        self.assertEqual(stored.checksum, items[0].data["checksum"])
        self.assertTrue(stored.is_published)

        with self.checkout(tree) as checkout:
            (item,) = parser.discover(checkout, source)
            result = parser.upsert(item, source, media_store())
        self.assertEqual(result.action, "unchanged")

        changed = dict(tree)
        changed["_people/alexeygrigorev.md"] = (
            _person_markdown().replace("Author of ML bookcamp.", "Updated biography.").encode()
        )
        with self.checkout(changed, commit="b" * 40) as checkout:
            (item,) = parser.discover(checkout, source)
            result = parser.upsert(item, source, media_store())
        self.assertEqual(result.action, "updated")

        self.assertEqual(parser.soft_delete_missing(set(), source), 1)
        self.assertFalse(SyncedDocument.objects.filter(source=source).exists())

    def test_ignores_other_sources(self) -> None:
        other = _source("some-other-site")
        parser = get_parser("people")
        with self.checkout({"_people/alexeygrigorev.md": _person_markdown().encode()}) as checkout:
            self.assertEqual(parser.discover(checkout, other), [])
            self.assertEqual(parser.soft_delete_missing(set(), other), 0)

    def test_rejects_non_public_fields(self) -> None:
        source = _source("dtc-main-site")
        parser = get_parser("people")
        marked_up = _person_markdown().replace("linkedin: alexeygrigorev\n", "notes: private\n")
        with self.checkout({"_people/alexeygrigorev.md": marked_up.encode()}) as checkout:
            with self.assertRaises(ContentParserError):
                parser.discover(checkout, source)


class ArticlesParserTests(_CheckoutCase):
    def test_discover_upsert_and_delete(self) -> None:
        source = _source("dtc-content")
        parser = get_parser("article")
        tree = {
            "articles/2024-03-07-hello-world.md": _article_markdown().encode(),
            "images/posts/hello-world/cover.png": b"png-bytes",
        }
        with self.checkout(tree) as checkout:
            items = parser.discover(checkout, source)
            self.assertEqual([item.key for item in items], ["hello-world"])
            record = items[0].data["record"]
            self.assertEqual(record["public_path"], "/blog/hello-world.html")
            self.assertEqual(record["published"], "2024-03-07")
            self.assertEqual(record["authors"], ["alexeygrigorev"])
            self.assertIsNone(record["faq"])
            self.assertTrue(
                any(
                    block["kind"] == "heading" and block["text"] == "Section One"
                    for block in record["blocks"]
                )
            )
            result = parser.upsert(items[0], source, media_store())
        self.assertEqual(result.action, "created")
        stored = SyncedDocument.objects.get(source=source, content_kind="article")
        self.assertEqual(stored.title, "Hello World")
        self.assertEqual(parser.soft_delete_missing(set(), source), 1)
        self.assertFalse(SyncedDocument.objects.filter(source=source).exists())

    def test_frontmatter_faq_is_validated_into_pairs(self) -> None:
        source = _source("dtc-content")
        parser = get_parser("article")
        marked_up = _article_markdown().replace(
            "---\n",
            "---\nfaq:\n- question: What is this?\n  answer: 'A test.'\n",
            1,
        )
        with self.checkout({"articles/2024-03-07-hello-world.md": marked_up.encode()}) as checkout:
            (item,) = parser.discover(checkout, source)
        self.assertEqual(
            item.data["record"]["faq"],
            [{"id": "faq-what-is-this", "question": "What is this?", "answer": "A test."}],
        )

    def test_ignores_other_sources_and_undated_names(self) -> None:
        other = _source("some-other-site")
        parser = get_parser("article")
        tree = {
            "articles/2024-03-07-hello-world.md": _article_markdown().encode(),
            "articles/notes.md": _article_markdown().encode(),
        }
        with self.checkout(tree) as checkout:
            self.assertEqual(parser.discover(checkout, other), [])
            with self.assertRaises(ContentParserError):
                parser.discover(checkout, _source("dtc-content"))


class RegistrationTests(unittest.TestCase):
    def test_site_parsers_are_registered(self) -> None:
        self.assertEqual(type(get_parser("article")).__name__, "ArticlesParser")
        self.assertEqual(type(get_parser("people")).__name__, "PeopleParser")
