from __future__ import annotations

import unittest
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from queue import Queue
from threading import Barrier, Event
from time import monotonic
from typing import Any
from unittest.mock import patch

from django.db import (
    IntegrityError,
    close_old_connections,
    connection,
    connections,
    transaction,
)
from django.db.models import F
from django.test import TransactionTestCase
from django.utils import timezone

from content.models import (
    ContentAsset,
    ContentDocument,
    ContentRelation,
    ContentRelease,
    ContentSource,
    expected_storage_prefix,
)
from content.services import (
    ActivateContentRelease,
    ContentCollisionError,
    CreateContentRelease,
    MarkReleaseReady,
    ReleaseSwapResult,
    TransitionContentRelease,
    activate_content_release,
    asset_manifest_checksum_for,
    begin_release_fetch,
    begin_release_validation,
    create_content_release,
    mark_release_ready,
)
from core.audit import record_audit_event
from core.models import AuditEvent, RevisionConflict

from .factories import CONTEXT, activate, make_ready_release, make_source


@unittest.skipUnless(
    connection.vendor == "postgresql",
    "authoritative content constraints require PostgreSQL",
)
class PostgreSQLContentInvariantTests(TransactionTestCase):
    def assertConstraint(self, error: IntegrityError, expected: str) -> None:
        diagnostics = getattr(error.__cause__, "diag", None)
        self.assertEqual(getattr(diagnostics, "constraint_name", None), expected)

    @contextmanager
    def assertDatabaseConstraint(self, expected: str) -> Iterator[None]:
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic():
                yield
        self.assertConstraint(caught.exception, expected)

    def _make_validating_release(
        self,
        source: ContentSource,
        *,
        commit_character: str = "a",
    ) -> ContentRelease:
        source.refresh_from_db()
        release = create_content_release(
            CreateContentRelease(
                source_id=source.id,
                expected_source_revision=source.revision,
                commit_sha=commit_character * 40,
                parser_version="postgres-parser-v1",
                rendering_version="postgres-renderer-v1",
                request_provenance={"mode": "postgres-test"},
            ),
            context=CONTEXT,
        )
        release = begin_release_fetch(
            TransitionContentRelease(release.id, release.revision),
            context=CONTEXT,
        )
        return begin_release_validation(
            TransitionContentRelease(release.id, release.revision),
            context=CONTEXT,
        )

    def _make_validating_release_with_children(
        self,
        source: ContentSource,
        *,
        commit_character: str = "a",
    ) -> tuple[ContentRelease, ContentDocument, ContentRelation, ContentAsset]:
        release = self._make_validating_release(
            source,
            commit_character=commit_character,
        )
        document = ContentDocument.objects.create(
            release=release,
            content_kind="fixture",
            stable_key="postgres-fixture",
            source_path="fixtures/postgres.md",
            checksum=commit_character * 64,
            exact_public_path="/PostgreSQL/Fixture.html",
            title="PostgreSQL fixture",
            rendered_html="<p>PostgreSQL fixture</p>",
            normalized_text="PostgreSQL fixture",
            is_published=True,
        )
        relation = ContentRelation.objects.create(
            source_document=document,
            relation_type="related",
            target_kind="fixture",
            target_key="external-fixture",
            resolved_public_path="/PostgreSQL/Related.html",
            order=0,
            is_required=True,
        )
        asset = ContentAsset.objects.create(
            release=release,
            source_path="fixtures/postgres.svg",
            stable_public_path="/assets/PostgreSQL-Fixture.svg",
            storage_key=(f"{expected_storage_prefix(source.stable_id, release.id)}postgres.svg"),
            content_type="image/svg+xml",
            size=128,
            checksum=commit_character * 64,
        )
        return release, document, relation, asset

    def _freeze_release(
        self,
        release: ContentRelease,
    ) -> ContentRelease:
        return mark_release_ready(
            MarkReleaseReady(
                release_id=release.id,
                expected_revision=release.revision,
                asset_manifest_checksum=asset_manifest_checksum_for(release.id),
            ),
            context=CONTEXT,
        )

    def _wait_until_backend_is_lock_blocked(self, backend_pid: int) -> None:
        deadline = monotonic() + 10
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                    [backend_pid],
                )
                row = cursor.fetchone()
            if row == ("Lock",):
                return
            Event().wait(0.01)
        self.fail(f"PostgreSQL backend {backend_pid} did not block on the parent lock")

    def _activate_in_thread(
        self,
        command: ActivateContentRelease,
        *,
        start: Barrier | None = None,
    ) -> ReleaseSwapResult | Exception:
        close_old_connections()
        try:
            if start is not None:
                start.wait(timeout=10)
            try:
                return activate_content_release(command, context=CONTEXT)
            except Exception as error:  # The caller asserts the precise domain type.
                return error
        finally:
            connections["default"].close()

    def test_expected_postgresql_triggers_are_installed(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT trigger.tgname, relation.relname,
                       trigger.tgdeferrable, trigger.tginitdeferred
                FROM pg_trigger trigger
                JOIN pg_class relation ON relation.oid = trigger.tgrelid
                WHERE NOT trigger.tgisinternal
                  AND trigger.tgname LIKE 'content_%'
                """
            )
            installed = {
                name: (table, deferred, initially_deferred)
                for name, table, deferred, initially_deferred in cursor.fetchall()
            }

        expected: dict[str, tuple[str, bool, bool]] = {
            "content_source_stable_tr": ("content_contentsource", False, False),
            "content_release_frozen_tr": ("content_contentrelease", False, False),
            "content_document_frozen_tr": ("content_contentdocument", False, False),
            "content_relation_frozen_tr": ("content_contentrelation", False, False),
            "content_asset_frozen_tr": ("content_contentasset", False, False),
            "content_source_active_state_ct": ("content_contentsource", True, True),
            "content_release_active_state_ct": ("content_contentrelease", True, True),
        }
        self.assertEqual({name: installed.get(name) for name in expected}, expected)

    def test_direct_sql_cannot_install_a_cross_source_active_pointer(self) -> None:
        source = make_source(stable_id="pointer-owner")
        other_source = make_source(stable_id="pointer-other")
        other_release = activate(
            other_source,
            make_ready_release(
                other_source,
                commit_character="a",
                public_path="/pointer-other.html",
                asset_path="/assets/pointer-other.svg",
            ),
        )

        with self.assertDatabaseConstraint("content_source_active_state_ct"):
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE content_contentsource SET active_release_id = %s WHERE id = %s",
                    [other_release.id, source.id],
                )
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

        source.refresh_from_db()
        self.assertIsNone(source.active_release_id)

    def test_direct_lifecycle_and_timestamp_bypasses_are_rejected(self) -> None:
        source = make_source(stable_id="lifecycle-bypass")
        bad_transition = self._make_validating_release(source, commit_character="a")

        with self.assertDatabaseConstraint("content_release_transition_ck"):
            ContentRelease.objects.filter(pk=bad_transition.id).update(
                status=ContentRelease.Status.ACTIVE,
                activated_at=timezone.now(),
                revision=F("revision") + 1,
            )

        source.refresh_from_db()
        bad_timestamps = create_content_release(
            CreateContentRelease(
                source_id=source.id,
                expected_source_revision=source.revision,
                commit_sha="b" * 40,
                parser_version="postgres-parser-v1",
                rendering_version="postgres-renderer-v1",
                request_provenance={"mode": "postgres-test"},
            ),
            context=CONTEXT,
        )
        with self.assertDatabaseConstraint("content_release_timestamps_ck"):
            ContentRelease.objects.filter(pk=bad_timestamps.id).update(
                status=ContentRelease.Status.FETCHING,
                fetched_at=timezone.now(),
                revision=F("revision") + 1,
            )

    def test_direct_insert_cannot_reuse_a_source_sequence(self) -> None:
        source = make_source(stable_id="duplicate-sequence")
        existing = self._make_validating_release(source, commit_character="a")
        duplicate = ContentRelease(
            source=source,
            sequence=existing.sequence,
            commit_sha="b" * 40,
            parser_version="postgres-parser-v2",
            rendering_version="postgres-renderer-v2",
            requested_at=timezone.now(),
            request_provenance={"mode": "direct-bypass"},
        )

        with self.assertDatabaseConstraint("content_release_source_seq_uq"):
            ContentRelease.objects.bulk_create([duplicate])

    def test_direct_asset_insert_requires_the_release_specific_storage_prefix(self) -> None:
        source = make_source(stable_id="asset-prefix")
        release = self._make_validating_release(source)
        malformed = ContentAsset(
            release=release,
            source_path="fixtures/wrong.svg",
            stable_public_path="/assets/wrong.svg",
            storage_key="content/a-different-source/a-different-release/wrong.svg",
            content_type="image/svg+xml",
            size=1,
            checksum="a" * 64,
        )

        with self.assertDatabaseConstraint("content_asset_storage_key_ck"):
            ContentAsset.objects.bulk_create([malformed])

    def test_frozen_child_insert_update_and_delete_bypasses_are_rejected(self) -> None:
        source = make_source(stable_id="frozen-children")
        release, document, relation, asset = self._make_validating_release_with_children(source)
        release = self._freeze_release(release)

        late_document = ContentDocument(
            release=release,
            content_kind="fixture",
            stable_key="late-document",
            source_path="fixtures/late.md",
            checksum="b" * 64,
            exact_public_path="/PostgreSQL/Late.html",
            title="Late document",
            rendered_html="<p>Late</p>",
            is_published=True,
        )
        with self.assertDatabaseConstraint("content_frozen_child_mutation"):
            ContentDocument.objects.bulk_create([late_document])

        with self.assertDatabaseConstraint("content_frozen_child_mutation"):
            ContentRelation.objects.filter(pk=relation.id).update(label="mutated")

        with self.assertDatabaseConstraint("content_frozen_child_mutation"):
            ContentAsset.objects.filter(pk=asset.id).delete()

        document.refresh_from_db()
        relation.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(document.title, "PostgreSQL fixture")
        self.assertEqual(relation.label, "")
        self.assertEqual(asset.size, 128)

    def test_ready_holds_parent_lock_until_a_late_child_mutation_is_rejected(self) -> None:
        source = make_source(stable_id="freeze-first")
        release, document, _, _ = self._make_validating_release_with_children(source)
        ready_command = MarkReleaseReady(
            release_id=release.id,
            expected_revision=release.revision,
            asset_manifest_checksum=asset_manifest_checksum_for(release.id),
        )
        ready_write_finished = Event()
        allow_ready_commit = Event()
        child_backend: Queue[int] = Queue()

        def hold_ready_audit(**kwargs: Any) -> AuditEvent:
            if kwargs["action"] == "content.release.ready":
                ready_write_finished.set()
                if not allow_ready_commit.wait(timeout=10):
                    raise TimeoutError("test did not release the ready transaction")
            return record_audit_event(**kwargs)

        def freeze() -> ContentRelease:
            close_old_connections()
            try:
                return mark_release_ready(ready_command, context=CONTEXT)
            finally:
                connections["default"].close()

        def mutate_child() -> Exception | None:
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    child_backend.put(cursor.fetchone()[0])
                try:
                    with transaction.atomic():
                        ContentDocument.objects.filter(pk=document.id).update(title="too late")
                except Exception as error:
                    return error
                return None
            finally:
                connections["default"].close()

        with patch("content.services.record_audit_event", side_effect=hold_ready_audit):
            with ThreadPoolExecutor(max_workers=2) as pool:
                ready_future = pool.submit(freeze)
                self.assertTrue(ready_write_finished.wait(timeout=10))
                child_future = pool.submit(mutate_child)
                child_pid = child_backend.get(timeout=10)
                try:
                    self._wait_until_backend_is_lock_blocked(child_pid)
                finally:
                    allow_ready_commit.set()
                frozen = ready_future.result(timeout=10)
                child_error = child_future.result(timeout=10)

        self.assertEqual(frozen.status, ContentRelease.Status.READY)
        self.assertIsInstance(child_error, IntegrityError)
        assert isinstance(child_error, IntegrityError)
        self.assertConstraint(child_error, "content_frozen_child_mutation")
        document.refresh_from_db()
        self.assertEqual(document.title, "PostgreSQL fixture")

    def test_inflight_child_mutation_commits_before_ready_can_freeze_parent(self) -> None:
        source = make_source(stable_id="child-first")
        release, document, _, _ = self._make_validating_release_with_children(source)
        ready_command = MarkReleaseReady(
            release_id=release.id,
            expected_revision=release.revision,
            asset_manifest_checksum=asset_manifest_checksum_for(release.id),
        )
        child_write_finished = Event()
        allow_child_commit = Event()
        ready_backend: Queue[int] = Queue()

        def mutate_child() -> None:
            close_old_connections()
            try:
                with transaction.atomic():
                    ContentDocument.objects.filter(pk=document.id).update(
                        title="mutated before ready"
                    )
                    child_write_finished.set()
                    if not allow_child_commit.wait(timeout=10):
                        raise TimeoutError("test did not release the child transaction")
            finally:
                connections["default"].close()

        def freeze() -> ContentRelease:
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    ready_backend.put(cursor.fetchone()[0])
                return mark_release_ready(ready_command, context=CONTEXT)
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            child_future = pool.submit(mutate_child)
            self.assertTrue(child_write_finished.wait(timeout=10))
            ready_future = pool.submit(freeze)
            ready_pid = ready_backend.get(timeout=10)
            try:
                self._wait_until_backend_is_lock_blocked(ready_pid)
            finally:
                allow_child_commit.set()
            child_future.result(timeout=10)
            frozen = ready_future.result(timeout=10)

        self.assertEqual(frozen.status, ContentRelease.Status.READY)
        document.refresh_from_db()
        self.assertEqual(document.title, "mutated before ready")

    def test_two_candidates_for_one_source_have_one_atomic_winner(self) -> None:
        source = make_source(stable_id="same-source-race")
        old_release = activate(
            source,
            make_ready_release(source, commit_character="a"),
        )
        first = make_ready_release(
            source,
            commit_character="b",
            heading="First contender",
            marker="first-contender",
        )
        second = make_ready_release(
            source,
            commit_character="c",
            heading="Second contender",
            marker="second-contender",
        )
        source.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()
        source_revision = source.revision
        commands = (
            ActivateContentRelease(
                source.id,
                first.id,
                source_revision,
                first.revision,
                "same-source race",
            ),
            ActivateContentRelease(
                source.id,
                second.id,
                source_revision,
                second.revision,
                "same-source race",
            ),
        )
        start = Barrier(2)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self._activate_in_thread, command, start=start) for command in commands
            ]
            results = tuple(future.result(timeout=15) for future in futures)

        winners = [result for result in results if isinstance(result, ReleaseSwapResult)]
        losers = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(winners), 1)
        self.assertEqual(len(losers), 1)
        self.assertIsInstance(losers[0], RevisionConflict)

        winner_id = winners[0].to_release_id
        loser_id = second.id if winner_id == first.id else first.id
        source.refresh_from_db()
        old_release.refresh_from_db()
        winner = ContentRelease.objects.get(pk=winner_id)
        loser = ContentRelease.objects.get(pk=loser_id)
        self.assertEqual(source.active_release_id, winner_id)
        self.assertEqual(old_release.status, ContentRelease.Status.SUPERSEDED)
        self.assertEqual(winner.status, ContentRelease.Status.ACTIVE)
        self.assertEqual(loser.status, ContentRelease.Status.READY)
        self.assertIsNone(loser.activated_at)

    def test_cross_source_contenders_return_one_collision_and_fully_rollback_loser(self) -> None:
        first_source = make_source(stable_id="cross-source-race-first")
        second_source = make_source(stable_id="cross-source-race-second")
        first = make_ready_release(first_source, commit_character="a")
        second = make_ready_release(second_source, commit_character="b")
        first_source.refresh_from_db()
        second_source.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()
        original_source_revisions = {
            first_source.id: first_source.revision,
            second_source.id: second_source.revision,
        }
        original_release_revisions = {first.id: first.revision, second.id: second.revision}
        commands = (
            ActivateContentRelease(
                first_source.id,
                first.id,
                first_source.revision,
                first.revision,
                "cross-source race",
            ),
            ActivateContentRelease(
                second_source.id,
                second.id,
                second_source.revision,
                second.revision,
                "cross-source race",
            ),
        )
        before_audits = AuditEvent.objects.filter(action="content.release.activate").count()
        swap_barrier = Barrier(2)

        with patch(
            "content.services._before_release_swap",
            side_effect=lambda: swap_barrier.wait(timeout=10),
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(self._activate_in_thread, command) for command in commands]
                results = tuple(future.result(timeout=15) for future in futures)

        winners = [result for result in results if isinstance(result, ReleaseSwapResult)]
        collisions = [result for result in results if isinstance(result, ContentCollisionError)]
        self.assertEqual(len(winners), 1)
        self.assertEqual(len(collisions), 1)
        self.assertEqual(str(collisions[0]), "active content path namespace collision")

        winner_release_id = winners[0].to_release_id
        releases = {
            release.id: release
            for release in ContentRelease.objects.filter(pk__in=(first.id, second.id))
        }
        sources = {
            source.id: source
            for source in ContentSource.objects.filter(pk__in=(first_source.id, second_source.id))
        }
        loser_release_id = second.id if winner_release_id == first.id else first.id
        loser_source_id = releases[loser_release_id].source_id
        winner_source_id = releases[winner_release_id].source_id
        self.assertEqual(sources[winner_source_id].active_release_id, winner_release_id)
        self.assertEqual(releases[winner_release_id].status, ContentRelease.Status.ACTIVE)
        self.assertIsNone(sources[loser_source_id].active_release_id)
        self.assertEqual(sources[loser_source_id].last_successful_commit, "")
        self.assertEqual(
            sources[loser_source_id].revision,
            original_source_revisions[loser_source_id],
        )
        self.assertEqual(releases[loser_release_id].status, ContentRelease.Status.READY)
        self.assertIsNone(releases[loser_release_id].activated_at)
        self.assertEqual(
            releases[loser_release_id].revision,
            original_release_revisions[loser_release_id],
        )
        self.assertEqual(
            AuditEvent.objects.filter(action="content.release.activate").count(),
            before_audits + 1,
        )

    def test_enabled_namespace_rejects_document_and_mixed_path_collisions(self) -> None:
        cases: tuple[tuple[str, dict[str, Any], dict[str, Any]], ...] = (
            (
                "document-document",
                {
                    "public_path": "/postgres-doc-collision.html",
                    "asset_path": "/assets/postgres-doc-owner.svg",
                },
                {
                    "public_path": "/postgres-doc-collision.html",
                    "asset_path": "/assets/postgres-doc-contender.svg",
                },
            ),
            (
                "document-asset",
                {
                    "public_path": "/postgres-mixed-collision.html",
                    "asset_path": "/assets/postgres-mixed-owner.svg",
                },
                {
                    "public_path": "/postgres-mixed-contender.html",
                    "asset_path": "/postgres-mixed-collision.html",
                },
            ),
            (
                "asset-asset",
                {
                    "public_path": "/postgres-asset-owner.html",
                    "asset_path": "/assets/postgres-asset-collision.svg",
                },
                {
                    "public_path": "/postgres-asset-contender.html",
                    "asset_path": "/assets/postgres-asset-collision.svg",
                },
            ),
        )
        for suffix, owner_paths, contender_paths in cases:
            with self.subTest(case=suffix):
                owner = make_source(stable_id=f"namespace-owner-{suffix}")
                activate(
                    owner,
                    make_ready_release(owner, commit_character="a", **owner_paths),
                )
                contender = make_source(
                    stable_id=f"namespace-contender-{suffix}",
                    enabled=False,
                )
                activate(
                    contender,
                    make_ready_release(contender, commit_character="b", **contender_paths),
                )

                with self.assertDatabaseConstraint("content_active_path_namespace_ct"):
                    ContentSource.objects.filter(pk=contender.id).update(enabled=True)
                    with connection.cursor() as cursor:
                        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

                contender.refresh_from_db()
                self.assertFalse(contender.enabled)

    def test_same_commit_with_distinct_build_versions_coexists(self) -> None:
        source = make_source(stable_id="same-commit-builds")
        first = activate(
            source,
            make_ready_release(source, commit_character="a"),
        )
        second = make_ready_release(
            source,
            commit_character="a",
            parser_version="fixture-parser-v2",
            rendering_version="fixture-renderer-v2",
            heading="Re-rendered fixture",
            marker="same-commit-new-build",
        )

        self.assertEqual(ContentRelease.objects.filter(source=source).count(), 2)
        self.assertEqual(first.commit_sha, second.commit_sha)
        self.assertNotEqual(
            (first.parser_version, first.rendering_version),
            (second.parser_version, second.rendering_version),
        )
        first_key = first.assets.get().storage_key
        second_key = second.assets.get().storage_key
        self.assertNotEqual(first_key, second_key)
        self.assertTrue(first_key.startswith(expected_storage_prefix(source.stable_id, first.id)))
        self.assertTrue(second_key.startswith(expected_storage_prefix(source.stable_id, second.id)))
