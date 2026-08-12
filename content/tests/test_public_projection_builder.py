from __future__ import annotations

import copy
import json
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from scripts import build_public_projection as builder


class PublicProjectionBuilderTests(SimpleTestCase):
    def temporary_directory(self) -> tempfile.TemporaryDirectory[str]:
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        return tempfile.TemporaryDirectory(dir=scratch)

    def test_course_catalog_checksum_is_pinned_and_tamper_evident(self) -> None:
        source = Path(settings.BASE_DIR) / "scripts" / "production_like_course_specs.json"
        self.assertEqual(len(builder._courses(source)), 12)

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

    def test_book_archive_normalizes_ordered_threads_and_empty_replies(self) -> None:
        archive = builder._book_archive(
            [
                {
                    "name": "Reader",
                    "text": "How do I start?",
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
                    "text": "How do I start?",
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

        self.assertEqual(manifest["counts"], {"finals": 796, "aliases": 1_592})
        self.assertEqual(len(manifest["finals"]), 796)
        self.assertEqual(len(manifest["aliases"]), 1_592)
        self.assertTrue(all(item["final_path"].endswith(".html") for item in manifest["finals"]))
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
        self.assertEqual(schema["properties"]["counts"]["properties"]["finals"]["const"], 796)
        self.assertEqual(schema["properties"]["counts"]["properties"]["aliases"]["const"], 1_592)
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
