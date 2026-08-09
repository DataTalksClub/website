from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict, fields
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from content.queries import (
    ResolvePublicAsset,
    ResolvePublicDocument,
    resolve_public_asset,
    resolve_public_document,
)

from .factories import CONTEXT, activate, make_ready_release, make_source


class PublicContentQueryTests(TestCase):
    def setUp(self) -> None:
        self.source = make_source()
        self.release = activate(
            self.source,
            make_ready_release(self.source, commit_character="a"),
        )

    def test_document_and_asset_resolve_with_exactly_one_query(self) -> None:
        with CaptureQueriesContext(connection) as captured:
            document = resolve_public_document(
                ResolvePublicDocument("/Fixture/Exact.html"), context=CONTEXT
            )
        self.assertEqual(len(captured), 1)
        self.assertIsNotNone(document)
        assert document is not None
        self.assertEqual(document.title, "Fixture release v1")
        self.assertNotIn("raw_body", asdict(document))
        self.assertNotIn("raw_frontmatter", asdict(document))
        self.assertNotIn("normalized_text", asdict(document))
        self.assertNotIn("structured_errors", asdict(document))
        self.assertEqual(
            {field.name for field in fields(document)},
            {
                "content_kind",
                "stable_key",
                "exact_public_path",
                "slug",
                "title",
                "summary",
                "canonical_url",
                "seo_title",
                "seo_description",
                "seo_image_url",
                "rendered_html",
                "noindex",
                "edit_url",
            },
        )
        with self.assertRaises(FrozenInstanceError):
            document.title = "mutated"  # type: ignore[misc]

        with CaptureQueriesContext(connection) as captured:
            asset = resolve_public_asset(
                ResolvePublicAsset("/assets/Fixture-Logo.svg"), context=CONTEXT
            )
        self.assertEqual(len(captured), 1)
        self.assertIsNotNone(asset)
        assert asset is not None
        self.assertTrue(asset.storage_key.endswith("/logo.svg"))

    def test_exact_case_slash_publish_and_noindex_contract(self) -> None:
        for path in (
            "/fixture/Exact.html",
            "/Fixture/Exact.html/",
            "/Fixture/Exact.html?preview=1",
            "/Fixture/Exact.html#heading",
            "/Fixture/Exact\x7f.html",
            "/unknown.html",
        ):
            self.assertIsNone(resolve_public_document(ResolvePublicDocument(path), context=CONTEXT))
        document = resolve_public_document(
            ResolvePublicDocument("/Fixture/Exact.html"), context=CONTEXT
        )
        assert document is not None
        self.assertFalse(document.noindex)
        for path in (
            "/assets/fixture-Logo.svg",
            "/assets/Fixture-Logo.svg/",
            "/assets/Fixture-Logo.svg?v=1",
            "/assets/Fixture-Logo.svg#icon",
            "/assets/Fixture-Logo\x7f.svg",
        ):
            self.assertIsNone(resolve_public_asset(ResolvePublicAsset(path), context=CONTEXT))

    def test_published_noindex_resolves_and_unpublished_does_not(self) -> None:
        noindex_source = make_source()
        activate(
            noindex_source,
            make_ready_release(
                noindex_source,
                commit_character="b",
                public_path="/noindex.html",
                asset_path="/assets/noindex.svg",
                heading="Noindex fixture",
                noindex=True,
            ),
        )
        noindex = resolve_public_document(ResolvePublicDocument("/noindex.html"), context=CONTEXT)
        assert noindex is not None
        self.assertTrue(noindex.noindex)

        private_source = make_source()
        activate(
            private_source,
            make_ready_release(
                private_source,
                commit_character="c",
                public_path="/private.html",
                asset_path="/assets/private.svg",
                heading="Private fixture",
                is_published=False,
            ),
        )
        self.assertIsNone(
            resolve_public_document(ResolvePublicDocument("/private.html"), context=CONTEXT)
        )

    def test_resolver_does_not_call_network_parser_sanitizer_or_write_paths(self) -> None:
        with (
            patch("requests.sessions.Session.request", side_effect=AssertionError("network")),
            patch("mistune.create_markdown", side_effect=AssertionError("parser")),
            patch("bleach.clean", side_effect=AssertionError("sanitizer")),
            patch("django.db.models.query.QuerySet.create", side_effect=AssertionError("write")),
            patch("django.db.models.query.QuerySet.update", side_effect=AssertionError("write")),
            patch("django.db.models.query.QuerySet.delete", side_effect=AssertionError("write")),
        ):
            self.assertIsNotNone(
                resolve_public_document(
                    ResolvePublicDocument("/Fixture/Exact.html"), context=CONTEXT
                )
            )
            self.assertIsNotNone(
                resolve_public_asset(
                    ResolvePublicAsset("/assets/Fixture-Logo.svg"), context=CONTEXT
                )
            )

    def test_ready_candidate_and_disabled_source_are_not_public(self) -> None:
        candidate = make_ready_release(
            self.source,
            commit_character="b",
            heading="Fixture release v2",
            marker="commit-v2",
        )
        document = resolve_public_document(
            ResolvePublicDocument("/Fixture/Exact.html"), context=CONTEXT
        )
        assert document is not None
        self.assertEqual(document.title, "Fixture release v1")
        self.assertEqual(candidate.status, "ready")

        self.source.refresh_from_db()
        self.source.enabled = False
        self.source.revision += 1
        self.source.save(update_fields=("enabled", "revision", "updated_at"))
        self.assertIsNone(
            resolve_public_document(ResolvePublicDocument("/Fixture/Exact.html"), context=CONTEXT)
        )
