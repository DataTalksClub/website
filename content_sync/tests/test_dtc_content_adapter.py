from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import yaml
from django.test import SimpleTestCase

from content_sync.dtc_content import adapter as adapter_module
from content_sync.dtc_content import media as media_module
from content_sync.dtc_content.adapter import (
    DtcContentValidationError,
    adapt_dtc_content_checkout,
)
from content_sync.dtc_content.contract import DTC_CONTENT_CONTRACT
from content_sync.dtc_content.media import (
    MediaValidationError,
    media_type,
    validate_media_batch,
)

from .helpers import fixture_checkout, gif_bytes, jpeg_bytes, png_bytes, progressive_jpeg_bytes

FIXTURE_COMMIT = "f" * 40


def _diagnostic(root: Path, *, contract=DTC_CONTENT_CONTRACT) -> tuple[str, str]:
    try:
        adapt_dtc_content_checkout(
            root,
            commit_sha=FIXTURE_COMMIT,
            contract=contract,
        )
    except DtcContentValidationError as error:
        diagnostic = error.diagnostics[0]
        return diagnostic.code, diagnostic.source_path
    raise AssertionError("invalid fixture unexpectedly passed")


def _load_yaml(path: Path) -> dict[str, object]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_yaml(path: Path, value: object) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


class DtcContentAdapterTests(SimpleTestCase):
    def test_bounded_media_batch_keeps_path_order_and_validation_results(self) -> None:
        items = (
            ("images/z.png", png_bytes()),
            ("images/b.jpg", b"not-a-jpeg"),
            ("images/a.jpg", jpeg_bytes()),
        )
        with (
            patch("content_sync.dtc_content.media._PARALLEL_MEDIA_MIN_ITEMS", 1),
            patch("content_sync.dtc_content.media._PARALLEL_MEDIA_WORKERS", 2),
        ):
            results = validate_media_batch(items)

        self.assertEqual(
            results,
            (
                ("images/a.jpg", "image/jpeg", ""),
                ("images/b.jpg", "", "media_extension_content_mismatch"),
                ("images/z.png", "image/png", ""),
            ),
        )

    def test_media_worker_failures_are_bounded_cleaned_and_retryable(self) -> None:
        canary = "sensitive-worker-canary"
        items = (
            ("images/z.png", png_bytes()),
            ("images/b.jpg", jpeg_bytes()),
            ("images/a.jpg", jpeg_bytes()),
        )

        with (
            patch("content_sync.dtc_content.media._PARALLEL_MEDIA_MIN_ITEMS", 1),
            patch("content_sync.dtc_content.media._PARALLEL_MEDIA_WORKERS", 2),
            patch(
                "content_sync.dtc_content.media.media_type",
                side_effect=RuntimeError(canary),
            ),
        ):
            child_results = validate_media_batch(items)

        self.assertEqual(
            child_results,
            tuple(
                (path, "", "media_validation_worker_failed")
                for path in ("images/a.jpg", "images/b.jpg", "images/z.png")
            ),
        )
        self.assertNotIn(canary, repr(child_results))
        self.assertIsNone(media_module._PARALLEL_MEDIA_PAYLOAD)

        with patch(
            "content_sync.dtc_content.media.media_type",
            return_value=canary,
        ):
            malformed_result = validate_media_batch(items[:1])
        self.assertEqual(
            malformed_result,
            (("images/z.png", "", "media_validation_worker_failed"),),
        )
        self.assertNotIn(canary, repr(malformed_result))

        shutdowns: list[tuple[bool, bool]] = []

        class MapFailureExecutor:
            def map(self, *args: object, **kwargs: object) -> object:
                raise RuntimeError(canary)

            def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
                shutdowns.append((wait, cancel_futures))

        with (
            patch("content_sync.dtc_content.media._PARALLEL_MEDIA_MIN_ITEMS", 1),
            patch("content_sync.dtc_content.media.current_process") as process,
            patch("content_sync.dtc_content.media.os.process_cpu_count", return_value=2),
            patch(
                "content_sync.dtc_content.media.get_all_start_methods",
                return_value=["fork"],
            ),
            patch(
                "content_sync.dtc_content.media.ProcessPoolExecutor",
                return_value=MapFailureExecutor(),
            ),
        ):
            process.return_value.daemon = False
            map_failure = validate_media_batch(items)

        self.assertEqual(
            map_failure,
            (("images/a.jpg", "", "media_validation_worker_failed"),),
        )
        self.assertEqual(shutdowns, [(True, True)])
        self.assertNotIn(canary, repr(map_failure))
        self.assertIsNone(media_module._PARALLEL_MEDIA_PAYLOAD)

        class ResultFailureExecutor:
            def map(self, *args: object, **kwargs: object) -> object:
                def results() -> Iterator[tuple[str, str, str]]:
                    yield ("images/a.jpg", "image/jpeg", "")
                    raise RuntimeError(canary)

                return results()

            def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
                shutdowns.append((wait, cancel_futures))

        with (
            patch("content_sync.dtc_content.media._PARALLEL_MEDIA_MIN_ITEMS", 1),
            patch("content_sync.dtc_content.media.current_process") as process,
            patch("content_sync.dtc_content.media.os.process_cpu_count", return_value=2),
            patch(
                "content_sync.dtc_content.media.get_all_start_methods",
                return_value=["fork"],
            ),
            patch(
                "content_sync.dtc_content.media.ProcessPoolExecutor",
                return_value=ResultFailureExecutor(),
            ),
        ):
            process.return_value.daemon = False
            result_failure = validate_media_batch(items)

        self.assertEqual(
            result_failure,
            (("images/b.jpg", "", "media_validation_worker_failed"),),
        )
        self.assertEqual(shutdowns, [(True, True), (True, True)])
        self.assertNotIn(canary, repr(result_failure))
        self.assertIsNone(media_module._PARALLEL_MEDIA_PAYLOAD)

        class ShutdownFailureExecutor:
            def map(self, *args: object, **kwargs: object) -> object:
                return iter(
                    (
                        ("images/a.jpg", "image/jpeg", ""),
                        ("images/b.jpg", "image/jpeg", ""),
                        ("images/z.png", "image/png", ""),
                    )
                )

            def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
                raise RuntimeError(canary)

        with (
            patch("content_sync.dtc_content.media._PARALLEL_MEDIA_MIN_ITEMS", 1),
            patch("content_sync.dtc_content.media.current_process") as process,
            patch("content_sync.dtc_content.media.os.process_cpu_count", return_value=2),
            patch(
                "content_sync.dtc_content.media.get_all_start_methods",
                return_value=["fork"],
            ),
            patch(
                "content_sync.dtc_content.media.ProcessPoolExecutor",
                return_value=ShutdownFailureExecutor(),
            ),
        ):
            process.return_value.daemon = False
            shutdown_failure = validate_media_batch(items)

        self.assertEqual(
            shutdown_failure,
            (("images/a.jpg", "", "media_validation_worker_failed"),),
        )
        self.assertNotIn(canary, repr(shutdown_failure))
        self.assertIsNone(media_module._PARALLEL_MEDIA_PAYLOAD)

        for interrupt_type in (KeyboardInterrupt, SystemExit):

            class InterruptingShutdownExecutor:
                def __init__(self, exception_type: type[BaseException]) -> None:
                    self.exception_type = exception_type

                def map(self, *args: object, **kwargs: object) -> object:
                    return iter(
                        (
                            ("images/a.jpg", "image/jpeg", ""),
                            ("images/b.jpg", "image/jpeg", ""),
                            ("images/z.png", "image/png", ""),
                        )
                    )

                def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
                    raise self.exception_type(canary)

            with self.subTest(interrupt=interrupt_type.__name__):
                with (
                    patch("content_sync.dtc_content.media._PARALLEL_MEDIA_MIN_ITEMS", 1),
                    patch("content_sync.dtc_content.media.current_process") as process,
                    patch(
                        "content_sync.dtc_content.media.os.process_cpu_count",
                        return_value=2,
                    ),
                    patch(
                        "content_sync.dtc_content.media.get_all_start_methods",
                        return_value=["fork"],
                    ),
                    patch(
                        "content_sync.dtc_content.media.ProcessPoolExecutor",
                        return_value=InterruptingShutdownExecutor(interrupt_type),
                    ),
                    self.assertRaises(interrupt_type) as interrupted,
                ):
                    process.return_value.daemon = False
                    validate_media_batch(items)

                self.assertEqual(str(interrupted.exception), canary)
                self.assertIsNone(media_module._PARALLEL_MEDIA_PAYLOAD)
                self.assertEqual(
                    validate_media_batch(items),
                    (
                        ("images/a.jpg", "image/jpeg", ""),
                        ("images/b.jpg", "image/jpeg", ""),
                        ("images/z.png", "image/png", ""),
                    ),
                )

        with (
            patch("content_sync.dtc_content.media._PARALLEL_MEDIA_MIN_ITEMS", 1),
            patch("content_sync.dtc_content.media.current_process") as process,
            patch("content_sync.dtc_content.media.os.process_cpu_count", return_value=2),
            patch(
                "content_sync.dtc_content.media.get_all_start_methods",
                return_value=["fork"],
            ),
            patch(
                "content_sync.dtc_content.media.ProcessPoolExecutor",
                side_effect=RuntimeError(canary),
            ),
        ):
            process.return_value.daemon = False
            startup_failure = validate_media_batch(items)

        self.assertEqual(
            startup_failure,
            (("images/a.jpg", "", "media_validation_worker_failed"),),
        )
        self.assertNotIn(canary, repr(startup_failure))
        self.assertIsNone(media_module._PARALLEL_MEDIA_PAYLOAD)

        with fixture_checkout() as root:
            first_media = min(
                path.relative_to(root).as_posix()
                for prefix in ("posts", "podcast", "books")
                for path in (root / "images" / prefix).rglob("*")
                if path.is_file()
            )
            with (
                patch("content_sync.dtc_content.media._PARALLEL_MEDIA_MIN_ITEMS", 1),
                patch(
                    "content_sync.dtc_content.media.media_type",
                    side_effect=RuntimeError(canary),
                ),
                self.assertRaises(DtcContentValidationError) as raised,
            ):
                adapt_dtc_content_checkout(root, commit_sha=FIXTURE_COMMIT)

            diagnostic = raised.exception.diagnostics[0]
            self.assertEqual(diagnostic.code, "media_validation_worker_failed")
            self.assertEqual(diagnostic.source_path, first_media)
            self.assertNotIn(canary, str(raised.exception))
            self.assertIsNone(media_module._PARALLEL_MEDIA_PAYLOAD)

            retry = adapt_dtc_content_checkout(root, commit_sha=FIXTURE_COMMIT)

        self.assertEqual(retry.counts["media"], 7)
        self.assertIsNone(media_module._PARALLEL_MEDIA_PAYLOAD)

    def test_representative_bundle_is_lossless_deterministic_and_network_free(self) -> None:
        with (
            fixture_checkout() as root,
            patch(
                "socket.create_connection",
                side_effect=AssertionError("network call"),
            ),
        ):
            first = adapt_dtc_content_checkout(root, commit_sha=FIXTURE_COMMIT)
            second = adapt_dtc_content_checkout(root, commit_sha=FIXTURE_COMMIT)

        self.assertEqual(
            first.counts,
            {
                "articles": 1,
                "podcasts": 2,
                "podcast_transcripts": 1,
                "books": 1,
                "media": 7,
            },
        )
        self.assertEqual(first.bundle_sha256, second.bundle_sha256)
        self.assertEqual(first.documents, second.documents)
        self.assertEqual(first.relations, second.relations)
        self.assertEqual(first.assets, second.assets)
        self.assertEqual(len(first.documents), 5)
        self.assertEqual(len(first.relations), 5)

        documents = {
            (document.content_kind, document.stable_key): document for document in first.documents
        }
        article = documents[("article", "segmentation")]
        self.assertIsNotNone(article.raw_frontmatter)
        self.assertIsNotNone(article.adapter_metadata)
        assert article.raw_frontmatter is not None
        assert article.adapter_metadata is not None
        self.assertEqual(article.exact_public_path, "/blog/segmentation.html")
        self.assertEqual(article.raw_frontmatter["unknown_legacy_field"], "retained")
        self.assertIn("The raw Markdown body", article.raw_body)
        self.assertIn("content-extension", article.rendered_html)
        self.assertNotIn("{%", article.rendered_html)
        self.assertEqual(article.contract_source_id, "dtc-main-site")
        self.assertIn("/edit/main/articles/", article.edit_url)
        self.assertIn(
            f"/blob/{FIXTURE_COMMIT}/articles/",
            article.adapter_metadata["immutable_source_url"],
        )

        episode = documents[("podcast", "analytics-engineer-skills-tools")]
        transcript = documents[("podcast_transcript", "analytics-engineer-skills-tools")]
        no_transcript = documents[("podcast", "building-domestic-risk-assessment-tool")]
        self.assertEqual(episode.exact_public_path, "/podcast/analytics-engineer-skills-tools.html")
        self.assertIsNone(transcript.exact_public_path)
        self.assertFalse(transcript.is_published)
        self.assertTrue(transcript.noindex)
        self.assertEqual(transcript.canonical_url, "")
        self.assertIn('"segments"', transcript.raw_structured_data)
        self.assertNotIn('"segments"', episode.raw_structured_data)
        self.assertIn(
            '"resources":[{"title":"Example","url":"https://example.com/resource?utm_source=fixture"}]',
            episode.raw_structured_data,
        )
        self.assertNotIn('"is_external"', episode.raw_structured_data)
        self.assertNotEqual(episode.edit_url, transcript.edit_url)
        self.assertFalse(
            any(
                relation.relation_type == "transcript"
                and relation.source_key == no_transcript.stable_key
                for relation in first.relations
            )
        )

        book = documents[("book", "20201214-ml-bookcamp")]
        self.assertIn("unknown_book_field", book.raw_structured_data)
        self.assertIn("Discussion archive", book.rendered_html)
        self.assertIn("It teaches with projects", book.normalized_text)
        self.assertEqual(
            {asset.content_type for asset in first.assets},
            {"image/gif", "image/jpeg", "image/png", "image/svg+xml"},
        )
        self.assertTrue(
            all(asset.stable_public_path.startswith("/images/") for asset in first.assets)
        )

    def test_article_images_require_exact_local_media_before_sanitizing(self) -> None:
        local = (
            "/images/posts/2025-08-11-tab-1-how-to-build-blood-cell-classifier-for-"
            "cancer-prediction-case-study-from-ml-zoomcamp/image10.gif"
        )
        rejected = (
            ("remote-markdown", "![remote](https://evil.invalid/x.png)", "unsafe_image_reference"),
            ("protocol-markdown", "![remote](//evil.invalid/x.png)", "unsafe_image_reference"),
            (
                "remote-raw",
                '<img src="https://evil.invalid/x.png" alt="remote">',
                "unsafe_image_reference",
            ),
            (
                "protocol-raw",
                '<img src="//evil.invalid/x.png" alt="remote">',
                "unsafe_image_reference",
            ),
            (
                "event-handler",
                f'<img src="{local}" onerror="alert(1)" alt="local">',
                "unsafe_image_attribute",
            ),
            (
                "remote-srcset",
                f'<img src="{local}" srcset="https://evil.invalid/x.png 2x" alt="local">',
                "unsafe_image_attribute",
            ),
            (
                "remote-theme-source",
                f'<img src="{local}" data-light-src="https://evil.invalid/x.png" alt="local">',
                "unsafe_image_reference",
            ),
            (
                "remote-style-source",
                f'<img src="{local}" style="background:url(https://evil.invalid/x.png)">',
                "unsafe_image_attribute",
            ),
            (
                "escaped-style-source",
                f'<img src="{local}" style="background:u\\72l(https://evil.invalid/x.png)">',
                "unsafe_image_attribute",
            ),
            (
                "commented-style-source",
                f'<img src="{local}" style="background:u/**/rl(https://evil.invalid/x.png)">',
                "unsafe_image_attribute",
            ),
            (
                "duplicate-source",
                f'<img src="{local}" src="{local}" alt="local">',
                "unsafe_image_attribute",
            ),
            ("missing-source", '<img alt="missing">', "unsafe_image_reference"),
        )
        for label, payload, expected in rejected:
            with self.subTest(label=label), fixture_checkout() as root:
                article = root / "articles" / "2020-11-29-segmentation.md"
                article.write_text(
                    article.read_text(encoding="utf-8") + f"\n{payload}\n",
                    encoding="utf-8",
                )
                code, source_path = _diagnostic(root)
                self.assertEqual(code, expected)
                self.assertEqual(source_path, "articles/2020-11-29-segmentation.md")

        with fixture_checkout() as root:
            article = root / "articles" / "2020-11-29-segmentation.md"
            article.write_text(
                article.read_text(encoding="utf-8")
                + f'\n![local]({local})\n<img src="{local}" alt="local" loading="lazy">\n',
                encoding="utf-8",
            )
            bundle = adapt_dtc_content_checkout(root, commit_sha=FIXTURE_COMMIT)
            document = next(item for item in bundle.documents if item.content_kind == "article")
            self.assertEqual(document.rendered_html.count(f'src="{local}"'), 2)

    def test_preflight_reuses_each_parsed_content_document(self) -> None:
        calls: list[str] = []
        original = adapter_module._load_yaml_mapping

        def recording_loader(
            text: str,
            *,
            path: str,
            contract=DTC_CONTENT_CONTRACT,
        ):
            calls.append(path)
            return original(text, path=path, contract=contract)

        with (
            fixture_checkout() as root,
            patch.object(
                adapter_module,
                "_load_yaml_mapping",
                side_effect=recording_loader,
            ),
        ):
            adapt_dtc_content_checkout(root, commit_sha=FIXTURE_COMMIT)

        content_calls = [
            path for path in calls if path.startswith(("articles/", "podcasts/", "books/"))
        ]
        self.assertEqual(len(content_calls), 5)
        self.assertTrue(all(count == 1 for count in Counter(content_calls).values()))

    def test_c_safe_yaml_preflight_preserves_alias_depth_and_node_limits(self) -> None:
        cases = (
            ("alias", "root: &value [one]\ncopy: *value\n", DTC_CONTENT_CONTRACT),
            (
                "depth",
                "root:\n  child:\n    leaf: value\n",
                replace(DTC_CONTENT_CONTRACT, max_yaml_depth=2),
            ),
            (
                "nodes",
                "first: one\nsecond: two\n",
                replace(DTC_CONTENT_CONTRACT, max_yaml_nodes=4),
            ),
        )
        for label, raw, contract in cases:
            with self.subTest(label=label), self.assertRaises(DtcContentValidationError) as raised:
                adapter_module._load_yaml_mapping(raw, path="fixture.yaml", contract=contract)
            self.assertEqual(raised.exception.diagnostics[0].code, "invalid_or_unsafe_yaml")
            self.assertEqual(raised.exception.diagnostics[0].source_path, "fixture.yaml")

    def test_podcast_description_rejects_all_seven_invalid_forms_without_fallback(self) -> None:
        invalid_values = (
            ("missing", None, True),
            ("blank", "   ", False),
            ("null", None, False),
            ("numeric", 123, False),
            ("boolean", True, False),
            ("sequence", ["not", "scalar"], False),
            ("mapping", {"not": "scalar"}, False),
        )
        for label, replacement, remove in invalid_values:
            with self.subTest(label=label), fixture_checkout() as root:
                path = root / "podcasts" / "analytics-engineer-skills-tools.yaml"
                value = _load_yaml(path)
                if remove:
                    value.pop("description")
                else:
                    value["description"] = replacement
                _write_yaml(path, value)
                self.assertEqual(_diagnostic(root)[0], "description_required")

    def test_podcast_numbers_require_native_positive_integers(self) -> None:
        invalid_values = (
            ("missing", None, True),
            ("null", None, False),
            ("boolean", True, False),
            ("string", "3", False),
            ("float", 3.0, False),
            ("zero", 0, False),
            ("negative", -1, False),
        )
        for field in ("season", "episode"):
            for label, replacement, remove in invalid_values:
                with self.subTest(field=field, label=label), fixture_checkout() as root:
                    path = root / "podcasts" / "analytics-engineer-skills-tools.yaml"
                    value = _load_yaml(path)
                    if remove:
                        value.pop(field)
                    else:
                        value[field] = replacement
                    _write_yaml(path, value)
                    self.assertEqual(_diagnostic(root)[0], f"podcast_{field}_invalid")

    def test_article_frontmatter_body_and_unknown_metadata_are_byte_bound(self) -> None:
        with fixture_checkout() as root:
            path = root / "articles" / "2020-11-29-segmentation.md"
            expected_checksum = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
            bundle = adapt_dtc_content_checkout(root, commit_sha=FIXTURE_COMMIT)
        article = next(
            document for document in bundle.documents if document.content_kind == "article"
        )
        self.assertIsNotNone(article.raw_frontmatter)
        assert article.raw_frontmatter is not None
        self.assertEqual(article.checksum, expected_checksum)
        self.assertTrue(article.raw_body.startswith("\n## Article body"))
        self.assertEqual(article.raw_frontmatter["unknown_legacy_field"], "retained")

    def test_transcript_validation_failures_are_path_specific_and_stable(self) -> None:
        cases: list[tuple[str, Callable[[Path], None], str]] = []

        def inline(root: Path) -> None:
            path = root / "podcasts" / "analytics-engineer-skills-tools.yaml"
            value = _load_yaml(path)
            value["transcript"] = []
            _write_yaml(path, value)

        cases.append(("inline", inline, "inline_transcript_not_allowed"))

        def escaping(root: Path) -> None:
            path = root / "podcasts" / "analytics-engineer-skills-tools.yaml"
            value = _load_yaml(path)
            value["transcript"] = "../outside.yaml"
            _write_yaml(path, value)

        cases.append(("escaping", escaping, "transcript_reference_outside_directory"))

        def missing(root: Path) -> None:
            (root / "podcasts" / "transcripts" / "analytics-engineer-skills-tools.yaml").unlink()

        cases.append(("missing", missing, "referenced_transcript_missing"))

        def orphan(root: Path) -> None:
            path = root / "podcasts" / "transcripts" / "orphan.yaml"
            _write_yaml(path, {"podcast": "orphan", "segments": [{"header": "orphan"}]})

        cases.append(("orphan", orphan, "orphan_transcript"))

        def mismatch(root: Path) -> None:
            path = root / "podcasts" / "transcripts" / "analytics-engineer-skills-tools.yaml"
            value = _load_yaml(path)
            value["podcast"] = "wrong"
            _write_yaml(path, value)

        cases.append(("mismatch", mismatch, "transcript_podcast_mismatch"))

        def duplicate(root: Path) -> None:
            episode = root / "podcasts" / "building-domestic-risk-assessment-tool.yaml"
            value = _load_yaml(episode)
            value["transcript"] = "transcripts/analytics-engineer-skills-tools.yaml"
            _write_yaml(episode, value)

        cases.append(("duplicate", duplicate, "duplicate_transcript_reference"))

        for name, mutate, expected in cases:
            with self.subTest(name=name), fixture_checkout() as root:
                mutate(root)
                first = _diagnostic(root)
                second = _diagnostic(root)
                self.assertEqual(first, second)
                self.assertEqual(first[0], expected)
                self.assertNotIn(str(root), first[1])

    def test_malformed_structures_and_unsafe_content_fail_closed(self) -> None:
        cases: list[tuple[str, Callable[[Path], None], str]] = []

        def invalid_yaml(root: Path) -> None:
            (root / "podcasts" / "analytics-engineer-skills-tools.yaml").write_text(
                "title: [unterminated",
                encoding="utf-8",
            )

        cases.append(("invalid-yaml", invalid_yaml, "invalid_or_unsafe_yaml"))

        def empty_article(root: Path) -> None:
            path = root / "articles" / "2020-11-29-segmentation.md"
            path.write_text(
                path.read_text(encoding="utf-8").split("---", 2)[0] + "---\n", encoding="utf-8"
            )

        cases.append(("empty-article", empty_article, "article_frontmatter_unclosed"))

        def bad_archive(root: Path) -> None:
            path = root / "books" / "20201214-ml-bookcamp.yaml"
            value = _load_yaml(path)
            value["archive"] = {"not": "ordered"}
            _write_yaml(path, value)

        cases.append(("book-archive", bad_archive, "book_archive_list_required"))

        def unsafe_url(root: Path) -> None:
            path = root / "podcasts" / "analytics-engineer-skills-tools.yaml"
            value = _load_yaml(path)
            value["links"] = {"unsafe": "javascript:alert(1)"}
            _write_yaml(path, value)

        cases.append(("unsafe-url", unsafe_url, "unsafe_url"))

        def unsafe_html(root: Path) -> None:
            path = root / "articles" / "2020-11-29-segmentation.md"
            path.write_text(
                path.read_text(encoding="utf-8") + "\n<script>alert(1)</script>\n", encoding="utf-8"
            )

        cases.append(("unsafe-html", unsafe_html, "unsafe_rendered_html"))

        def yaml_alias(root: Path) -> None:
            path = root / "podcasts" / "analytics-engineer-skills-tools.yaml"
            path.write_text(
                path.read_text(encoding="utf-8") + "\naliased: &value [one]\ncopy: *value\n",
                encoding="utf-8",
            )

        cases.append(("yaml-alias", yaml_alias, "invalid_or_unsafe_yaml"))

        def unsafe_svg(root: Path) -> None:
            path = root / "images" / "podcast" / "badges" / "spotify.svg"
            path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
                encoding="utf-8",
            )

        cases.append(("unsafe-svg", unsafe_svg, "unsafe_svg"))

        def podcast_slug_too_long(root: Path) -> None:
            path = root / "podcasts" / "analytics-engineer-skills-tools.yaml"
            value = _load_yaml(path)
            slug = "a" * (adapter_module._PODCAST_SLUG_MAX_LENGTH + 1)
            value["slug"] = slug
            value["legacy_path"] = f"/podcast/{slug}.html"
            _write_yaml(path, value)
            path.rename(path.with_name(f"{slug}.yaml"))

        cases.append(("podcast-slug-too-long", podcast_slug_too_long, "podcast_slug_too_long"))

        def book_slug_too_long(root: Path) -> None:
            path = root / "books" / "20201214-ml-bookcamp.yaml"
            value = _load_yaml(path)
            slug = "b" * (adapter_module._BOOK_SLUG_MAX_LENGTH + 1)
            value["slug"] = slug
            value["legacy_path"] = f"/books/{slug}.html"
            _write_yaml(path, value)
            path.rename(path.with_name(f"{slug}.yaml"))

        cases.append(("book-slug-too-long", book_slug_too_long, "book_slug_too_long"))

        def book_title_too_long(root: Path) -> None:
            path = root / "books" / "20201214-ml-bookcamp.yaml"
            value = _load_yaml(path)
            value["title"] = "c" * (adapter_module._BOOK_TITLE_MAX_LENGTH + 1)
            _write_yaml(path, value)

        cases.append(("book-title-too-long", book_title_too_long, "book_title_too_long"))

        for name, mutate, expected in cases:
            with self.subTest(name=name), fixture_checkout() as root:
                mutate(root)
                self.assertEqual(_diagnostic(root)[0], expected)

    def test_slug_and_title_at_length_limit_still_pass(self) -> None:
        podcast_slug = "a" * adapter_module._PODCAST_SLUG_MAX_LENGTH
        book_slug = "b" * adapter_module._BOOK_SLUG_MAX_LENGTH
        book_title = "c" * adapter_module._BOOK_TITLE_MAX_LENGTH
        podcast_public_path = f"/podcast/{podcast_slug}.html"
        book_public_path = f"/books/{book_slug}.html"

        with fixture_checkout() as root:
            podcast_path = root / "podcasts" / "analytics-engineer-skills-tools.yaml"
            podcast_value = _load_yaml(podcast_path)
            podcast_value["slug"] = podcast_slug
            podcast_value["legacy_path"] = podcast_public_path
            _write_yaml(podcast_path, podcast_value)
            podcast_path.rename(podcast_path.with_name(f"{podcast_slug}.yaml"))

            transcript_path = (
                root / "podcasts" / "transcripts" / "analytics-engineer-skills-tools.yaml"
            )
            transcript_value = _load_yaml(transcript_path)
            transcript_value["podcast"] = podcast_slug
            _write_yaml(transcript_path, transcript_value)

            book_path = root / "books" / "20201214-ml-bookcamp.yaml"
            book_value = _load_yaml(book_path)
            book_value["slug"] = book_slug
            book_value["legacy_path"] = book_public_path
            book_value["title"] = book_title
            _write_yaml(book_path, book_value)
            book_path.rename(book_path.with_name(f"{book_slug}.yaml"))

            # These synthetic slugs are not part of the real adopted-path set the
            # production public projection exposes, so the routes are marked
            # adopted here purely to isolate the length guard from the unrelated
            # legacy-route contract check.
            original_checked_contracts = adapter_module._checked_contracts

            def patched_checked_contracts():
                index, digest, adopted, approved = original_checked_contracts()
                return index, digest, adopted | {podcast_public_path, book_public_path}, approved

            with patch.object(
                adapter_module,
                "_checked_contracts",
                side_effect=patched_checked_contracts,
            ):
                bundle = adapt_dtc_content_checkout(root, commit_sha=FIXTURE_COMMIT)

        self.assertEqual(bundle.counts["podcasts"], 2)
        self.assertEqual(bundle.counts["books"], 1)
        documents = {
            (document.content_kind, document.stable_key): document for document in bundle.documents
        }
        self.assertEqual(
            documents[("podcast", podcast_slug)].exact_public_path, podcast_public_path
        )
        self.assertEqual(documents[("book", book_slug)].exact_public_path, book_public_path)

    def test_paths_assets_limits_and_media_tampering_fail_closed(self) -> None:
        with fixture_checkout() as root:
            missing = root / "images" / "books" / "20201214-ml-bookcamp" / "preview.jpg"
            missing.unlink()
            self.assertEqual(_diagnostic(root)[0], "referenced_asset_missing")

        with fixture_checkout() as root:
            target = root / "images" / "books" / "20201214-ml-bookcamp" / "cover.jpg"
            target.unlink()
            os.symlink("preview.jpg", target)
            self.assertEqual(_diagnostic(root)[0], "symlink_not_allowed")

        with fixture_checkout() as root:
            path = (
                root
                / "images"
                / "podcast"
                / "open-source-turned-into-career-and-startup-creation.jpg"
            )
            path.write_bytes(b"not-a-jpeg")
            self.assertEqual(_diagnostic(root)[0], "media_extension_content_mismatch")

        with fixture_checkout() as root:
            (root / "books" / "unsupported.md").write_text("unsupported", encoding="utf-8")
            self.assertEqual(_diagnostic(root)[0], "unsupported_content_path")

        with fixture_checkout() as root:
            contract = replace(DTC_CONTENT_CONTRACT, max_files=1)
            self.assertEqual(
                _diagnostic(root, contract=contract)[0], "source_file_count_limit_exceeded"
            )

        with fixture_checkout() as root:
            contract = replace(DTC_CONTENT_CONTRACT, max_source_bytes=1)
            self.assertEqual(_diagnostic(root, contract=contract)[0], "source_byte_limit_exceeded")

    def test_raster_headers_truncation_and_polyglots_fail_closed(self) -> None:
        gif_path = (
            "images/posts/2025-08-11-tab-1-how-to-build-blood-cell-classifier-for-"
            "cancer-prediction-case-study-from-ml-zoomcamp/image10.gif"
        )
        cases = (
            ("images/books/20201214-ml-bookcamp/cover.jpg", b"\xff\xd8\xff\xd9"),
            (
                "images/posts/2022-10-02-naming-variables-in-machine-learning/image1.png",
                b"\x89PNG\r\n\x1a\n",
            ),
            (gif_path, b"GIF89a"),
            (
                "images/books/20201214-ml-bookcamp/cover.jpg",
                b"\xff\xd8\xff<script>alert(1)</script>\xff\xd9",
            ),
            (
                "images/posts/2022-10-02-naming-variables-in-machine-learning/image1.png",
                b"\x89PNG\r\n\x1a\n<script>alert(1)</script>",
            ),
            (gif_path, b"GIF89a<script>alert(1)</script>"),
            ("images/books/20201214-ml-bookcamp/cover.jpg", jpeg_bytes() + b"<script/>"),
            (
                "images/posts/2022-10-02-naming-variables-in-machine-learning/image1.png",
                png_bytes() + b"<script/>",
            ),
            (gif_path, gif_bytes() + b"<script/>"),
        )
        for relative, payload in cases:
            with self.subTest(relative=relative, size=len(payload)), fixture_checkout() as root:
                (root / relative).write_bytes(payload)
                self.assertEqual(_diagnostic(root)[0], "media_extension_content_mismatch")

    def test_jpeg_entropy_replaced_by_script_bytes_fails_closed(self) -> None:
        payload = jpeg_bytes()[:-3] + b"<script>alert(1)</script>" + b"\xff\xd9"
        with fixture_checkout() as root:
            path = root / "images" / "books" / "20201214-ml-bookcamp" / "cover.jpg"
            path.write_bytes(payload)
            self.assertEqual(_diagnostic(root)[0], "media_extension_content_mismatch")

    def test_jpeg_frame_precision_is_explicit_for_baseline_and_progressive_lanes(self) -> None:
        baseline = jpeg_bytes()
        self.assertEqual(media_type(baseline, path="fixture.jpg"), "image/jpeg")
        self.assertEqual(
            media_type(progressive_jpeg_bytes(), path="fixture.jpg"),
            "image/jpeg",
        )

        sof0 = baseline.index(b"\xff\xc0")
        for precision in (9, 12, 16):
            invalid_baseline = bytearray(baseline)
            invalid_baseline[sof0 + 4] = precision
            with self.subTest(marker="SOF0", precision=precision):
                with self.assertRaises(MediaValidationError) as raised:
                    media_type(bytes(invalid_baseline), path="fixture.jpg")
                self.assertEqual(raised.exception.code, "media_extension_content_mismatch")

            with self.subTest(marker="SOF2", precision=precision):
                with self.assertRaises(MediaValidationError) as raised:
                    media_type(progressive_jpeg_bytes(precision=precision), path="fixture.jpg")
                self.assertEqual(raised.exception.code, "media_extension_content_mismatch")

    def test_svg_active_content_and_external_reference_bypasses_fail_closed(self) -> None:
        external_cases = (
            "<style>@import url(https://evil.invalid/a.css);</style>",
            "<style>.x { fill: url(https://evil.invalid/a.svg#x); }</style>",
            "<style>@font-face { src: url(https://evil.invalid/a.woff); }</style>",
            '<?XmL-StYlEsHeEt href="https://evil.invalid/a.css"?>',
            '<image href="https://evil.invalid/a.png"/>',
            '<image xmlns:xlink="http://www.w3.org/1999/xlink" '
            'xlink:href="&#104;ttps://evil.invalid/a.png"/>',
            '<image href="  https://evil.invalid/a.png"/>',
            '<g xml:base="https://evil.invalid/"><use href="#local"/></g>',
            '<rect style="fill:u/**/rl(https://evil.invalid/a.svg#x)"/>',
            '<rect style="fill:u\\72l(https://evil.invalid/a.svg#x)"/>',
            '<rect style="@\\69mport url(https://evil.invalid/a.css)"/>',
        )
        for fragment in external_cases:
            with self.subTest(fragment=fragment), fixture_checkout() as root:
                path = root / "images" / "podcast" / "badges" / "spotify.svg"
                if fragment.lstrip().lower().startswith("<?xml-stylesheet"):
                    payload = fragment + '<svg xmlns="http://www.w3.org/2000/svg"/>'
                else:
                    payload = f'<svg xmlns="http://www.w3.org/2000/svg">{fragment}</svg>'
                path.write_text(payload, encoding="utf-8")
                self.assertEqual(_diagnostic(root)[0], "unsafe_svg_external_reference")

        unsafe_cases = (
            '<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            '<svg xmlns="http://www.w3.org/2000/svg">&xxe;</svg>',
            '<svg xmlns="http://www.w3.org/2000/svg"><ScRiPt>alert(1)</ScRiPt></svg>',
            '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',
            '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject/></svg>',
            '<svg xmlns="http://www.w3.org/2000/svg"><animate attributeName="href"/></svg>',
        )
        for payload in unsafe_cases:
            with self.subTest(payload=payload), fixture_checkout() as root:
                path = root / "images" / "podcast" / "badges" / "spotify.svg"
                path.write_text(payload, encoding="utf-8")
                self.assertEqual(_diagnostic(root)[0], "unsafe_svg")

    def test_svg_presentation_attributes_normalize_css_before_url_validation(self) -> None:
        presentation_attributes = (
            "clip-path",
            "color-profile",
            "cursor",
            "fill",
            "filter",
            "marker",
            "marker-end",
            "marker-mid",
            "marker-start",
            "mask",
            "stroke",
        )
        disguised_urls = (
            r"u\72l(https://evil.invalid/a.svg#x)",
            "u/**/rl(https://evil.invalid/a.svg#x)",
        )
        for attribute in presentation_attributes:
            for disguised_url in disguised_urls:
                with (
                    self.subTest(attribute=attribute, value=disguised_url),
                    fixture_checkout() as root,
                ):
                    path = root / "images" / "podcast" / "badges" / "spotify.svg"
                    path.write_text(
                        '<svg xmlns="http://www.w3.org/2000/svg">'
                        f'<rect {attribute}="{disguised_url}" width="1" height="1"/>'
                        "</svg>",
                        encoding="utf-8",
                    )
                    self.assertEqual(
                        _diagnostic(root)[0],
                        "unsafe_svg_external_reference",
                    )

    def test_migration_tamper_time_limit_and_commit_shape_fail_closed(self) -> None:
        with fixture_checkout() as root:
            migration = root / "migration.yaml"
            migration.write_text(
                migration.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8"
            )
            self.assertEqual(_diagnostic(root)[0], "migration_provenance_tampered")

        with fixture_checkout() as root:
            ticks = iter((0.0, 61.0, 61.0, 61.0, 61.0))
            with self.assertRaises(DtcContentValidationError) as raised:
                adapt_dtc_content_checkout(
                    root, commit_sha=FIXTURE_COMMIT, clock=lambda: next(ticks)
                )
            self.assertEqual(
                raised.exception.diagnostics[0].code, "source_validation_time_limit_exceeded"
            )

        with fixture_checkout() as root, self.assertRaises(DtcContentValidationError) as raised:
            adapt_dtc_content_checkout(root, commit_sha="main")
        self.assertEqual(raised.exception.diagnostics[0].code, "source_commit_invalid")
