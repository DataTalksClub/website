from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connections
from django.test import TransactionTestCase

from content.models import ContentAsset, ContentDocument, ContentRelation, ContentRelease
from content.services import create_content_source
from content_sync.dtc_content.adapter import DtcContentValidationError
from content_sync.dtc_content.contract import DTC_CONTENT_CONTRACT
from content_sync.dtc_content.preparation import (
    PreparedCandidateResult,
    prepare_dtc_content_candidate,
)
from core.models import AuditEvent, RevisionConflict
from core.services import ServiceContext

from .helpers import fixture_checkout, verify_fixture_checkout

MATERIAL_DATABASE = os.getenv("DTC_CONTENT_MATERIAL_DATABASE") == "1"
CONTEXT = ServiceContext(
    request_id="issue-103-material-database",
    correlation_id="issue-103-material-database",
    actor_ref="user:issue-103-tests",
)


@skipUnless(MATERIAL_DATABASE, "material database coverage is opt-in")
class DtcContentMaterialDatabaseTests(TransactionTestCase):
    reset_sequences = True

    def _source(self):
        return create_content_source(
            DTC_CONTENT_CONTRACT.create_source_command(enabled=True),
            context=CONTEXT,
        )

    def _contend(self, source, verified_checkouts):
        barrier = Barrier(len(verified_checkouts))

        def prepare(verified):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    return prepare_dtc_content_candidate(
                        source_id=source.id,
                        expected_source_revision=source.revision,
                        verified_checkout=verified,
                        commit_sha=verified.commit_sha,
                        person_resolver=lambda key: f"/people/{key}.html",
                        context=CONTEXT,
                    )
                except Exception as error:  # The caller asserts the bounded domain result.
                    return error
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=len(verified_checkouts)) as pool:
            futures = [pool.submit(prepare, verified) for verified in verified_checkouts]
            return tuple(future.result(timeout=30) for future in futures)

    def _assert_one_complete_release(self) -> None:
        self.assertEqual(ContentRelease.objects.count(), 1)
        self.assertEqual(ContentDocument.objects.count(), 5)
        self.assertEqual(ContentRelation.objects.count(), 5)
        self.assertEqual(ContentAsset.objects.count(), 7)
        self.assertEqual(
            AuditEvent.objects.filter(action="content.release.create").count(),
            1,
        )

    def test_concurrent_identical_preparation_has_one_complete_winner(self) -> None:
        source = self._source()
        with fixture_checkout() as root:
            verified = verify_fixture_checkout(root, salt="material-identical")

        results = self._contend(source, (verified, verified))

        self.assertTrue(all(isinstance(result, PreparedCandidateResult) for result in results))
        prepared = [result for result in results if isinstance(result, PreparedCandidateResult)]
        self.assertEqual(sum(not result.replayed for result in prepared), 1)
        self.assertEqual(sum(result.replayed for result in prepared), 1)
        self.assertEqual(len({result.release.id for result in prepared}), 1)
        self._assert_one_complete_release()

    def test_same_identity_different_bundle_has_no_partial_loser(self) -> None:
        source = self._source()
        with fixture_checkout() as first_root:
            first = verify_fixture_checkout(first_root, salt="material-evidence")
        with fixture_checkout() as second_root:
            article = second_root / "articles" / "2020-11-29-segmentation.md"
            article.write_text(
                article.read_text(encoding="utf-8").replace(
                    "Fixture segmentation article",
                    "Fixture segmentation article with different evidence",
                ),
                encoding="utf-8",
            )
            second_source = verify_fixture_checkout(second_root, salt="material-different")
        different_bundle = replace(
            second_source.bundle,
            commit_sha=first.commit_sha,
            source_tree_sha=first.tree_sha,
        )
        second = replace(
            second_source,
            commit_sha=first.commit_sha,
            tree_sha=first.tree_sha,
            bundle=different_bundle,
        )

        results = self._contend(source, (first, second))

        winners = [result for result in results if isinstance(result, PreparedCandidateResult)]
        losers = [result for result in results if not isinstance(result, PreparedCandidateResult)]
        self.assertEqual(len(winners), 1, results)
        self.assertEqual(len(losers), 1, results)
        self.assertIsInstance(losers[0], (RevisionConflict, DtcContentValidationError))
        self._assert_one_complete_release()
