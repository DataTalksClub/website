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


def _episode_yaml() -> bytes:
    return (
        b"slug: test-episode\n"
        b"legacy_path: /podcast/test-episode.html\n"
        b"title: A test episode\n"
        b"short: Test episode\n"
        b"description: A safe description.\n"
        b"season: 3\n"
        b"episode: 11\n"
        b"dateadded: '2024-05-01'\n"
        b"guests:\n"
        b"- fixture-guest\n"
        b"image: images/podcast/test-episode.jpg\n"
        b"ids:\n"
        b"  youtube: abc12345678\n"
        b"links:\n"
        b"  youtube: https://www.youtube.com/watch?v=abc12345678\n"
        b"transcript: e11-transcript.yaml\n"
        b"resources:\n"
        b"- title: Example\n"
        b"  url: https://example.com/resource\n"
    )


def _transcript_yaml() -> bytes:
    return (
        b"podcast: test-episode\n"
        b"segments:\n"
        b"- who: Alexey\n"
        b"  line: Hello and welcome.\n"
        b"  sec: 0\n"
        b"  time: '00:00:00'\n"
    )


def _book_yaml() -> bytes:
    return (
        b"slug: 20201214-ml-bookcamp\n"
        b"legacy_path: /books/20201214-ml-bookcamp.html\n"
        b"title: Machine Learning Bookcamp\n"
        b"description: A deterministic book description.\n"
        b"summary: The summary prose.\n"
        b"start: 2020-12-14\n"
        b"authors:\n"
        b"- fixture-book-author\n"
        b"cover: images/books/20201214-ml-bookcamp/cover.jpg\n"
        b"links:\n"
        b"- text: Book page\n"
        b"  link: https://example.com/book\n"
        b"archive:\n"
        b"- name: Reader\n"
        b"  text: What makes this book useful?\n"
        b"  replies:\n"
        b"  - name: Author\n"
        b"    text: It teaches with projects.\n"
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
    # The reference data seeds the podwiki source for the catalogue's synced
    # reads, so a parser test for that slug reuses the row instead of colliding.
    return ContentSource.objects.get_or_create(
        slug=slug,
        defaults={
            "repo_name": f"DataTalksClub/{slug}",
            "webhook_secret": "test-secret",
            "max_files": 100,
        },
    )[0]


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


class PodcastsParserTests(_CheckoutCase):
    def test_discover_upsert_update_and_delete(self) -> None:
        source = _source("dtc-content")
        parser = get_parser("podcast")
        tree = {
            "podcasts/s03/e11.yaml": _episode_yaml(),
            "podcasts/s03/e11-transcript.yaml": _transcript_yaml(),
            "podcasts/_s12e08.yaml": b"slug: draft\n",
            "images/podcast/test-episode.jpg": b"jpeg-bytes",
        }
        with self.checkout(tree) as checkout:
            items = parser.discover(checkout, source)
            self.assertEqual([item.key for item in items], ["test-episode"])
            record = items[0].data["record"]
            self.assertEqual(record["public_path"], "/podcast/test-episode.html")
            self.assertEqual(record["season"], 3)
            self.assertEqual(record["episode"], 11)
            self.assertEqual(record["guests"], ["fixture-guest"])
            self.assertEqual(
                record["links"], {"youtube": "https://www.youtube.com/watch?v=abc12345678"}
            )
            self.assertEqual(record["video"], {"provider": "youtube", "id": "abc12345678"})
            self.assertEqual([resource["title"] for resource in record["resources"]], ["Example"])
            self.assertEqual(len(record["transcript"]), 1)
            self.assertEqual(record["transcript"][0]["line"], "Hello and welcome.")
            self.assertEqual(
                record["transcript_provenance"]["source_path"],
                "podcasts/s03/e11-transcript.yaml",
            )
            self.assertEqual(record["provenance"]["revision"], "a" * 40)
            result = parser.upsert(items[0], source, media_store())
        self.assertEqual(result.action, "created")
        stored = SyncedDocument.objects.get(source=source, content_kind="podcast")
        self.assertEqual(stored.slug, "test-episode")

        with self.checkout(tree) as checkout:
            (item,) = parser.discover(checkout, source)
            result = parser.upsert(item, source, media_store())
        self.assertEqual(result.action, "unchanged")

        changed = dict(tree)
        changed["podcasts/s03/e11.yaml"] = _episode_yaml().replace(
            b"A safe description.", b"Updated description."
        )
        with self.checkout(changed, commit="b" * 40) as checkout:
            (item,) = parser.discover(checkout, source)
            result = parser.upsert(item, source, media_store())
        self.assertEqual(result.action, "updated")

        self.assertEqual(parser.soft_delete_missing(set(), source), 1)
        self.assertFalse(SyncedDocument.objects.filter(source=source).exists())

    def test_rejects_misnamed_transcripts(self) -> None:
        source = _source("dtc-content")
        parser = get_parser("podcast")
        marked_up = _episode_yaml().replace(
            b"transcript: e11-transcript.yaml", b"transcript: wrong-name.yaml"
        )
        tree = {
            "podcasts/s03/e11.yaml": marked_up,
            "podcasts/s03/wrong-name.yaml": _transcript_yaml(),
        }
        with self.checkout(tree) as checkout:
            with self.assertRaises(ContentParserError):
                parser.discover(checkout, source)

    def test_accepts_flat_layout_transcript_references(self) -> None:
        source = _source("dtc-content")
        parser = get_parser("podcast")
        marked_up = _episode_yaml().replace(
            b"transcript: e11-transcript.yaml", b"transcript: transcripts/test-episode.yaml"
        )
        tree = {
            "podcasts/test-episode.yaml": marked_up,
            "podcasts/transcripts/test-episode.yaml": _transcript_yaml(),
        }
        with self.checkout(tree) as checkout:
            (item,) = parser.discover(checkout, source)
        self.assertEqual(len(item.data["record"]["transcript"]), 1)
        self.assertEqual(
            item.data["record"]["transcript_provenance"]["source_path"],
            "podcasts/transcripts/test-episode.yaml",
        )

    def test_ignores_other_sources(self) -> None:
        other = _source("some-other-site")
        parser = get_parser("podcast")
        tree = {"podcasts/s03/e11.yaml": _episode_yaml()}
        with self.checkout(tree) as checkout:
            self.assertEqual(parser.discover(checkout, other), [])
            self.assertEqual(parser.soft_delete_missing(set(), other), 0)


class BooksParserTests(_CheckoutCase):
    def test_discover_upsert_and_delete(self) -> None:
        source = _source("dtc-content")
        parser = get_parser("book")
        tree = {
            "books/2020/201214-ml-bookcamp.yaml": _book_yaml(),
            "books/_draft-book.yaml": b"slug: draft\n",
            "images/books/20201214-ml-bookcamp/cover.jpg": b"jpeg-bytes",
        }
        with self.checkout(tree) as checkout:
            items = parser.discover(checkout, source)
            self.assertEqual([item.key for item in items], ["20201214-ml-bookcamp"])
            record = items[0].data["record"]
            self.assertEqual(record["public_path"], "/books/20201214-ml-bookcamp.html")
            self.assertEqual(record["authors"], ["fixture-book-author"])
            self.assertEqual(record["published"], "2020-12-14")
            self.assertEqual(
                record["links"], [{"label": "Book page", "url": "https://example.com/book"}]
            )
            self.assertEqual(record["image_source"], "images/books/20201214-ml-bookcamp/cover.jpg")
            self.assertEqual(len(record["archive"]), 1)
            self.assertEqual(record["provenance"]["revision"], "a" * 40)
            result = parser.upsert(items[0], source, media_store())
        self.assertEqual(result.action, "created")
        stored = SyncedDocument.objects.get(source=source, content_kind="book")
        self.assertEqual(stored.title, "Machine Learning Bookcamp")
        self.assertEqual(parser.soft_delete_missing(set(), source), 1)
        self.assertFalse(SyncedDocument.objects.filter(source=source).exists())

    def test_ignores_other_sources(self) -> None:
        other = _source("some-other-site")
        parser = get_parser("book")
        tree = {"books/2020/201214-ml-bookcamp.yaml": _book_yaml()}
        with self.checkout(tree) as checkout:
            self.assertEqual(parser.discover(checkout, other), [])
            self.assertEqual(parser.soft_delete_missing(set(), other), 0)


def _docs_page(title: str, parent: str | None = None, body: str = "A paragraph.\n") -> bytes:
    frontmatter = f"title: {title}\nnav_order: 1\n"
    if parent is not None:
        frontmatter += f"parent: {parent}\n"
    return f"---\n{frontmatter}---\n\n{body}".encode()


class DocsParserTests(_CheckoutCase):
    def setUp(self) -> None:
        super().setUp()
        # The reference data seeds the synced docs rows the catalogue reads;
        # the parser contract tests exercise their own synced state from empty.
        SyncedDocument.objects.filter(source__slug="dtc-docs").delete()

    def test_discover_scopes_pages_and_resolves_hierarchy(self) -> None:
        source = _source("dtc-docs")
        other = _source("dtc-content")
        parser = get_parser("docs")
        tree = {
            "index.md": _docs_page("Docs Home"),
            "courses/faq-course/index.md": _docs_page("FAQ Course", parent="Docs Home"),
            "general/deep-dive.md": _docs_page(
                "Deep Dive",
                parent="FAQ Course",
                body="A paragraph.\n\n![Diagram](images/diagram.png)\n",
            ),
            "general/_partial.md": b"partial\n",
            "drafts/unfinished.md": _docs_page("Unfinished"),
            "README.md": _docs_page("Stray Readme"),
            "general/images/diagram.png": b"png-bytes",
        }
        with self.checkout(tree) as checkout:
            self.assertEqual(parser.discover(checkout, other), [])
            items = parser.discover(checkout, source)
            self.assertEqual(
                [item.key for item in items],
                ["courses/faq-course", "general/deep-dive", "index"],
            )
            course, deep, home = items
            self.assertEqual(home.data["record"]["public_path"], "/docs/")
            self.assertEqual(course.data["record"]["public_path"], "/docs/courses/faq-course/")
            self.assertEqual(deep.data["record"]["public_path"], "/docs/general/deep-dive/")
            home_metadata = home.data["record"]["metadata"]
            self.assertEqual(home_metadata["parent_path"], "")
            course_metadata = course.data["record"]["metadata"]
            self.assertEqual(course_metadata["parent"], "Docs Home")
            self.assertEqual(course_metadata["parent_path"], "/docs/")
            deep_metadata = deep.data["record"]["metadata"]
            self.assertEqual(deep_metadata["parent_path"], "/docs/courses/faq-course/")
            self.assertEqual(deep.data["record"]["images"], ["general/images/diagram.png"])

    def test_upsert_is_checksum_stable_and_uploads_referenced_images(self) -> None:
        source = _source("dtc-docs")
        parser = get_parser("docs")
        tree = {
            "general/deep-dive.md": _docs_page(
                "Deep Dive", body="A paragraph.\n\n![Diagram](images/diagram.png)\n"
            ),
            "general/images/diagram.png": b"png-bytes",
        }
        with self.checkout(tree) as checkout:
            items = parser.discover(checkout, source)
            result = parser.upsert(items[0], source, media_store())
            self.assertEqual(result.action, "created")
            stored = SyncedDocument.objects.get(source=source, content_kind="docs")
            self.assertEqual(stored.public_path, "/docs/general/deep-dive/")
            self.assertTrue(stored.record["metadata"]["edit_url"].endswith("general/deep-dive.md"))
            self.assertEqual(stored.record["metadata"]["has_toc"], True)
            self.assertEqual(parser.upsert(items[0], source, media_store()).action, "unchanged")
            self.assertEqual(parser.soft_delete_missing({"other"}, source), 1)
            self.assertFalse(SyncedDocument.objects.filter(source=source).exists())

    def test_rejects_colliding_public_paths(self) -> None:
        source = _source("dtc-docs")
        parser = get_parser("docs")
        tree = {
            "general/deep-dive.md": _docs_page("Deep Dive"),
            "general/deep-dive/index.md": _docs_page("Deep Dive Too"),
        }
        with self.checkout(tree) as checkout:
            with self.assertRaises(ContentParserError):
                parser.discover(checkout, source)


def _faq_course_metadata() -> bytes:
    return (
        b"course: test-zoomcamp\n"
        b'course_name: "Test Zoomcamp"\n'
        b"slack_channel: course-test\n"
        b"sections:\n"
        b"- id: general\n"
        b'  name: "General"\n'
        b"- id: empty\n"
        b'  name: "Empty Section"\n'
    )


def _faq_question(
    sort_order: int, question_id: str, slug: str, body: bytes = b"An answer.\n"
) -> bytes:
    frontmatter = (
        f"id: {question_id}\n"
        f"question: 'Question {sort_order} about {slug}'\n"
        f"sort_order: {sort_order}\n"
    ).encode()
    if b"IMAGE" in body:
        frontmatter += (
            b"images:\n"
            b"- description: 'shot'\n"
            b"  id: image_1\n" + f"  path: images/test-zoomcamp/{slug}.png\n".encode()
        )
    return b"---\n" + frontmatter + b"---\n\n" + body


class FaqParserTests(_CheckoutCase):
    def setUp(self) -> None:
        super().setUp()
        # The reference data seeds the synced FAQ rows the catalogue reads;
        # the parser contract tests exercise their own synced state from empty.
        SyncedDocument.objects.filter(source__slug="dtc-faq").delete()

    def test_discover_builds_course_tree_and_drops_empty_sections(self) -> None:
        source = _source("dtc-faq")
        parser = get_parser("faq")
        tree = {
            "_questions/test-zoomcamp/_metadata.yaml": _faq_course_metadata(),
            "_questions/test-zoomcamp/general/01_1234567890_first-q.md": _faq_question(
                1, "1234567890", "first-q"
            ),
            "_questions/test-zoomcamp/general/02_0987654321_second-q.md": _faq_question(
                2, "0987654321", "second-q"
            ),
            "_questions/test-zoomcamp/empty/.keep": b"",
        }
        with self.checkout(tree) as checkout:
            items = parser.discover(checkout, source)
            self.assertEqual([item.key for item in items], ["test-zoomcamp"])
            record = items[0].data["record"]
            self.assertEqual(record["course_name"], "Test Zoomcamp")
            self.assertEqual([section["id"] for section in record["sections"]], ["general"])
            self.assertEqual(
                [question["id"] for question in record["sections"][0]["questions"]],
                ["1234567890", "0987654321"],
            )
            self.assertEqual(record["sections"][0]["questions"][0]["slug"], "first-q")

    def test_upsert_uploads_declared_images_and_is_checksum_stable(self) -> None:
        source = _source("dtc-faq")
        parser = get_parser("faq")
        tree = {
            "_questions/test-zoomcamp/_metadata.yaml": _faq_course_metadata(),
            "_questions/test-zoomcamp/general/01_1234567890_first-q.md": _faq_question(
                1,
                "1234567890",
                "first-q",
                body=b"See the shot.\n\n<{IMAGE:image_1}>\n",
            ),
            "images/test-zoomcamp/first-q.png": b"png-bytes",
        }
        with self.checkout(tree) as checkout:
            items = parser.discover(checkout, source)
            result = parser.upsert(items[0], source, media_store())
            self.assertEqual(result.action, "created")
            stored = SyncedDocument.objects.get(source=source, content_kind="faq")
            self.assertEqual(stored.public_path, "/faq/test-zoomcamp.html")
            self.assertEqual(
                stored.record["sections"][0]["questions"][0]["images"][0]["path"],
                "images/test-zoomcamp/first-q.png",
            )
            self.assertEqual(parser.upsert(items[0], source, media_store()).action, "unchanged")
            self.assertEqual(parser.soft_delete_missing({"other"}, source), 1)
            self.assertFalse(SyncedDocument.objects.filter(source=source).exists())


def _wiki_page(title: str, body: str) -> bytes:
    return (
        f"---\ntitle: {title}\nsummary: A test page.\ntags:\n- testing\n---\n\n{body}\n"
    ).encode()


def _wiki_graph() -> bytes:
    import json

    return json.dumps(
        {
            "generated_at": "2024-05-01T00:00:00Z",
            "counts": {"guides": 0},
            "nodes": [
                {
                    "id": "wiki:hello-wiki",
                    "collection": "wiki",
                    "title": "Hello Wiki",
                    "type": "page",
                    "url": "/wiki/hello-wiki",
                },
                {
                    "id": "podcast:_s3e11_test-episode",
                    "collection": "podcast",
                    "title": "A test episode",
                    "type": "episode",
                    "url": "https://datatalks.club/podcast/test-episode",
                },
            ],
            "links": [
                {
                    "kind": "citation",
                    "source": "wiki:hello-wiki",
                    "target": "podcast:_s3e11_test-episode",
                    "weight": 1,
                }
            ],
        }
    ).encode()


def _wiki_search(fragment: str = "section-one", segment: str = "Section One") -> bytes:
    import json

    return json.dumps(
        {
            "docs": [
                {
                    "id": "hello-wiki",
                    "document_type": "wiki",
                    "title": "Hello Wiki",
                    "url": f"/wiki/hello-wiki#{fragment}",
                    "segment_title": segment,
                    "text": "Body text.",
                },
                {
                    "id": "episode",
                    "document_type": "podcast",
                    "title": "A test episode",
                    "url": "/podcast/s3e11/test-episode.html",
                    "episode_slug": "test-episode",
                    "text": "Episode text.",
                },
            ]
        }
    ).encode()


def _synced_row(source, content_kind: str, slug: str, public_path: str) -> SyncedDocument:
    return SyncedDocument.objects.create(
        source=source,
        content_kind=content_kind,
        stable_key=slug,
        slug=slug,
        title=slug,
        public_path=public_path,
        source_path=f"{content_kind}/{slug}",
        checksum="a" * 64,
    )


class PodwikiParserTests(_CheckoutCase):
    def setUp(self) -> None:
        super().setUp()
        # The reference data seeds the synced wiki rows the catalogue reads;
        # the parser contract tests exercise their own synced state from empty.
        SyncedDocument.objects.filter(source__slug="dtc-podwiki").delete()

    def _entities(self, podcast_slug: str = "test-episode") -> None:
        entities = _source("dtc-content-entities")
        _synced_row(entities, "podcast", podcast_slug, f"/podcast/s3e11/{podcast_slug}.html")
        _synced_row(entities, "book", "fixture-book", "/books/fixture-book.html")
        _synced_row(entities, "people", "fixture-person", "/people/fixture-person.html")

    def test_discover_builds_pages_and_singletons_from_other_rows(self) -> None:
        source = _source("dtc-podwiki")
        self._entities()
        parser = get_parser("wiki")
        tree = {
            "_wiki/hello-wiki.md": _wiki_page(
                "Hello Wiki",
                "Intro citing [[Hello Second]] and [[cite:test-episode]] "
                "plus [[person:fixture-person]].\n\n"
                "## Section One\n\nBody text.",
            ),
            "_wiki/hello-second.md": _wiki_page("Hello Second", "Second page."),
            "_wiki/nested/deep.md": _wiki_page("Nested Page", "Not a wiki page."),
            "graph/graph.json": _wiki_graph(),
            "search/search-corpus.json": _wiki_search(),
            "assets/og-default.png": b"\x89PNG\r\n\x1a\npng-bytes",
        }
        with self.checkout(tree) as checkout:
            items = parser.discover(checkout, source)
            self.assertEqual(
                [item.key for item in items],
                [
                    "hello-second",
                    "hello-wiki",
                    "$wiki_graph",
                    "$wiki_search",
                    "$wiki_assets",
                ],
            )
            page = items[1].data["record"]
            self.assertEqual(page["public_path"], "/wiki/hello-wiki")
            self.assertEqual(page["fragment_ids"], ["section-one"])
            self.assertEqual(
                [
                    (block["kind"], block.get("id"))
                    for block in page["blocks"]
                    if block["kind"] == "heading"
                ],
                [("heading", "section-one")],
            )
            self.assertEqual(
                sorted(relation["type"] for relation in page["relations"]),
                ["citation", "person", "wiki"],
            )
            relations = {relation["type"]: relation for relation in page["relations"]}
            self.assertEqual(relations["wiki"]["href"], "/wiki/hello-second")
            self.assertEqual(relations["citation"]["href"], "/podcast/s3e11/test-episode.html")
            self.assertEqual(relations["person"]["href"], "/people/fixture-person.html")
            graph = items[2].data["record"]
            self.assertEqual(graph["counts"], {"guides": 0, "nodes": 2, "links": 1, "podcasts": 1})
            self.assertEqual(
                [node["url"] for node in graph["nodes"]],
                ["/wiki/hello-wiki", "/podcast/s3e11/test-episode.html"],
            )

    def test_upsert_writes_pages_and_singletons_checksum_stably(self) -> None:
        source = _source("dtc-podwiki")
        self._entities()
        parser = get_parser("wiki")
        tree = {
            "_wiki/hello-wiki.md": _wiki_page(
                "Hello Wiki", "A section.\n\n## Section One\n\nBody text."
            ),
            "graph/graph.json": _wiki_graph(),
            "search/search-corpus.json": _wiki_search(),
            "assets/og-default.png": b"\x89PNG\r\n\x1a\npng-bytes",
        }
        with self.checkout(tree) as checkout:
            items = parser.discover(checkout, source)
            for item in items:
                result = parser.upsert(item, source, media_store())
                self.assertEqual(result.action, "created")
        self.assertEqual(
            sorted(
                SyncedDocument.objects.filter(source=source).values_list(
                    "content_kind", "stable_key"
                )
            ),
            [
                ("wiki", "hello-wiki"),
                ("wiki_assets", "wiki_assets"),
                ("wiki_graph", "wiki_graph"),
                ("wiki_search", "wiki_search"),
            ],
        )
        graph = SyncedDocument.objects.get(source=source, content_kind="wiki_graph")
        self.assertEqual(graph.public_path, "/-/podwiki/wiki_graph")
        self.assertEqual(graph.slug, "")
        assets = SyncedDocument.objects.get(source=source, content_kind="wiki_assets")
        self.assertEqual(list(assets.record["wiki_assets"]), ["/wiki/assets/og-default.png"])
        with self.checkout(tree) as checkout:
            for item in parser.discover(checkout, source):
                self.assertEqual(parser.upsert(item, source, media_store()).action, "unchanged")
        self.assertEqual(parser.soft_delete_missing(set(), source), 4)
        self.assertFalse(SyncedDocument.objects.filter(source=source).exists())

    def test_withdrawn_podcasts_drop_from_graph_and_search(self) -> None:
        source = _source("dtc-podwiki")
        self._entities(podcast_slug="other-episode")
        parser = get_parser("wiki")
        tree = {
            "_wiki/hello-wiki.md": _wiki_page(
                "Hello Wiki", "A section.\n\n## Section One\n\nBody text."
            ),
            "graph/graph.json": _wiki_graph(),
            "search/search-corpus.json": _wiki_search(),
            "assets/og-default.png": b"\x89PNG\r\n\x1a\npng-bytes",
        }
        with self.checkout(tree) as checkout:
            items = parser.discover(checkout, source)
        by_key = {item.key: item.data["record"] for item in items}
        graph = by_key["$wiki_graph"]
        search = by_key["$wiki_search"]
        self.assertEqual(graph["counts"], {"guides": 0, "nodes": 1, "links": 0, "podcasts": 0})
        self.assertEqual([node["id"] for node in graph["nodes"]], ["wiki:hello-wiki"])
        self.assertEqual([document["id"] for document in search["docs"]], ["hello-wiki"])

    def test_refuses_to_sync_without_the_entity_sources(self) -> None:
        source = _source("dtc-podwiki")
        parser = get_parser("wiki")
        tree = {
            "_wiki/hello-wiki.md": _wiki_page(
                "Hello Wiki", "A section.\n\n## Section One\n\nBody text."
            ),
            "graph/graph.json": _wiki_graph(),
            "search/search-corpus.json": _wiki_search(),
            "assets/og-default.png": b"\x89PNG\r\n\x1a\npng-bytes",
        }
        with self.checkout(tree) as checkout:
            with self.assertRaises(ContentParserError):
                parser.discover(checkout, source)

    def test_rejects_fragments_the_page_does_not_render(self) -> None:
        source = _source("dtc-podwiki")
        self._entities()
        parser = get_parser("wiki")
        tree = {
            "_wiki/hello-wiki.md": _wiki_page(
                "Hello Wiki", "A section.\n\n## Section One\n\nBody text."
            ),
            "graph/graph.json": _wiki_graph(),
            "search/search-corpus.json": _wiki_search(
                fragment="missing-heading", segment="Missing Heading"
            ),
            "assets/og-default.png": b"\x89PNG\r\n\x1a\npng-bytes",
        }
        with self.checkout(tree) as checkout:
            with self.assertRaises(ContentParserError):
                parser.discover(checkout, source)

    def test_ignores_other_sources(self) -> None:
        other = _source("some-other-wiki")
        parser = get_parser("wiki")
        tree = {
            "_wiki/hello-wiki.md": _wiki_page(
                "Hello Wiki", "A section.\n\n## Section One\n\nBody text."
            ),
            "graph/graph.json": _wiki_graph(),
            "search/search-corpus.json": _wiki_search(),
            "assets/og-default.png": b"\x89PNG\r\n\x1a\npng-bytes",
        }
        with self.checkout(tree) as checkout:
            self.assertEqual(parser.discover(checkout, other), [])
            self.assertEqual(parser.soft_delete_missing(set(), other), 0)


class RegistrationTests(unittest.TestCase):
    def test_site_parsers_are_registered(self) -> None:
        self.assertEqual(type(get_parser("article")).__name__, "ArticlesParser")
        self.assertEqual(type(get_parser("people")).__name__, "PeopleParser")
        self.assertEqual(type(get_parser("podcast")).__name__, "PodcastsParser")
        self.assertEqual(type(get_parser("book")).__name__, "BooksParser")
        self.assertEqual(type(get_parser("docs")).__name__, "DocsParser")
        self.assertEqual(type(get_parser("faq")).__name__, "FaqParser")
        self.assertEqual(type(get_parser("wiki")).__name__, "PodwikiParser")
