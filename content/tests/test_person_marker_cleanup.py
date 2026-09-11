"""Person biographies render however many legacy tokens the release carries.

The request path once refused the whole people collection unless the corpus
held exactly the accepted bootstrap's ten legacy target-metadata tokens (audit
ARC-03): cleaning one biography, or publishing a valid smaller corpus, turned
every person page into a server error.  The frozen corpus-wide count now lives
in the reviewed-projection build contract; rendering just cleans each block.
"""

from __future__ import annotations

from typing import Any

from django.test import TestCase

from content import catalogue
from content.models import expected_storage_prefix
from content.services import (
    CreateContentRelease,
    MarkReleaseReady,
    PrepareAsset,
    PreparedAsset,
    PreparedDocument,
    PrepareDocument,
    TransitionContentRelease,
    asset_manifest_checksum_for,
    begin_release_fetch,
    begin_release_validation,
    create_content_release,
    mark_release_ready,
    prepare_asset,
    prepare_document,
)
from content.tests.factories import CONTEXT, activate, make_source

_LEGACY_TOKEN = 'Read the docs {: target="blank"} today.'
_CLEAN_TEXT = "A biography with no legacy tokens."


def _person(slug: str, title: str, text: str) -> dict[str, Any]:
    return {
        "slug": slug,
        "title": title,
        "blocks": [{"kind": "paragraph", "text": text}],
    }


def _release_with_people(source, character: str, people: list[dict[str, Any]]):
    """A ready release carrying one ``people`` document per given person."""

    source.refresh_from_db()
    release = create_content_release(
        CreateContentRelease(
            source_id=source.id,
            expected_source_revision=source.revision,
            commit_sha=character * 40,
            parser_version="fixture-parser-v1",
            rendering_version="fixture-renderer-v1",
            request_provenance={"mode": "fixture"},
        ),
        context=CONTEXT,
    )
    release = begin_release_fetch(
        TransitionContentRelease(release.id, release.revision), context=CONTEXT
    )
    release = begin_release_validation(
        TransitionContentRelease(release.id, release.revision), context=CONTEXT
    )
    for position, person in enumerate(people):
        # Every mutation bumps the release revision; re-read before each write.
        release.refresh_from_db()
        prepare_document(
            PrepareDocument(
                release.id,
                release.revision,
                PreparedDocument(
                    content_kind="people",
                    stable_key=person["slug"],
                    source_path=f"people/{person['slug']}.md",
                    checksum=character * 64,
                    exact_public_path=f"/people/{person['slug']}/",
                    slug=person["slug"],
                    title=person["title"],
                    summary="",
                    canonical_url=f"https://datatalks.club/people/{person['slug']}/",
                    seo_title=person["title"],
                    seo_description="",
                    raw_frontmatter={},
                    raw_body="",
                    rendered_html=f"<p>{person['blocks'][0]['text']}</p>",
                    adapter_metadata={"position": position, "record": person},
                    is_published=True,
                    noindex=False,
                    edit_url="https://github.com/DataTalksClub/website/edit/main/people.md",
                ),
            ),
            context=CONTEXT,
        )
    release.refresh_from_db()
    prepare_asset(
        PrepareAsset(
            release.id,
            release.revision,
            PreparedAsset(
                source_path="fixtures/logo.svg",
                stable_public_path="/assets/fixture-logo.svg",
                storage_key=(f"{expected_storage_prefix(source.stable_id, release.id)}logo.svg"),
                content_type="image/svg+xml",
                size=128,
                checksum=character * 64,
            ),
        ),
        context=CONTEXT,
    )
    release.refresh_from_db()
    return mark_release_ready(
        MarkReleaseReady(
            release_id=release.id,
            expected_revision=release.revision,
            asset_manifest_checksum=asset_manifest_checksum_for(release.id),
        ),
        context=CONTEXT,
    )


class PersonMarkerCleanupTests(TestCase):
    def setUp(self) -> None:
        catalogue._records.cache_clear()
        catalogue._cleaned_bodies.cache_clear()

    def test_a_corpus_with_any_number_of_legacy_tokens_renders(self) -> None:
        source = make_source()
        release = _release_with_people(
            source,
            character="a",
            people=[
                _person("clean-person", "Clean Person", _CLEAN_TEXT),
                _person("legacy-person", "Legacy Person", _LEGACY_TOKEN),
            ],
        )
        activate(source, release)

        cleaned = catalogue._cleaned_bodies(str(release.id), "people")

        self.assertEqual(len(cleaned), 2)
        by_slug = {record["slug"]: record for record in cleaned}
        self.assertEqual(
            by_slug["clean-person"]["blocks"][0]["text"],
            _CLEAN_TEXT,
        )
        self.assertNotIn(
            'target="blank"',
            by_slug["legacy-person"]["blocks"][0]["text"],
        )
