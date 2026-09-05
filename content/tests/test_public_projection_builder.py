from __future__ import annotations

import copy
import json
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import TestCase

from content.public_text import (
    strip_leaked_target_attributes,
    strip_target_attributes_from_links,
)
from scripts import build_public_projection as builder


class PublicProjectionBuilderTests(TestCase):
    def temporary_directory(self) -> tempfile.TemporaryDirectory[str]:
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        return tempfile.TemporaryDirectory(dir=scratch)

    def test_course_catalog_checksum_is_pinned_and_tamper_evident(self) -> None:
        source = Path(settings.BASE_DIR) / "scripts" / "production_like_course_specs.json"

        with self.temporary_directory() as directory:
            changed = Path(directory) / "course-specs.json"
            shutil.copy2(source, changed)
            changed.write_bytes(changed.read_bytes() + b"\n")
            with self.assertRaisesRegex(
                builder.ProjectionBuildError,
                "checksum mismatch",
            ):
                builder._courses(changed)

    def test_unsafe_urls_and_oversize_strings_fail_closed(self) -> None:
        for value in ("javascript:alert(1)", "data:text/html,bad", "//example.com/path"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(builder.ProjectionBuildError, "unsafe public URL"):
                    builder._safe_url(value, field="test", optional=False)
        with self.assertRaisesRegex(builder.ProjectionBuildError, "invalid public field"):
            builder._string("x" * 101, field="test", maximum=100)

    def test_podcast_resources_keep_only_bounded_https_destinations_in_source_order(self) -> None:
        resources = builder._podcast_resources(
            [
                {"title": "First", "url": "https://example.test/first"},
                {"title": "Historical HTTP link", "url": "http://example.test/old"},
                {"title": "Second", "url": "https://example.test/second"},
            ],
            source_name="episode.yaml",
        )
        self.assertEqual(
            resources,
            [
                {
                    "title": "First",
                    "url": "https://example.test/first",
                    "is_external": True,
                    "target": "_blank",
                    "rel": "noopener noreferrer",
                },
                {
                    "title": "Second",
                    "url": "https://example.test/second",
                    "is_external": True,
                    "target": "_blank",
                    "rel": "noopener noreferrer",
                },
            ],
        )
        self.assertEqual(
            builder._podcast_resources(
                [
                    {
                        "title": "Related episode",
                        "url": "https://www.datatalks.club/podcast/example.html",
                    }
                ],
                source_name="episode.yaml",
                podcast_records=[
                    {
                        "slug": "example",
                        "season": 1,
                        "episode": 1,
                        "public_path": "/podcast/example.html",
                    }
                ],
            ),
            [
                {
                    "title": "Related episode",
                    "url": "/podcast/s01e01/example",
                    "is_external": False,
                    "target": "",
                    "rel": "",
                }
            ],
        )
        self.assertEqual(
            builder._podcast_resources(
                [{"title": "Related episode", "url": "/podcast/s01e01-example.html"}],
                source_name="episode.yaml",
                podcast_records=[
                    {
                        "slug": "example",
                        "season": 1,
                        "episode": 1,
                        "public_path": "/podcast/example.html",
                    }
                ],
            )[0]["url"],
            "/podcast/s01e01/example",
        )
        self.assertEqual(
            builder._podcast_resources(
                [
                    {
                        "title": "Related episode",
                        "url": "http://www.datatalks.club/podcast/s01e01-example.html",
                    }
                ],
                source_name="episode.yaml",
                podcast_records=[
                    {
                        "slug": "example",
                        "season": 1,
                        "episode": 1,
                        "public_path": "/podcast/example.html",
                    }
                ],
            )[0]["url"],
            "/podcast/s01e01/example",
        )
        with self.assertRaisesRegex(builder.ProjectionBuildError, "unsafe public URL"):
            builder._podcast_resources(
                [{"title": "Unsafe", "url": "javascript:alert(1)"}],
                source_name="episode.yaml",
            )

    def test_podcast_video_requires_a_source_identity_that_matches_the_watch_link(self) -> None:
        raw = {"ids": {"youtube": "Video_1"}}
        links = {"youtube": "https://www.youtube.com/watch?v=Video_1"}
        self.assertEqual(
            builder._podcast_video(raw, links, source_name="episode.yaml"),
            {"provider": "youtube", "id": "Video_1"},
        )
        self.assertIsNone(builder._podcast_video({}, links, source_name="episode.yaml"))
        self.assertIsNone(
            builder._podcast_video(
                {"ids": {"youtube": "bad id"}},
                links,
                source_name="episode.yaml",
            )
        )
        with self.assertRaisesRegex(builder.ProjectionBuildError, "identity mismatch"):
            builder._podcast_video(
                {"ids": {"youtube": "Other_1"}},
                links,
                source_name="episode.yaml",
            )

    def test_podcast_numbers_require_positive_integer_source_values(self) -> None:
        self.assertEqual(builder._positive_integer(1, field="podcast season"), 1)
        for value in (None, False, True, "1", 0, -1, 1.0):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    builder.ProjectionBuildError,
                    "invalid positive integer: podcast season",
                ):
                    builder._positive_integer(value, field="podcast season")

    def test_unsafe_svg_is_rejected_before_it_can_be_projected(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory) / "source"
            media = root / "images" / "posts" / "unsafe.svg"
            media.parent.mkdir(parents=True)
            media.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(builder.ProjectionBuildError, "unsafe SVG"):
                builder._copy_media(root, Path(directory) / "output", mode="preferred")

    def test_plain_body_projection_does_not_execute_source_markup(self) -> None:
        blocks = builder._body_blocks(
            "## Safe heading\n\n<script>alert(1)</script> **Visible text** {{ secret }}"
        )
        self.assertEqual(blocks[0]["id"], "safe-heading")
        self.assertNotIn("<script", str(blocks))
        self.assertNotIn("secret", str(blocks))
        self.assertIn("Visible text", str(blocks))

    def test_wiki_body_preserves_local_links_without_widening_plain_bios(self) -> None:
        source = "Read [the guide](https://www.datatalks.club/blog/guide.html?from=wiki#setup)."

        self.assertEqual(
            builder._body_blocks(source, preserve_links=True),
            [
                {
                    "kind": "paragraph",
                    "text": "Read the guide.",
                    "markdown": "Read [the guide](/blog/guide.html?from=wiki#setup).",
                }
            ],
        )
        self.assertEqual(
            builder._body_blocks(source),
            [{"kind": "paragraph", "text": "Read the guide."}],
        )

    def test_wiki_graph_localizes_host_variants_and_preserves_url_suffixes(self) -> None:
        payload = {
            "nodes": [
                {"url": ("http://www.datatalks.club/podcast/episode.html?from=graph#transcript")},
                {"url": "/wiki/topic/?from=search#section"},
            ]
        }

        localized = builder._canonicalize_wiki_document_urls(
            payload,
            {"episode": "/podcast/s24e01/episode"},
            {},
            {},
        )

        self.assertEqual(
            localized["nodes"][0]["url"],
            "/podcast/s24e01/episode?from=graph#transcript",
        )
        self.assertEqual(
            localized["nodes"][1]["url"],
            "/wiki/topic?from=search#section",
        )

    def test_target_attribute_grammar_is_narrow_and_preserves_link_content(self) -> None:
        self.assertEqual(
            strip_target_attributes_from_links(
                '[label](https://example.test/path){:target = "blank"}, surrounding prose'
            ),
            "[label](https://example.test/path), surrounding prose",
        )
        self.assertEqual(
            strip_target_attributes_from_links(
                "[label](https://example.test/path){:target=&quot;blank&quot;}."
            ),
            "[label](https://example.test/path).",
        )
        self.assertEqual(
            builder._plain_inline(
                '[label](https://example.test/path){:target="blank"}, surrounding prose'
            ),
            "label, surrounding prose",
        )

        unsupported = (
            '[label](https://example.test/path){:target="_blank"}',
            "[label](https://example.test/path){:target=blank}",
            "[label](https://example.test/path){:target='blank'}",
            '[label](https://example.test/path){:target="blank" class="external"}',
            "[label](https://example.test/path){#external}",
            'literal {:target="blank"}',
        )
        for value in unsupported:
            with self.subTest(value=value):
                self.assertEqual(strip_target_attributes_from_links(value), value)

        code = '`[label](https://example.test/path){:target="blank"}`'
        self.assertEqual(strip_target_attributes_from_links(code), code)
        self.assertEqual(strip_leaked_target_attributes(code), code)
        self.assertEqual(
            strip_leaked_target_attributes('literal {:target="blank"}'),
            'literal {:target="blank"}',
        )
        self.assertEqual(
            strip_leaked_target_attributes('literal{:target="blank"}'),
            'literal{:target="blank"}',
        )
        self.assertEqual(
            strip_leaked_target_attributes(
                'label{:target="blank"}',
                validated_projection=True,
            ),
            "label",
        )
        self.assertEqual(
            strip_leaked_target_attributes("label{:target=&quot;blank&quot;}"),
            "label{:target=&quot;blank&quot;}",
        )
        self.assertEqual(
            strip_leaked_target_attributes(
                "label{:target=&quot;blank&quot;}",
                validated_projection=True,
            ),
            "label",
        )

    def test_editorial_links_to_our_pages_stay_on_the_active_host(self) -> None:
        source = (
            "[Survey](https://datatalks.club/blog/survey.html?year=2026#roles) "
            "[Home](http://www.datatalks.club) "
            '<a class="cta" href="https://DATATALKS.CLUB/events/42#video">Event</a> '
            "![Image](https://datatalks.club/images/chart.png) "
            "[CDN](https://static.datatalks.club/chart.svg) "
            "[External](https://example.com/path)"
        )

        localized = builder._localize_editorial_links(source)

        self.assertIn("[Survey](/blog/survey.html?year=2026#roles)", localized)
        self.assertIn("[Home](/)", localized)
        self.assertIn('href="/events/42#video"', localized)
        self.assertIn("![Image](https://datatalks.club/images/chart.png)", localized)
        self.assertIn("https://static.datatalks.club/chart.svg", localized)
        self.assertIn("https://example.com/path", localized)
        self.assertEqual(
            builder._localize_internal_url("https://datatalks.club:invalid/blog/post"),
            "https://datatalks.club:invalid/blog/post",
        )

    def test_reviewed_sponsor_canvases_bridge_to_local_accessible_images(self) -> None:
        block = builder._article_chart_block(
            {
                "data-title": "Roles",
                "data-type": "pie",
            },
            (
                "Data engineering 28.5%, data science and ML 26.8%, analytics 16.8%, "
                "software development 13.1%, management and consulting 7.0%, other 7.8%."
            ),
            {},
        )

        self.assertEqual(block["kind"], "chart")
        self.assertEqual(block["src"], "/static/content/article-charts/sponsor-roles.svg")
        self.assertEqual(block["alt"], "Pie chart of DataTalks.Club community roles")
        self.assertEqual((block["width"], block["height"]), (640, 400))

        unknown = builder._article_chart_block(
            {"data-title": "A different chart"}, "Its source caption.", {}
        )
        self.assertEqual(
            unknown,
            {
                "kind": "chart",
                "text": "A different chart",
                "caption": "Its source caption.",
            },
        )

    def test_book_archive_normalizes_ordered_threads_and_empty_replies(self) -> None:
        archive = builder._book_archive(
            [
                {
                    "name": "Reader",
                    "text": (
                        "How do I start? Read [the archive]"
                        "(https://datatalks.club/books.html#archive)."
                    ),
                    "replies": [
                        {"name": "Author", "text": "Begin with chapter one."},
                        {"name": "Reader", "text": ""},
                    ],
                }
            ],
            source_name="book.yaml",
        )
        self.assertEqual(
            archive,
            [
                {
                    "name": "Reader",
                    "text": "How do I start? Read [the archive](/books.html#archive).",
                    "replies": [
                        {"name": "Author", "text": "Begin with chapter one."},
                        {"name": "Reader", "text": ""},
                    ],
                }
            ],
        )

    def test_book_archive_rejects_unbounded_shapes(self) -> None:
        with self.assertRaisesRegex(builder.ProjectionBuildError, "book archive rejected"):
            builder._book_archive({"name": "Reader"}, source_name="book.yaml")
        with self.assertRaisesRegex(builder.ProjectionBuildError, "book archive reply rejected"):
            builder._book_archive(
                [{"name": "Reader", "text": "Question", "replies": ["bad"]}],
                source_name="book.yaml",
            )

    def test_podcast_event_lineage_uses_only_exact_recording_identity(self) -> None:
        podcasts = [
            {
                "slug": "canonical-youtube",
                "links": {"youtube": "https://www.youtube.com/watch?v=ExactVideo1"},
            },
            {
                "slug": "canonical-audio",
                "links": {"anchor": "https://podcasters.example/episodes/exact-recording"},
            },
        ]
        events = [
            {
                "slug": "youtube-lineage",
                "title": "A deliberately different title",
                "type": "podcast",
                "links": [{"label": "Watch recording", "url": "https://youtu.be/ExactVideo1"}],
            },
            {
                "slug": "audio-lineage",
                "title": "Another title",
                "type": "podcast",
                "links": [
                    {
                        "label": "Listen to recording",
                        "url": "https://podcasters.example/episodes/exact-recording",
                    }
                ],
            },
            {
                "slug": "same-title-is-not-identity",
                "title": "A deliberately different title",
                "type": "podcast",
                "links": [{"label": "Watch recording", "url": "https://youtu.be/OtherVideo01"}],
            },
            {
                "slug": "non-podcast-is-not-lineage",
                "title": "A deliberately different title",
                "type": "webinar",
                "links": [{"label": "Watch recording", "url": "https://youtu.be/ExactVideo1"}],
            },
            {
                "slug": "non-recording-link-is-not-lineage",
                "title": "A deliberately different title",
                "type": "podcast",
                "links": [
                    {"label": "Open external event page", "url": "https://youtu.be/ExactVideo1"}
                ],
            },
        ]

        self.assertEqual(
            builder._podcast_event_lineage(podcasts, events),
            {
                "audio-lineage": "canonical-audio",
                "youtube-lineage": "canonical-youtube",
            },
        )

    def editorial_route_fixture(
        self,
    ) -> tuple[dict[str, list[dict]], dict[str, str], dict]:
        root = Path(settings.BASE_DIR) / "content" / "public_projection"
        collections = {
            name: json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
            for name in builder.EDITORIAL_ROUTE_COLLECTIONS
        }
        source_artifacts = {
            f"{name}.json": builder._sha256_file(root / f"{name}.json")
            for name in builder.EDITORIAL_ROUTE_COLLECTIONS
        }
        manifest = builder._build_editorial_route_manifest(
            collections,
            selection_mode="preferred",
            source_artifact_digests=source_artifacts,
        )
        return collections, source_artifacts, manifest

    @staticmethod
    def refresh_manifest_digest(manifest: dict) -> None:
        manifest["content_sha256"] = builder._editorial_route_manifest_digest(manifest)

    def test_editorial_route_manifest_is_exhaustive_and_schema_bound(self) -> None:
        collections, source_artifacts, manifest = self.editorial_route_fixture()

        self.assertEqual(
            {
                item["final_path"]
                for item in manifest["finals"]
                if not item["final_path"].endswith(".html")
            },
            {
                "/podcast/s24e04/from-genai-pilots-to-production",
                "/podcast/s24e05/ai-adoption-in-enterprise-beyond-writing-code",
                "/podcast/s24e06/how-to-build-ai-that-actually-ships-in-production",
            },
        )
        self.assertTrue(
            all(not item["source_path"].endswith(".html") for item in manifest["aliases"])
        )
        self.assertEqual(
            manifest["content_sha256"],
            builder._editorial_route_manifest_digest(manifest),
        )
        schema_path = Path(settings.BASE_DIR) / manifest["schema"]["path"]
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(manifest["schema"]["sha256"], builder._sha256_file(schema_path))
        builder._validate_editorial_route_manifest(
            manifest,
            collections,
            selection_mode="preferred",
            source_artifact_digests=source_artifacts,
        )

    def test_editorial_route_manifest_rejects_omissions_and_digest_drift(self) -> None:
        collections, source_artifacts, manifest = self.editorial_route_fixture()
        omitted = copy.deepcopy(manifest)
        omitted["aliases"].pop()
        self.refresh_manifest_digest(omitted)
        with self.assertRaisesRegex(builder.ProjectionBuildError, "alias count mismatch"):
            builder._validate_editorial_route_manifest(
                omitted,
                collections,
                selection_mode="preferred",
                source_artifact_digests=source_artifacts,
            )

        changed = copy.deepcopy(manifest)
        changed["content_sha256"] = "0" * 64
        with self.assertRaisesRegex(builder.ProjectionBuildError, "content digest mismatch"):
            builder._validate_editorial_route_manifest(
                changed,
                collections,
                selection_mode="preferred",
                source_artifact_digests=source_artifacts,
            )

    def test_editorial_route_manifest_rejects_collisions_chains_and_loops(self) -> None:
        collections, source_artifacts, manifest = self.editorial_route_fixture()
        cases: list[tuple[str, dict, str]] = []

        collision = copy.deepcopy(manifest)
        collision["aliases"][0]["source_path"] = collision["finals"][0]["final_path"]
        cases.append(("collision", collision, "route collision"))

        chain = copy.deepcopy(manifest)
        chain["aliases"][0]["final_path"] = chain["aliases"][1]["source_path"]
        cases.append(("chain", chain, "redirect chain"))

        loop = copy.deepcopy(manifest)
        loop["aliases"][0]["final_path"] = loop["aliases"][1]["source_path"]
        loop["aliases"][1]["final_path"] = loop["aliases"][0]["source_path"]
        cases.append(("loop", loop, "redirect loop"))

        for label, changed, error in cases:
            with self.subTest(label=label):
                self.refresh_manifest_digest(changed)
                with self.assertRaisesRegex(builder.ProjectionBuildError, error):
                    builder._validate_editorial_route_manifest(
                        changed,
                        collections,
                        selection_mode="preferred",
                        source_artifact_digests=source_artifacts,
                    )


class ArticleBodyProjectionTests(TestCase):
    """The article body builder, which is what stopped flattening a body to prose."""

    def temporary_directory(self) -> tempfile.TemporaryDirectory[str]:
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        return tempfile.TemporaryDirectory(dir=scratch)

    def blocks(self, body: str, *, media_root: Path | None = None) -> list[dict]:
        return builder._article_blocks(body, media_root=media_root, counters={})

    def test_a_fenced_sample_stays_a_sample_instead_of_becoming_headings(self) -> None:
        """The old plain-text builder read `# comment` inside code as an `<h1>`."""

        blocks = self.blocks("```python\n# load the frame\n- not a list item\nprint(1)\n```")

        self.assertEqual(
            blocks,
            [
                {
                    "kind": "code",
                    "text": "# load the frame\n- not a list item\nprint(1)",
                    "language": "python",
                }
            ],
        )

    def test_a_numbered_run_is_marked_as_ordered(self) -> None:
        blocks = self.blocks("1. first\n2. second\n\n- bullet")

        self.assertEqual(
            [(block["kind"], block["text"], block.get("ordered", False)) for block in blocks],
            [
                ("list_item", "first", True),
                ("list_item", "second", True),
                ("list_item", "bullet", False),
            ],
        )

    def test_a_link_keeps_its_address_and_loses_the_legacy_directive(self) -> None:
        blocks = self.blocks('Read [the guide](https://example.test/g){:target="_blank"} today.')

        self.assertEqual(blocks[0]["text"], "Read the guide today.")
        self.assertEqual(blocks[0]["markdown"], "Read [the guide](https://example.test/g) today.")

    def test_a_table_becomes_rows_a_page_can_draw(self) -> None:
        blocks = self.blocks("| Tool | Use |\n|------|-----|\n| dbt | **transform** |")

        self.assertEqual(
            blocks,
            [
                {
                    "kind": "table",
                    "label": "Table 1",
                    "head": ["Tool", "Use"],
                    "rows": [["dbt", "**transform**"]],
                }
            ],
        )

    def test_an_illustration_carries_its_own_size_and_its_caption(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            picture = root / "images" / "posts" / "demo" / "chart.png"
            picture.parent.mkdir(parents=True)
            # A 1x1 PNG, written byte by byte so the header is the real thing.
            picture.write_bytes(
                bytes.fromhex(
                    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
                    "01f15c4890000000a49444154789c6300010000050001"
                    "0d0a2db40000000049454e44ae426082"
                )
            )
            body = (
                "<figure>\n"
                '<img src="/images/posts/demo/chart.png" alt="A described chart" />\n'
                "<figcaption><p>What it shows</p></figcaption>\n"
                "</figure>"
            )

            blocks = self.blocks(body, media_root=root)

        self.assertEqual(
            blocks,
            [
                {
                    "kind": "image",
                    "src": "/images/posts/demo/chart.png",
                    "alt": "A described chart",
                    "caption": "What it shows",
                    "width": 1,
                    "height": 1,
                }
            ],
        )

    def test_an_off_site_or_missing_illustration_never_reaches_a_block(self) -> None:
        with self.temporary_directory() as directory:
            remote = self.blocks(
                '<img src="https://example.test/a.png" alt="remote" />', media_root=Path(directory)
            )
            self.assertEqual(remote, [])
            with self.assertRaisesRegex(builder.ProjectionBuildError, "missing from the pinned"):
                self.blocks(
                    '<img src="/images/posts/demo/absent.png" alt="gone" />',
                    media_root=Path(directory),
                )
        with self.assertRaisesRegex(builder.ProjectionBuildError, "image path rejected"):
            self.blocks('<img src="/images/posts/../secret.png" alt="escape" />')

    def test_executable_or_embedding_markup_stops_the_build(self) -> None:
        """A checked artifact does not carry a script, a frame, or a handler."""

        for source in (
            "<script>alert(1)</script> text",
            '<iframe src="https://example.test/"></iframe>',
            '<img src="/images/posts/demo/a.png" onerror="alert(1)" alt="x">',
            '<a href="javascript:alert(1)">click</a>',
        ):
            with self.subTest(source=source):
                with self.assertRaisesRegex(builder.ProjectionBuildError, "disallowed markup"):
                    self.blocks(source)

    def test_the_liquid_this_site_cannot_run_is_removed(self) -> None:
        blocks = self.blocks("**Visible text** {% include faq-accordion.html %}")

        self.assertNotIn("faq-accordion", str(blocks))
        self.assertIn("Visible text", str(blocks))

    def test_a_heading_the_source_named_keeps_that_name(self) -> None:
        """The article's own table of contents links to the identifier it wrote."""

        blocks = self.blocks('<h2 id="ml-zoomcamp">1. ML Zoomcamp</h2>\n\n## ML Zoomcamp')

        self.assertEqual(
            [(block["id"], block["text"]) for block in blocks],
            [("ml-zoomcamp", "1. ML Zoomcamp"), ("ml-zoomcamp-2", "ML Zoomcamp")],
        )
