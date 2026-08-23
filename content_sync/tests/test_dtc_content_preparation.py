from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from content.models import (
    LEGACY_PUBLIC_CONTRACT_DIGEST,
    ActiveContentPath,
    ContentAsset,
    ContentDocument,
    ContentRelation,
    ContentRelease,
)
from content.services import (
    ActivateContentRelease,
    ContentReadinessError,
    CreateContentRelease,
    CreateContentSource,
    PrepareAsset,
    PreparedAsset,
    PreparedDocument,
    PrepareDocument,
    RollbackContentRelease,
    TransitionContentRelease,
    activate_content_release,
    begin_release_fetch,
    begin_release_validation,
    create_content_release,
    create_content_source,
    prepare_asset,
    prepare_document,
    rollback_content_release,
)
from content_sync.dtc_content.adapter import DtcContentValidationError
from content_sync.dtc_content.contract import DTC_CONTENT_CONTRACT
from content_sync.dtc_content.preparation import prepare_dtc_content_candidate
from content_sync.dtc_content.repository import verify_dtc_content_checkout
from core.models import AuditEvent, RevisionConflict
from core.services import ServiceContext

from .helpers import fixture_checkout, jpeg_bytes, verify_fixture_checkout

CONTEXT = ServiceContext(
    request_id="issue-103-tests",
    correlation_id="issue-103-tests",
    actor_ref="user:issue-103-tests",
)


def _resolve_person(key: str) -> str:
    return f"/people/{key.replace(' ', '-').lower()}.html"


class DtcContentPreparationTests(TestCase):
    def _source(self, *, enabled: bool = True):
        return create_content_source(
            DTC_CONTENT_CONTRACT.create_source_command(enabled=enabled),
            context=CONTEXT,
        )

    def _prepare(self, source, root: Path, character: str):
        source.refresh_from_db()
        verified = verify_fixture_checkout(root, salt=character)
        return prepare_dtc_content_candidate(
            source_id=source.id,
            expected_source_revision=source.revision,
            verified_checkout=verified,
            commit_sha=verified.commit_sha,
            person_resolver=_resolve_person,
            context=CONTEXT,
        )

    def test_valid_candidate_is_ready_complete_and_idempotent_on_sqlite(self) -> None:
        source = self._source()
        with (
            fixture_checkout() as root,
            patch.dict(
                os.environ,
                {"DATABASE_URL": "postgresql://unusable.invalid/example"},
            ),
        ):
            first = self._prepare(source, root, "a")
            source.refresh_from_db()
            audit_count = AuditEvent.objects.count()
            second = self._prepare(source, root, "a")

        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(first.release.id, second.release.id)
        self.assertEqual(first.release.status, ContentRelease.Status.READY)
        self.assertEqual(first.release.document_count, 5)
        self.assertEqual(first.release.relation_count, 5)
        self.assertEqual(first.release.asset_count, 7)
        self.assertEqual(ContentRelease.objects.count(), 1)
        self.assertEqual(ContentDocument.objects.count(), 5)
        self.assertEqual(ContentRelation.objects.count(), 5)
        self.assertEqual(ContentAsset.objects.count(), 7)
        self.assertEqual(AuditEvent.objects.count(), audit_count)
        transcript = ContentDocument.objects.get(content_kind="podcast_transcript")
        self.assertIsNone(transcript.exact_public_path)
        self.assertFalse(transcript.is_published)
        self.assertIn('"segments"', transcript.raw_structured_data)
        relation = ContentRelation.objects.get(relation_type="transcript")
        self.assertEqual(relation.resolved_target_document_id, transcript.id)

    def test_activation_change_set_and_explicit_rollback_swap_one_complete_release(self) -> None:
        source = self._source()
        with fixture_checkout() as first_root:
            first = self._prepare(source, first_root, "a")
        source.refresh_from_db()
        first.release.refresh_from_db()
        activate_content_release(
            ActivateContentRelease(
                source.id,
                first.release.id,
                source.revision,
                first.release.revision,
                "fixture v1",
            ),
            context=CONTEXT,
        )

        with fixture_checkout() as second_root:
            article = second_root / "articles" / "2020-11-29-segmentation.md"
            article.write_text(
                article.read_text(encoding="utf-8").replace(
                    "Fixture segmentation article",
                    "Fixture segmentation article v2",
                ),
                encoding="utf-8",
            )
            podcast = second_root / "podcasts" / "analytics-engineer-skills-tools.yaml"
            podcast.write_text(
                podcast.read_text(encoding="utf-8").replace(
                    "Fixture podcast with transcript",
                    "Fixture podcast with transcript v2",
                ),
                encoding="utf-8",
            )
            transcript = (
                second_root / "podcasts" / "transcripts" / "analytics-engineer-skills-tools.yaml"
            )
            transcript.write_text(
                transcript.read_text(encoding="utf-8").replace(
                    "Welcome to the fixture episode.",
                    "Welcome to the fixture episode v2.",
                ),
                encoding="utf-8",
            )
            book = second_root / "books" / "20201214-ml-bookcamp.yaml"
            book.write_text(
                book.read_text(encoding="utf-8").replace(
                    "Fixture Machine Learning Bookcamp",
                    "Fixture Machine Learning Bookcamp v2",
                ),
                encoding="utf-8",
            )
            media = second_root / "images" / "books" / "20201214-ml-bookcamp" / "cover.jpg"
            media.write_bytes(jpeg_bytes(comment=b"fixture-book-cover-v2"))
            second = self._prepare(source, second_root, "b")

        source.refresh_from_db()
        second.release.refresh_from_db()
        activate_content_release(
            ActivateContentRelease(
                source.id,
                second.release.id,
                source.revision,
                second.release.revision,
                "fixture v2",
            ),
            context=CONTEXT,
        )
        source.refresh_from_db()
        self.assertEqual(source.active_release_id, second.release.id)
        self.assertEqual(
            set(ActiveContentPath.objects.values_list("release_id", flat=True)),
            {second.release.id},
        )
        self.assertEqual(
            ContentAsset.objects.filter(
                release=second.release,
                storage_key__startswith=(f"content/dtc-content/{second.release.id}/"),
            ).count(),
            7,
        )
        first.release.refresh_from_db()
        rollback_content_release(
            RollbackContentRelease(
                source.id,
                first.release.id,
                source.revision,
                first.release.revision,
                "verified fixture rollback",
            ),
            context=CONTEXT,
        )
        source.refresh_from_db()
        self.assertEqual(source.active_release_id, first.release.id)
        self.assertEqual(
            set(ActiveContentPath.objects.values_list("release_id", flat=True)),
            {first.release.id},
        )
        self.assertEqual(
            ContentDocument.objects.filter(release=first.release).count(),
            5,
        )
        self.assertEqual(ContentAsset.objects.filter(release=first.release).count(), 7)

    def test_preparation_failure_and_unresolved_identity_leave_no_partial_candidate(self) -> None:
        source = self._source()
        with fixture_checkout() as root:
            active = self._prepare(source, root, "a")
        source.refresh_from_db()
        active.release.refresh_from_db()
        activate_content_release(
            ActivateContentRelease(
                source.id,
                active.release.id,
                source.revision,
                active.release.revision,
                "fixture active",
            ),
            context=CONTEXT,
        )
        source.refresh_from_db()
        before = {
            "source_revision": source.revision,
            "release_count": ContentRelease.objects.count(),
            "document_count": ContentDocument.objects.count(),
            "asset_count": ContentAsset.objects.count(),
            "relation_count": ContentRelation.objects.count(),
            "audit_count": AuditEvent.objects.count(),
            "claim_count": ActiveContentPath.objects.count(),
        }

        def fail_during_documents(phase: str, index: int) -> None:
            if phase == "document" and index == 1:
                raise RuntimeError("injected preparation failure")

        with fixture_checkout() as root:
            verified = verify_fixture_checkout(root, salt="b")
            with self.assertRaisesRegex(RuntimeError, "injected preparation failure"):
                prepare_dtc_content_candidate(
                    source_id=source.id,
                    expected_source_revision=source.revision,
                    verified_checkout=verified,
                    commit_sha=verified.commit_sha,
                    person_resolver=_resolve_person,
                    context=CONTEXT,
                    preparation_probe=fail_during_documents,
                )
        source.refresh_from_db()
        self.assertEqual(source.active_release_id, active.release.id)
        self.assertEqual(source.revision, before["source_revision"])
        self.assertEqual(ContentRelease.objects.count(), before["release_count"])
        self.assertEqual(ContentDocument.objects.count(), before["document_count"])
        self.assertEqual(ContentAsset.objects.count(), before["asset_count"])
        self.assertEqual(ContentRelation.objects.count(), before["relation_count"])
        self.assertEqual(AuditEvent.objects.count(), before["audit_count"])
        self.assertEqual(ActiveContentPath.objects.count(), before["claim_count"])

        with fixture_checkout() as root:
            verified = verify_fixture_checkout(root, salt="c")
            with self.assertRaises(DtcContentValidationError) as raised:
                prepare_dtc_content_candidate(
                    source_id=source.id,
                    expected_source_revision=source.revision,
                    verified_checkout=verified,
                    commit_sha=verified.commit_sha,
                    person_resolver=lambda key: None,
                    context=CONTEXT,
                )
        self.assertEqual(
            raised.exception.diagnostics[0].code,
            "required_person_unresolved",
        )
        self.assertEqual(ContentRelease.objects.count(), before["release_count"])

    def test_checkout_media_rejection_precedes_every_database_and_audit_write(self) -> None:
        self._source()
        before = (
            ContentRelease.objects.count(),
            ContentDocument.objects.count(),
            ContentRelation.objects.count(),
            ContentAsset.objects.count(),
            AuditEvent.objects.count(),
        )
        with fixture_checkout() as root:
            verified = verify_fixture_checkout(root, salt="malformed-media")
            media = root / "images" / "books" / "20201214-ml-bookcamp" / "cover.jpg"
            media.write_bytes(b"\xff\xd8\xff\xd9")
            subprocess.run(
                ("git", "-C", str(root), "add", "images/books/20201214-ml-bookcamp/cover.jpg"),
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ("git", "-C", str(root), "commit", "-m", "Malformed media"),
                check=True,
                capture_output=True,
                env={
                    **os.environ,
                    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                    "GIT_AUTHOR_NAME": "Fixture Author",
                    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
                    "GIT_COMMITTER_NAME": "Fixture Author",
                },
            )
            commit = subprocess.run(
                ("git", "-C", str(root), "rev-parse", "HEAD"),
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertNotEqual(commit, verified.commit_sha)
            with self.assertRaises(DtcContentValidationError) as raised:
                verify_dtc_content_checkout(root, expected_commit=commit)
            self.assertEqual(
                raised.exception.diagnostics[0].code,
                "media_extension_content_mismatch",
            )
        self.assertEqual(
            (
                ContentRelease.objects.count(),
                ContentDocument.objects.count(),
                ContentRelation.objects.count(),
                ContentAsset.objects.count(),
                AuditEvent.objects.count(),
            ),
            before,
        )

    def test_relation_asset_readiness_and_audit_failures_roll_back_every_write(self) -> None:
        source = self._source()

        for salt, failed_phase in (("r", "relation"), ("s", "asset"), ("t", "ready")):
            before = (
                source.revision,
                ContentRelease.objects.count(),
                ContentDocument.objects.count(),
                ContentRelation.objects.count(),
                ContentAsset.objects.count(),
                AuditEvent.objects.count(),
            )

            def probe(phase: str, index: int, _failed_phase: str = failed_phase) -> None:
                if phase == _failed_phase and index == 0:
                    raise RuntimeError(f"injected {_failed_phase} failure")

            with fixture_checkout() as root:
                verified = verify_fixture_checkout(root, salt=salt)
                with self.assertRaisesRegex(RuntimeError, f"injected {failed_phase} failure"):
                    prepare_dtc_content_candidate(
                        source_id=source.id,
                        expected_source_revision=source.revision,
                        verified_checkout=verified,
                        commit_sha=verified.commit_sha,
                        person_resolver=_resolve_person,
                        context=CONTEXT,
                        preparation_probe=probe,
                    )
            source.refresh_from_db()
            self.assertEqual(
                (
                    source.revision,
                    ContentRelease.objects.count(),
                    ContentDocument.objects.count(),
                    ContentRelation.objects.count(),
                    ContentAsset.objects.count(),
                    AuditEvent.objects.count(),
                ),
                before,
            )

        with fixture_checkout() as root:
            verified = verify_fixture_checkout(root, salt="u")
            audit_before = (
                source.revision,
                ContentRelease.objects.count(),
                AuditEvent.objects.count(),
            )
            with (
                patch(
                    "content.services.record_audit_event", side_effect=RuntimeError("audit fail")
                ),
                self.assertRaisesRegex(RuntimeError, "audit fail"),
            ):
                prepare_dtc_content_candidate(
                    source_id=source.id,
                    expected_source_revision=source.revision,
                    verified_checkout=verified,
                    commit_sha=verified.commit_sha,
                    person_resolver=_resolve_person,
                    context=CONTEXT,
                )
        source.refresh_from_db()
        self.assertEqual(
            (source.revision, ContentRelease.objects.count(), AuditEvent.objects.count()),
            audit_before,
        )

    def test_replay_rejects_evidence_mismatch_and_stale_source_revision(self) -> None:
        source = self._source()
        with fixture_checkout() as root:
            verified = verify_fixture_checkout(root, salt="evidence")
            first = prepare_dtc_content_candidate(
                source_id=source.id,
                expected_source_revision=source.revision,
                verified_checkout=verified,
                commit_sha=verified.commit_sha,
                person_resolver=_resolve_person,
                context=CONTEXT,
            )
            source.refresh_from_db()
            before = (
                ContentRelease.objects.count(),
                ContentDocument.objects.count(),
                ContentRelation.objects.count(),
                ContentAsset.objects.count(),
                AuditEvent.objects.count(),
            )
            changed_bundle = replace(verified.bundle, bundle_sha256="0" * 64)
            changed_checkout = replace(verified, bundle=changed_bundle)
            with self.assertRaises(DtcContentValidationError) as raised:
                prepare_dtc_content_candidate(
                    source_id=source.id,
                    expected_source_revision=source.revision,
                    verified_checkout=changed_checkout,
                    commit_sha=verified.commit_sha,
                    person_resolver=_resolve_person,
                    context=CONTEXT,
                )
            self.assertEqual(
                raised.exception.diagnostics[0].code,
                "existing_release_provenance_mismatch",
            )
            with self.assertRaises(RevisionConflict):
                prepare_dtc_content_candidate(
                    source_id=source.id,
                    expected_source_revision=source.revision - 1,
                    verified_checkout=verified,
                    commit_sha=verified.commit_sha,
                    person_resolver=_resolve_person,
                    context=CONTEXT,
                )
            with self.assertRaises(DtcContentValidationError) as evidence_error:
                prepare_dtc_content_candidate(
                    source_id=source.id,
                    expected_source_revision=source.revision,
                    verified_checkout=replace(verified, tree_sha="0" * 40),
                    commit_sha=verified.commit_sha,
                    person_resolver=_resolve_person,
                    context=CONTEXT,
                )
            self.assertEqual(
                evidence_error.exception.diagnostics[0].code,
                "verified_checkout_evidence_mismatch",
            )
        self.assertEqual(first.release.status, ContentRelease.Status.READY)
        self.assertEqual(
            (
                ContentRelease.objects.count(),
                ContentDocument.objects.count(),
                ContentRelation.objects.count(),
                ContentAsset.objects.count(),
                AuditEvent.objects.count(),
            ),
            before,
        )

    def test_legacy_digest_replay_candidate_is_rejected_without_mutating_existing_release(
        self,
    ) -> None:
        source = self._source()
        with fixture_checkout() as root:
            verified = verify_fixture_checkout(root, salt="legacy-replay")
            first = prepare_dtc_content_candidate(
                source_id=source.id,
                expected_source_revision=source.revision,
                verified_checkout=verified,
                commit_sha=verified.commit_sha,
                person_resolver=_resolve_person,
                context=CONTEXT,
            )
            ContentRelease.objects.filter(pk=first.release.id).update(
                public_contracts_sha256=LEGACY_PUBLIC_CONTRACT_DIGEST,
            )
            source.refresh_from_db()

            def snapshot() -> dict[str, object]:
                return {
                    "release": ContentRelease.objects.values(
                        "id",
                        "source_id",
                        "sequence",
                        "commit_sha",
                        "parser_version",
                        "rendering_version",
                        "status",
                        "revision",
                        "request_provenance",
                        "document_count",
                        "relation_count",
                        "asset_count",
                        "public_contracts_sha256",
                        "asset_manifest_checksum",
                        "created_at",
                        "updated_at",
                    ).get(pk=first.release.id),
                    "documents": list(
                        ContentDocument.objects.filter(release_id=first.release.id)
                        .order_by("pk")
                        .values(
                            "id",
                            "release_id",
                            "content_kind",
                            "stable_key",
                            "source_path",
                            "checksum",
                            "raw_body",
                            "raw_structured_data",
                            "rendered_html",
                            "adapter_metadata",
                            "is_published",
                        )
                    ),
                    "relations": list(
                        ContentRelation.objects.filter(source_document__release_id=first.release.id)
                        .order_by("pk")
                        .values(
                            "id",
                            "source_document_id",
                            "relation_type",
                            "target_kind",
                            "target_key",
                            "resolved_target_document_id",
                            "resolved_public_path",
                            "order",
                            "is_required",
                        )
                    ),
                    "assets": list(
                        ContentAsset.objects.filter(release_id=first.release.id)
                        .order_by("pk")
                        .values(
                            "id",
                            "release_id",
                            "source_path",
                            "stable_public_path",
                            "storage_key",
                            "content_type",
                            "size",
                            "checksum",
                        )
                    ),
                }

            before = snapshot()
            counts_before = (
                ContentRelease.objects.count(),
                ContentDocument.objects.count(),
                ContentRelation.objects.count(),
                ContentAsset.objects.count(),
                AuditEvent.objects.count(),
            )
            source_before = (source.revision, source.active_release_id)

            with self.assertRaises(DtcContentValidationError) as raised:
                prepare_dtc_content_candidate(
                    source_id=source.id,
                    expected_source_revision=source.revision,
                    verified_checkout=verified,
                    commit_sha=verified.commit_sha,
                    person_resolver=_resolve_person,
                    context=CONTEXT,
                )

        self.assertEqual(
            raised.exception.diagnostics[0].code,
            "existing_release_contract_digest_mismatch",
        )
        source.refresh_from_db()
        self.assertEqual((source.revision, source.active_release_id), source_before)
        self.assertEqual(
            (
                ContentRelease.objects.count(),
                ContentDocument.objects.count(),
                ContentRelation.objects.count(),
                ContentAsset.objects.count(),
                AuditEvent.objects.count(),
            ),
            counts_before,
        )
        self.assertEqual(snapshot(), before)

    def test_legacy_and_dtc_sources_cannot_overlap_adopted_ownership(self) -> None:
        legacy = create_content_source(
            CreateContentSource(
                stable_id="dtc-main-site",
                display_name="Legacy main site",
                repository_owner="DataTalksClub",
                repository_name="datatalksclub.github.io",
                branch="main",
                path_allowlist=("_posts/",),
                adapter_type="legacy-main-v1",
                mount_path="/",
                enabled=True,
            ),
            context=CONTEXT,
        )
        release = create_content_release(
            CreateContentRelease(
                source_id=legacy.id,
                expected_source_revision=legacy.revision,
                commit_sha="d" * 40,
                parser_version="legacy-v1",
                rendering_version="legacy-v1",
                request_provenance={"mode": "ownership-test"},
            ),
            context=CONTEXT,
        )
        release = begin_release_fetch(
            TransitionContentRelease(release.id, release.revision),
            context=CONTEXT,
        )
        release = begin_release_validation(
            TransitionContentRelease(release.id, release.revision),
            context=CONTEXT,
        )
        with self.assertRaisesRegex(
            ContentReadinessError,
            "content kind is not owned by the candidate source",
        ):
            prepare_document(
                PrepareDocument(
                    release.id,
                    release.revision,
                    PreparedDocument(
                        content_kind="article",
                        stable_key="forbidden",
                        source_path="_posts/forbidden.md",
                        checksum="d" * 64,
                        exact_public_path="/blog/forbidden.html",
                        title="Forbidden",
                        rendered_html="<p>Forbidden</p>",
                        is_published=True,
                    ),
                ),
                context=CONTEXT,
            )

        dtc = self._source()
        release = create_content_release(
            CreateContentRelease(
                source_id=dtc.id,
                expected_source_revision=dtc.revision,
                commit_sha="e" * 40,
                parser_version=DTC_CONTENT_CONTRACT.parser_version,
                rendering_version=DTC_CONTENT_CONTRACT.rendering_version,
                request_provenance={"mode": "reverse-ownership-test"},
            ),
            context=CONTEXT,
        )
        release = begin_release_fetch(
            TransitionContentRelease(release.id, release.revision), context=CONTEXT
        )
        release = begin_release_validation(
            TransitionContentRelease(release.id, release.revision), context=CONTEXT
        )
        with self.assertRaisesRegex(
            ContentReadinessError,
            "content kind is not owned by the candidate source",
        ):
            prepare_document(
                PrepareDocument(
                    release.id,
                    release.revision,
                    PreparedDocument(
                        content_kind="person",
                        stable_key="forbidden",
                        source_path="people/forbidden.md",
                        checksum="e" * 64,
                        exact_public_path="/people/forbidden.html",
                        title="Forbidden",
                        rendered_html="<p>Forbidden</p>",
                        is_published=True,
                    ),
                ),
                context=CONTEXT,
            )
        with self.assertRaisesRegex(
            ContentReadinessError,
            "asset namespace is not owned by the candidate source",
        ):
            prepare_asset(
                PrepareAsset(
                    release.id,
                    release.revision,
                    PreparedAsset(
                        source_path="images/authors/forbidden.jpg",
                        stable_public_path="/images/authors/forbidden.jpg",
                        storage_key=f"content/dtc-content/{release.id}/images/authors/forbidden.jpg",
                        content_type="image/jpeg",
                        size=1,
                        checksum="e" * 64,
                    ),
                ),
                context=CONTEXT,
            )
