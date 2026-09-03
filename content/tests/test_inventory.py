from __future__ import annotations

from django.test import TestCase

from content.inventory import (
    checked_public_contract_inventory_sha256,
    content_route_contracts,
)
from content.models import PUBLIC_CONTRACT_DIGEST
from content.route_contracts import ContractClassification, ReviewState
from content.services import (
    ContentReadinessError,
    CreateContentRelease,
    PreparedDocument,
    PrepareDocument,
    TransitionContentRelease,
    begin_release_fetch,
    begin_release_validation,
    create_content_release,
    prepare_document,
)

from .factories import CONTEXT, make_source


class ContentInventoryTests(TestCase):
    EXPECTED = {
        "/articles.html": (
            "contract-6e9ac5e76323fa925bdf84dc",
            "dtc-main-site",
            "ee43d3fa0929faf691178d79f19528e6f15a83e5",
        ),
        "/docs/": (
            "contract-ec43f46c5ede0fb0de00248b",
            "dtc-docs",
            "3f23e006ffdaa498bbc69697408853b6f5eb37dc",
        ),
        "/faq/data-engineering-zoomcamp.html": (
            "contract-d553da2e9bf20451c28668c5",
            "dtc-faq",
            "c8da1deea9e24945922702994de101dd90a5380a",
        ),
        "/podwiki/": (
            "contract-33c686cf7bcb99d666d773c4",
            "dtc-podwiki",
            "988b79d0d655bf4755945c3118544cb9e0dbead6",
        ),
        "/people/ella%28wati%29sahnan.html": (
            "contract-67a6d2cef2d71031f427846e",
            "dtc-main-site",
            "ee43d3fa0929faf691178d79f19528e6f15a83e5",
        ),
    }

    def test_checked_inventory_retains_exact_content_base_route_provenance(self) -> None:
        contracts = content_route_contracts()
        self.assertEqual(checked_public_contract_inventory_sha256(), PUBLIC_CONTRACT_DIGEST)
        by_path = {contract.percent_encoded_public_reference: contract for contract in contracts}
        self.assertEqual(len(by_path), len(contracts))
        for path, expected in self.EXPECTED.items():
            contract = by_path[path]
            self.assertEqual(
                (contract.contract_id, contract.source_id, contract.source_revision),
                expected,
            )
            self.assertEqual(contract.classification, ContractClassification.PRESERVE)
            self.assertEqual(contract.review_state, ReviewState.PROPOSED_PRESERVE)
            self.assertFalse(contract.query)
            self.assertFalse(contract.fragment)
        self.assertTrue(all(contract.source_id != "dtc-course-platform" for contract in contracts))
        self.assertNotIn("/faq/ai-dev-tools-zoomcamp.html#09d4e6901b", by_path)
        self.assertNotIn("/podwiki/search/?q=machine+learning", by_path)
        self.assertNotIn("/Articles.html", by_path)
        self.assertNotIn("/articles.html/", by_path)
        self.assertNotIn("/people/ella(wati)sahnan.html", by_path)

    def test_preparation_binds_exact_contract_triple_and_rejects_spoofed_provenance(self) -> None:
        source = make_source(stable_id="dtc-main-site")
        source.refresh_from_db()
        release = create_content_release(
            CreateContentRelease(
                source.id,
                source.revision,
                "d" * 40,
                "parser-v1",
                "renderer-v1",
                {"mode": "inventory-fixture"},
            ),
            context=CONTEXT,
        )
        release = begin_release_fetch(
            TransitionContentRelease(release.id, release.revision), context=CONTEXT
        )
        release = begin_release_validation(
            TransitionContentRelease(release.id, release.revision), context=CONTEXT
        )
        contract_id, source_id, revision = self.EXPECTED["/articles.html"]
        document = prepare_document(
            PrepareDocument(
                release.id,
                release.revision,
                PreparedDocument(
                    content_kind="html",
                    stable_key="articles",
                    source_path="articles.md",
                    checksum="d" * 64,
                    title="Articles",
                    exact_public_path="/articles.html",
                    rendered_html="<h1>Articles</h1>",
                    is_published=True,
                    contract_id=contract_id,
                    contract_source_id=source_id,
                    contract_source_revision=revision,
                ),
            ),
            context=CONTEXT,
        )
        self.assertEqual(document.contract_id, contract_id)
        self.assertEqual(document.contract_source_revision, revision)

        release.refresh_from_db()
        with self.assertRaises(ContentReadinessError):
            prepare_document(
                PrepareDocument(
                    release.id,
                    release.revision,
                    PreparedDocument(
                        content_kind="html",
                        stable_key="spoofed",
                        source_path="spoofed.md",
                        checksum="e" * 64,
                        title="Spoofed",
                        exact_public_path="/Articles.html",
                        rendered_html="<h1>Spoofed</h1>",
                        is_published=True,
                        contract_id=contract_id,
                        contract_source_id=source_id,
                        contract_source_revision=revision,
                    ),
                ),
                context=CONTEXT,
            )
