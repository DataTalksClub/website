from __future__ import annotations

import json
from collections.abc import Callable
from unittest.mock import patch

from django.test import TestCase

from content.models import (
    LEGACY_PUBLIC_CONTRACT_DIGEST,
    PUBLIC_CONTRACT_DIGEST,
    ContentDocument,
    ContentRelease,
    expected_storage_prefix,
)
from content.queries import (
    ResolvePublicAsset,
    ResolvePublicDocument,
    resolve_public_asset,
    resolve_public_document,
)
from content.services import (
    ActivateContentRelease,
    ContentLifecycleError,
    ContentReadinessError,
    CreateContentRelease,
    EndReleaseWithDiagnostics,
    MarkReleaseReady,
    PrepareAsset,
    PreparedAsset,
    PreparedDocument,
    PrepareDocument,
    PreparedRelation,
    PrepareRelation,
    RollbackContentRelease,
    TransitionContentRelease,
    activate_content_release,
    asset_manifest_checksum_for,
    begin_release_fetch,
    begin_release_validation,
    create_content_release,
    mark_release_failed,
    mark_release_invalid,
    mark_release_ready,
    prepare_asset,
    prepare_document,
    prepare_relation,
    rollback_content_release,
    sanitize_rendered_html,
)
from core.models import AuditEvent, RevisionConflict
from core.services import ServiceContext

from .factories import CONTEXT, activate, make_ready_release, make_source


class ContentLifecycleTests(TestCase):
    def _create_queued(self, source, character: str = "d") -> ContentRelease:
        source.refresh_from_db()
        return create_content_release(
            CreateContentRelease(
                source_id=source.id,
                expected_source_revision=source.revision,
                commit_sha=character * 40,
                parser_version="parser-v1",
                rendering_version="renderer-v1",
                request_provenance={"mode": "fixture"},
            ),
            context=CONTEXT,
        )

    def test_new_release_digest_is_current_and_legacy_release_can_be_rolled_back(self) -> None:
        source = make_source()
        source.refresh_from_db()
        with self.assertRaisesRegex(
            ContentReadinessError,
            "checked public contract artifact",
        ):
            create_content_release(
                CreateContentRelease(
                    source_id=source.id,
                    expected_source_revision=source.revision,
                    commit_sha="a" * 40,
                    parser_version="parser-v1",
                    rendering_version="renderer-v1",
                    request_provenance={"mode": "legacy-digest-rejection"},
                    public_contracts_sha256=LEGACY_PUBLIC_CONTRACT_DIGEST,
                ),
                context=CONTEXT,
            )
        # Scoped to this source: the test database also carries the reviewed
        # documentation release, which this test says nothing about.
        self.assertEqual(ContentRelease.objects.filter(source=source).count(), 0)

        first = activate(source, make_ready_release(source, commit_character="b"))
        self.assertEqual(first.public_contracts_sha256, PUBLIC_CONTRACT_DIGEST)
        second = make_ready_release(source, commit_character="c")
        first.refresh_from_db()
        source.refresh_from_db()
        ContentRelease.objects.filter(pk=second.id).update(
            public_contracts_sha256=LEGACY_PUBLIC_CONTRACT_DIGEST,
        )
        second.refresh_from_db()
        with self.assertRaises(ContentReadinessError):
            activate_content_release(
                ActivateContentRelease(
                    source.id,
                    second.id,
                    source.revision,
                    second.revision,
                    "legacy digest cannot activate as a new candidate",
                ),
                context=CONTEXT,
            )
        source.refresh_from_db()
        self.assertEqual(source.active_release_id, first.id)

        ContentRelease.objects.filter(pk=second.id).update(
            public_contracts_sha256=PUBLIC_CONTRACT_DIGEST,
        )
        second.refresh_from_db()
        source.refresh_from_db()
        activate(source, second)

        ContentRelease.objects.filter(pk=first.id).update(
            public_contracts_sha256=LEGACY_PUBLIC_CONTRACT_DIGEST,
        )
        first.refresh_from_db()
        source.refresh_from_db()
        rollback_content_release(
            RollbackContentRelease(
                source.id,
                first.id,
                source.revision,
                first.revision,
                "retain legacy release",
            ),
            context=CONTEXT,
        )
        source.refresh_from_db()
        first.refresh_from_db()
        self.assertEqual(source.active_release_id, first.id)
        self.assertEqual(first.public_contracts_sha256, LEGACY_PUBLIC_CONTRACT_DIGEST)

    def test_allowed_terminal_edges_and_omitted_transitions_fail_closed(self) -> None:
        source = make_source()
        queued = self._create_queued(source, "d")
        failed = mark_release_failed(
            EndReleaseWithDiagnostics(queued.id, queued.revision, ({"code": "fetch_failed"},)),
            context=CONTEXT,
        )
        self.assertEqual(failed.status, ContentRelease.Status.FAILED)
        self.assertIsNotNone(failed.failed_at)
        self.assertIsNone(failed.validated_at)
        with self.assertRaises(ContentLifecycleError):
            begin_release_fetch(
                TransitionContentRelease(failed.id, failed.revision), context=CONTEXT
            )

        fetching = begin_release_fetch(
            TransitionContentRelease(self._create_queued(source, "e").id, 1), context=CONTEXT
        )
        failed_fetching = mark_release_failed(
            EndReleaseWithDiagnostics(
                fetching.id,
                fetching.revision,
                ({"code": "checkout_failed"},),
            ),
            context=CONTEXT,
        )
        self.assertEqual(failed_fetching.status, ContentRelease.Status.FAILED)

        validating = begin_release_validation(
            TransitionContentRelease(
                begin_release_fetch(
                    TransitionContentRelease(self._create_queued(source, "f").id, 1),
                    context=CONTEXT,
                ).id,
                2,
            ),
            context=CONTEXT,
        )
        invalid = mark_release_invalid(
            EndReleaseWithDiagnostics(
                validating.id,
                validating.revision,
                ({"code": "invalid_fixture"},),
            ),
            context=CONTEXT,
        )
        self.assertEqual(invalid.status, ContentRelease.Status.INVALID)
        self.assertIsNotNone(invalid.validated_at)
        self.assertIsNone(invalid.failed_at)

        validating_failure = begin_release_validation(
            TransitionContentRelease(
                begin_release_fetch(
                    TransitionContentRelease(self._create_queued(source, "0").id, 1),
                    context=CONTEXT,
                ).id,
                2,
            ),
            context=CONTEXT,
        )
        failed_validation = mark_release_failed(
            EndReleaseWithDiagnostics(
                validating_failure.id,
                validating_failure.revision,
                ({"code": "validation_worker_failed"},),
            ),
            context=CONTEXT,
        )
        self.assertEqual(failed_validation.status, ContentRelease.Status.FAILED)
        self.assertIsNotNone(failed_validation.fetched_at)
        self.assertIsNone(failed_validation.validated_at)
        self.assertIsNotNone(failed_validation.failed_at)

    def test_every_omitted_lifecycle_transition_fails_closed(self) -> None:
        source = make_source()
        queued = self._create_queued(source, "a")
        fetching = begin_release_fetch(
            TransitionContentRelease(self._create_queued(source, "b").id, 1), context=CONTEXT
        )
        validating = begin_release_validation(
            TransitionContentRelease(
                begin_release_fetch(
                    TransitionContentRelease(self._create_queued(source, "c").id, 1),
                    context=CONTEXT,
                ).id,
                2,
            ),
            context=CONTEXT,
        )
        invalid_candidate = begin_release_validation(
            TransitionContentRelease(
                begin_release_fetch(
                    TransitionContentRelease(self._create_queued(source, "d").id, 1),
                    context=CONTEXT,
                ).id,
                2,
            ),
            context=CONTEXT,
        )
        invalid = mark_release_invalid(
            EndReleaseWithDiagnostics(
                invalid_candidate.id,
                invalid_candidate.revision,
                ({"code": "invalid"},),
            ),
            context=CONTEXT,
        )
        failed_candidate = self._create_queued(source, "e")
        failed = mark_release_failed(
            EndReleaseWithDiagnostics(
                failed_candidate.id,
                failed_candidate.revision,
                ({"code": "failed"},),
            ),
            context=CONTEXT,
        )
        ready = make_ready_release(
            source,
            commit_character="f",
            public_path="/matrix-ready.html",
            asset_path="/assets/matrix-ready.svg",
        )
        superseded = activate(
            source,
            make_ready_release(
                source,
                commit_character="0",
                public_path="/matrix-v1.html",
                asset_path="/assets/matrix-v1.svg",
            ),
        )
        active = activate(
            source,
            make_ready_release(
                source,
                commit_character="1",
                public_path="/matrix-v2.html",
                asset_path="/assets/matrix-v2.svg",
            ),
        )
        releases = {
            ContentRelease.Status.QUEUED: queued,
            ContentRelease.Status.FETCHING: fetching,
            ContentRelease.Status.VALIDATING: validating,
            ContentRelease.Status.READY: ready,
            ContentRelease.Status.ACTIVE: active,
            ContentRelease.Status.SUPERSEDED: superseded,
            ContentRelease.Status.INVALID: invalid,
            ContentRelease.Status.FAILED: failed,
        }

        def activate_candidate(release: ContentRelease) -> object:
            source.refresh_from_db()
            return activate_content_release(
                ActivateContentRelease(
                    source.id,
                    release.id,
                    source.revision,
                    release.revision,
                ),
                context=CONTEXT,
            )

        def rollback_candidate(release: ContentRelease) -> object:
            source.refresh_from_db()
            return rollback_content_release(
                RollbackContentRelease(
                    source.id,
                    release.id,
                    source.revision,
                    release.revision,
                    "lifecycle matrix rollback",
                ),
                context=CONTEXT,
            )

        operations: tuple[tuple[str, frozenset[str], Callable[[ContentRelease], object]], ...] = (
            (
                ContentRelease.Status.FETCHING,
                frozenset({ContentRelease.Status.QUEUED}),
                lambda release: begin_release_fetch(
                    TransitionContentRelease(release.id, release.revision), context=CONTEXT
                ),
            ),
            (
                ContentRelease.Status.VALIDATING,
                frozenset({ContentRelease.Status.FETCHING}),
                lambda release: begin_release_validation(
                    TransitionContentRelease(release.id, release.revision), context=CONTEXT
                ),
            ),
            (
                ContentRelease.Status.READY,
                frozenset({ContentRelease.Status.VALIDATING}),
                lambda release: mark_release_ready(
                    MarkReleaseReady(
                        release.id,
                        release.revision,
                        release.asset_manifest_checksum or "0" * 64,
                    ),
                    context=CONTEXT,
                ),
            ),
            (
                ContentRelease.Status.INVALID,
                frozenset({ContentRelease.Status.VALIDATING}),
                lambda release: mark_release_invalid(
                    EndReleaseWithDiagnostics(
                        release.id,
                        release.revision,
                        ({"code": "matrix_invalid"},),
                    ),
                    context=CONTEXT,
                ),
            ),
            (
                ContentRelease.Status.FAILED,
                frozenset(
                    {
                        ContentRelease.Status.QUEUED,
                        ContentRelease.Status.FETCHING,
                        ContentRelease.Status.VALIDATING,
                    }
                ),
                lambda release: mark_release_failed(
                    EndReleaseWithDiagnostics(
                        release.id,
                        release.revision,
                        ({"code": "matrix_failed"},),
                    ),
                    context=CONTEXT,
                ),
            ),
            (
                ContentRelease.Status.ACTIVE,
                frozenset({ContentRelease.Status.READY}),
                activate_candidate,
            ),
            (
                ContentRelease.Status.ACTIVE,
                frozenset({ContentRelease.Status.SUPERSEDED}),
                rollback_candidate,
            ),
        )
        source.refresh_from_db()
        active_release_id = source.active_release_id
        audit_count = AuditEvent.objects.count()
        for next_status, allowed_statuses, operation in operations:
            for status, release in releases.items():
                if status in allowed_statuses:
                    continue
                with self.subTest(status=status, next_status=next_status):
                    release.refresh_from_db()
                    with self.assertRaises(ContentLifecycleError):
                        operation(release)
                    release.refresh_from_db()
                    self.assertEqual(release.status, status)
                    source.refresh_from_db()
                    self.assertEqual(source.active_release_id, active_release_id)
        self.assertEqual(AuditEvent.objects.count(), audit_count)

    def test_stale_revision_and_unattributed_swap_are_rejected(self) -> None:
        source = make_source()
        release = make_ready_release(source, commit_character="a")
        source.refresh_from_db()
        release.refresh_from_db()
        with self.assertRaises(RevisionConflict):
            activate_content_release(
                ActivateContentRelease(
                    source.id,
                    release.id,
                    source.revision - 1,
                    release.revision,
                ),
                context=CONTEXT,
            )
        with self.assertRaises(RevisionConflict):
            activate_content_release(
                ActivateContentRelease(source.id, release.id, source.revision, 1),
                context=CONTEXT,
            )
        source.refresh_from_db()
        release.refresh_from_db()
        anonymous = ServiceContext(correlation_id="anonymous-swap")
        with self.assertRaises(ContentLifecycleError):
            activate_content_release(
                ActivateContentRelease(
                    source.id,
                    release.id,
                    source.revision,
                    release.revision,
                ),
                context=anonymous,
            )
        self.assertIsNone(source.active_release_id)

    def test_normal_activation_stale_base_and_non_increasing_sequence(self) -> None:
        source = make_source()
        activate(source, make_ready_release(source, commit_character="a"))
        v2 = make_ready_release(
            source,
            commit_character="b",
            heading="Fixture release v2",
            marker="commit-v2",
        )
        stale = make_ready_release(
            source,
            commit_character="c",
            public_path="/stale.html",
            asset_path="/assets/stale.svg",
            heading="Stale candidate",
        )
        activate(source, v2)
        source.refresh_from_db()
        stale.refresh_from_db()
        with self.assertRaises(ContentLifecycleError):
            activate_content_release(
                ActivateContentRelease(
                    source.id,
                    stale.id,
                    source.revision,
                    stale.revision,
                ),
                context=CONTEXT,
            )
        source.refresh_from_db()
        self.assertEqual(source.active_release_id, v2.id)

        current = ContentRelease.objects.get(pk=v2.id)
        candidate = ContentRelease.objects.get(pk=stale.id)
        stored_sequences = (current.sequence, candidate.sequence)
        stored_candidate_base_id = candidate.based_on_release_id
        stored_statuses = (current.status, candidate.status)
        stored_revisions = (current.revision, candidate.revision)
        stored_audit_count = AuditEvent.objects.count()
        candidate.based_on_release_id = current.id
        for candidate_sequence in (current.sequence - 1, current.sequence):
            candidate.sequence = candidate_sequence
            with (
                self.subTest(candidate_sequence=candidate_sequence),
                patch(
                    "content.services._lock_swap_releases",
                    return_value=(current, candidate),
                ),
                self.assertRaisesRegex(
                    ContentLifecycleError,
                    "normal activation sequence must increase",
                ),
            ):
                activate_content_release(
                    ActivateContentRelease(
                        source.id,
                        candidate.id,
                        source.revision,
                        candidate.revision,
                    ),
                    context=CONTEXT,
                )
            persisted_current = ContentRelease.objects.get(pk=current.id)
            persisted_candidate = ContentRelease.objects.get(pk=candidate.id)
            self.assertEqual(
                (persisted_current.status, persisted_candidate.status),
                stored_statuses,
            )
            self.assertEqual(
                (persisted_current.revision, persisted_candidate.revision),
                stored_revisions,
            )
            self.assertEqual(AuditEvent.objects.count(), stored_audit_count)

        source.refresh_from_db()
        v2.refresh_from_db()
        stale.refresh_from_db()
        self.assertEqual(source.active_release_id, v2.id)
        self.assertEqual((v2.sequence, stale.sequence), stored_sequences)
        self.assertEqual(stale.based_on_release_id, stored_candidate_base_id)

    def test_invalid_and_failed_releases_never_activate_or_roll_back(self) -> None:
        source = make_source()
        active = activate(source, make_ready_release(source, commit_character="a"))
        invalid = self._create_queued(source, "b")
        invalid = begin_release_fetch(
            TransitionContentRelease(invalid.id, invalid.revision), context=CONTEXT
        )
        invalid = begin_release_validation(
            TransitionContentRelease(invalid.id, invalid.revision), context=CONTEXT
        )
        invalid = mark_release_invalid(
            EndReleaseWithDiagnostics(
                invalid.id,
                invalid.revision,
                ({"code": "invalid"},),
            ),
            context=CONTEXT,
        )
        failed_candidate = self._create_queued(source, "c")
        failed = mark_release_failed(
            EndReleaseWithDiagnostics(
                failed_candidate.id,
                failed_candidate.revision,
                ({"code": "failed"},),
            ),
            context=CONTEXT,
        )
        for release in (invalid, failed):
            source.refresh_from_db()
            with self.assertRaises(ContentLifecycleError):
                activate_content_release(
                    ActivateContentRelease(
                        source.id,
                        release.id,
                        source.revision,
                        release.revision,
                    ),
                    context=CONTEXT,
                )
            with self.assertRaises(ContentLifecycleError):
                rollback_content_release(
                    RollbackContentRelease(
                        source.id,
                        release.id,
                        source.revision,
                        release.revision,
                        "invalid rollback target",
                    ),
                    context=CONTEXT,
                )
        source.refresh_from_db()
        self.assertEqual(source.active_release_id, active.id)

    def test_activation_rollback_and_audit_are_atomic_and_redacted(self) -> None:
        source = make_source()
        v1 = activate(source, make_ready_release(source, commit_character="a"))
        v2 = make_ready_release(
            source,
            commit_character="b",
            heading="Fixture release v2",
            marker="commit-v2",
        )
        source.refresh_from_db()
        v2.refresh_from_db()
        activate_content_release(
            ActivateContentRelease(
                source.id,
                v2.id,
                source.revision,
                v2.revision,
                reason="ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            ),
            context=CONTEXT,
        )
        visible = resolve_public_document(
            ResolvePublicDocument("/Fixture/Exact.html"), context=CONTEXT
        )
        assert visible is not None
        self.assertEqual(visible.title, "Fixture release v2")

        source.refresh_from_db()
        v1.refresh_from_db()
        result = rollback_content_release(
            RollbackContentRelease(
                source.id,
                v1.id,
                source.revision,
                v1.revision,
                "operator rollback after fixture validation",
            ),
            context=CONTEXT,
        )
        self.assertEqual(result.mode, "rollback")
        visible = resolve_public_document(
            ResolvePublicDocument("/Fixture/Exact.html"), context=CONTEXT
        )
        assert visible is not None
        self.assertEqual(visible.title, "Fixture release v1")

        # Scoped to this source: the test database also carries the reviewed
        # documentation release, whose activation this test says nothing about.
        events = AuditEvent.objects.filter(
            action__in=("content.release.activate", "content.release.rollback"),
            target_id=source.id,
        )
        self.assertEqual(events.count(), 3)
        for event in events:
            self.assertEqual(event.actor_ref, "user:content-tests")
            self.assertEqual(event.request_id, "request-content-tests")
            self.assertEqual(event.correlation_id, "correlation-content-tests")
            self.assertEqual(event.target_id, source.id)
            self.assertIn(event.action, {"content.release.activate", "content.release.rollback"})
            self.assertIn(event.metadata["mode"], {"activation", "rollback"})
            self.assertEqual(event.metadata["source_id"], str(source.id))
            self.assertIn("from_release_id", event.metadata)
            self.assertIn("to_release_id", event.metadata)
            self.assertIn("reason", event.metadata)
        rendered = json.dumps(
            [
                {
                    "actor_ref": event.actor_ref,
                    "action": event.action,
                    "request_id": event.request_id,
                    "correlation_id": event.correlation_id,
                    "changes": event.changes,
                    "metadata": event.metadata,
                }
                for event in events
            ]
        )
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz0123456789", rendered)
        self.assertNotIn("# raw commit", rendered)
        self.assertNotIn("private_build_note", rendered)
        self.assertIn(str(source.id), rendered)
        self.assertIn("rollback", rendered)

    def test_missing_reason_never_active_and_injected_failures_keep_last_known_good(self) -> None:
        source = make_source()
        v1 = activate(source, make_ready_release(source, commit_character="a"))
        ready = make_ready_release(
            source,
            commit_character="b",
            heading="Fixture release v2",
            marker="commit-v2",
        )
        source.refresh_from_db()
        ready.refresh_from_db()
        with self.assertRaises(ContentLifecycleError):
            rollback_content_release(
                RollbackContentRelease(
                    source.id,
                    ready.id,
                    source.revision,
                    ready.revision,
                    "",
                ),
                context=CONTEXT,
            )
        with self.assertRaises(ContentLifecycleError):
            rollback_content_release(
                RollbackContentRelease(
                    source.id,
                    ready.id,
                    source.revision,
                    ready.revision,
                    "ready release was never active",
                ),
                context=CONTEXT,
            )
        with patch("content.services._before_release_swap", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                activate_content_release(
                    ActivateContentRelease(
                        source.id,
                        ready.id,
                        source.revision,
                        ready.revision,
                    ),
                    context=CONTEXT,
                )
        source.refresh_from_db()
        ready.refresh_from_db()
        v1.refresh_from_db()
        self.assertEqual(source.active_release_id, v1.id)
        self.assertEqual(v1.status, ContentRelease.Status.ACTIVE)
        self.assertEqual(ready.status, ContentRelease.Status.READY)

        with patch("content.services.record_audit_event", side_effect=RuntimeError("audit")):
            with self.assertRaises(RuntimeError):
                activate_content_release(
                    ActivateContentRelease(
                        source.id,
                        ready.id,
                        source.revision,
                        ready.revision,
                    ),
                    context=CONTEXT,
                )
        source.refresh_from_db()
        self.assertEqual(source.active_release_id, v1.id)

    def test_rollback_injected_and_audit_failures_keep_current_release(self) -> None:
        source = make_source()
        v1 = activate(source, make_ready_release(source, commit_character="a"))
        v2 = activate(
            source,
            make_ready_release(
                source,
                commit_character="b",
                heading="Fixture release v2",
                marker="commit-v2",
            ),
        )
        for patch_target in (
            "content.services._before_release_swap",
            "content.services.record_audit_event",
        ):
            source.refresh_from_db()
            v1.refresh_from_db()
            with patch(patch_target, side_effect=RuntimeError("rollback injected")):
                with self.assertRaises(RuntimeError):
                    rollback_content_release(
                        RollbackContentRelease(
                            source.id,
                            v1.id,
                            source.revision,
                            v1.revision,
                            "test rollback failure",
                        ),
                        context=CONTEXT,
                    )
            source.refresh_from_db()
            v1.refresh_from_db()
            v2.refresh_from_db()
            self.assertEqual(source.active_release_id, v2.id)
            self.assertEqual(v1.status, ContentRelease.Status.SUPERSEDED)
            self.assertEqual(v2.status, ContentRelease.Status.ACTIVE)

    def test_readiness_rejects_cross_release_relation_and_unsafe_html(self) -> None:
        source = make_source()
        active = activate(source, make_ready_release(source, commit_character="a"))
        candidate = self._create_queued(source, "b")
        candidate = begin_release_fetch(
            TransitionContentRelease(candidate.id, candidate.revision), context=CONTEXT
        )
        candidate = begin_release_validation(
            TransitionContentRelease(candidate.id, candidate.revision), context=CONTEXT
        )
        prepared = prepare_document(
            PrepareDocument(
                candidate.id,
                candidate.revision,
                PreparedDocument(
                    content_kind="fixture",
                    stable_key="unsafe",
                    source_path="unsafe.md",
                    checksum="b" * 64,
                    title="Unsafe",
                    exact_public_path="/unsafe.html",
                    rendered_html='<svg/onload=alert(1)><img src="https://evil.invalid/x">',
                    is_published=True,
                ),
            ),
            context=CONTEXT,
        )
        candidate.refresh_from_db()
        active_document = ContentDocument.objects.get(release=active)
        with self.assertRaises(ContentReadinessError):
            prepare_relation(
                PrepareRelation(
                    candidate.id,
                    candidate.revision,
                    PreparedRelation(
                        source_document_id=prepared.id,
                        relation_type="related",
                        target_kind="fixture",
                        target_key="active",
                        order=0,
                        resolved_target_document_id=active_document.id,
                    ),
                ),
                context=CONTEXT,
            )
        with self.assertRaises(ContentReadinessError):
            prepare_relation(
                PrepareRelation(
                    candidate.id,
                    candidate.revision,
                    PreparedRelation(
                        source_document_id=prepared.id,
                        relation_type="guest",
                        target_kind="person",
                        target_key="missing-person",
                        order=1,
                        is_required=True,
                    ),
                ),
                context=CONTEXT,
            )
        with self.assertRaises(ContentReadinessError):
            mark_release_ready(
                MarkReleaseReady(
                    candidate.id,
                    candidate.revision,
                    asset_manifest_checksum_for(candidate.id),
                ),
                context=CONTEXT,
            )
        visible = resolve_public_document(
            ResolvePublicDocument("/Fixture/Exact.html"), context=CONTEXT
        )
        assert visible is not None
        self.assertEqual(visible.title, "Fixture release v1")

    def test_missing_manifest_and_path_collisions_leave_public_release_unchanged(self) -> None:
        source = make_source()
        active = activate(source, make_ready_release(source, commit_character="a"))
        candidate = self._create_queued(source, "b")
        candidate = begin_release_fetch(
            TransitionContentRelease(candidate.id, candidate.revision), context=CONTEXT
        )
        candidate = begin_release_validation(
            TransitionContentRelease(candidate.id, candidate.revision), context=CONTEXT
        )
        with self.assertRaises(ContentReadinessError):
            mark_release_ready(
                MarkReleaseReady(candidate.id, candidate.revision, ""),
                context=CONTEXT,
            )

        with self.assertRaises(ContentReadinessError):
            make_ready_release(
                source,
                commit_character="c",
                public_path="/internal-collision",
                asset_path="/internal-collision",
            )

        other_source = make_source()
        with self.assertRaises(ContentReadinessError):
            make_ready_release(
                other_source,
                commit_character="d",
                public_path="/Fixture/Exact.html",
                asset_path="/assets/Fixture-Logo.svg",
            )
        with self.assertRaises(ContentReadinessError):
            make_ready_release(
                other_source,
                commit_character="e",
                public_path="/other-source.html",
                asset_path="/Fixture/Exact.html",
            )
        source.refresh_from_db()
        self.assertEqual(source.active_release_id, active.id)
        visible = resolve_public_document(
            ResolvePublicDocument("/Fixture/Exact.html"), context=CONTEXT
        )
        assert visible is not None
        self.assertEqual(visible.title, "Fixture release v1")

    def test_missing_prepared_asset_fails_manifest_and_preserves_old_asset(self) -> None:
        source = make_source()
        active = activate(source, make_ready_release(source, commit_character="a"))
        old_asset = resolve_public_asset(
            ResolvePublicAsset("/assets/Fixture-Logo.svg"), context=CONTEXT
        )
        assert old_asset is not None
        candidate = self._create_queued(source, "b")
        candidate = begin_release_fetch(
            TransitionContentRelease(candidate.id, candidate.revision), context=CONTEXT
        )
        candidate = begin_release_validation(
            TransitionContentRelease(candidate.id, candidate.revision), context=CONTEXT
        )
        prepare_document(
            PrepareDocument(
                candidate.id,
                candidate.revision,
                PreparedDocument(
                    content_kind="fixture",
                    stable_key="v2",
                    source_path="v2.md",
                    checksum="b" * 64,
                    title="V2",
                    exact_public_path="/v2.html",
                    rendered_html="<h1>V2</h1>",
                    is_published=True,
                ),
            ),
            context=CONTEXT,
        )
        candidate.refresh_from_db()
        prepared_asset = prepare_asset(
            PrepareAsset(
                candidate.id,
                candidate.revision,
                PreparedAsset(
                    source_path="v2.svg",
                    stable_public_path="/assets/v2.svg",
                    storage_key=(
                        f"{expected_storage_prefix(source.stable_id, candidate.id)}v2.svg"
                    ),
                    content_type="image/svg+xml",
                    size=10,
                    checksum="b" * 64,
                ),
            ),
            context=CONTEXT,
        )
        expected_manifest = asset_manifest_checksum_for(candidate.id)
        prepared_asset.delete()
        candidate.refresh_from_db()
        with self.assertRaises(ContentReadinessError):
            mark_release_ready(
                MarkReleaseReady(candidate.id, candidate.revision, expected_manifest),
                context=CONTEXT,
            )
        source.refresh_from_db()
        self.assertEqual(source.active_release_id, active.id)
        current_asset = resolve_public_asset(
            ResolvePublicAsset("/assets/Fixture-Logo.svg"), context=CONTEXT
        )
        assert current_asset is not None
        self.assertEqual(current_asset.storage_key, old_asset.storage_key)
        self.assertEqual(current_asset.checksum, old_asset.checksum)

    def test_sanitizer_rejects_active_fetch_vectors_and_entity_obfuscation(self) -> None:
        unsafe = (
            '<img src="https://evil.invalid/x">',
            '<img src="//evil.invalid/x">',
            '<img src="data:image/svg+xml,x">',
            '<img src="java&#x73;cript:alert(1)">',
            "<svg/onload=alert(1)>",
        )
        for rendered_html in unsafe:
            self.assertNotEqual(
                sanitize_rendered_html("fixture", rendered_html),
                rendered_html,
            )

    def test_same_commit_with_new_build_uses_distinct_release_storage_keys(self) -> None:
        source = make_source()
        first = activate(source, make_ready_release(source, commit_character="a"))
        second = make_ready_release(
            source,
            commit_character="a",
            parser_version="fixture-parser-v2",
            rendering_version="fixture-renderer-v2",
            heading="Re-rendered fixture",
            marker="commit-v1-render-v2",
        )
        first_asset = resolve_public_asset(
            ResolvePublicAsset("/assets/Fixture-Logo.svg"), context=CONTEXT
        )
        assert first_asset is not None
        second_key = second.assets.get().storage_key
        self.assertNotEqual(first_asset.storage_key, second_key)
        self.assertIn(str(first.id), first_asset.storage_key)
        self.assertIn(str(second.id), second_key)
