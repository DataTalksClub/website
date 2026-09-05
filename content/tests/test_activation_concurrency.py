from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.db import OperationalError, close_old_connections, connections
from django.test import TransactionTestCase

from content.models import ActiveContentPath, ContentDocument, ContentRelease, ContentSource
from content.services import (
    ActivateContentRelease,
    ContentCollisionError,
    ReleaseSwapResult,
    RollbackContentRelease,
    activate_content_release,
    rollback_content_release,
)
from core.models import AuditEvent

from .factories import CONTEXT, activate, make_ready_release, make_source


class PortableContentActivationConcurrencyTests(TransactionTestCase):
    def _activation_command(
        self,
        source: ContentSource,
        release: ContentRelease,
        *,
        reason: str = "portable activation contender",
    ) -> ActivateContentRelease:
        source.refresh_from_db()
        release.refresh_from_db()
        return ActivateContentRelease(
            source.id,
            release.id,
            source.revision,
            release.revision,
            reason,
        )

    def _activate_in_thread(
        self,
        command: ActivateContentRelease,
    ) -> ReleaseSwapResult | Exception:
        close_old_connections()
        try:
            try:
                return activate_content_release(command, context=CONTEXT)
            except Exception as error:  # The caller asserts the precise domain type.
                return error
        finally:
            connections["default"].close()

    def test_stale_validation_interleaving_has_one_winner_and_one_collision(self) -> None:
        first_source = make_source(stable_id="stale-validation-first")
        second_source = make_source(stable_id="stale-validation-second")
        first = make_ready_release(first_source, commit_character="a")
        second = make_ready_release(second_source, commit_character="b")
        first_command = self._activation_command(first_source, first)
        second_command = self._activation_command(second_source, second)
        original_source_revision = second_command.expected_source_revision
        original_release_revision = second_command.expected_release_revision
        before_audits = AuditEvent.objects.filter(action="content.release.activate").count()
        hook_calls = 0
        first_results: list[ReleaseSwapResult] = []

        def activate_first_after_second_validates() -> None:
            nonlocal hook_calls
            hook_calls += 1
            if hook_calls == 1:
                result = activate_content_release(first_command, context=CONTEXT)
                first_results.append(result)

        with (
            patch(
                "content.services._before_release_swap",
                side_effect=activate_first_after_second_validates,
            ),
            self.assertRaisesRegex(
                ContentCollisionError,
                "active content path namespace collision",
            ),
        ):
            activate_content_release(second_command, context=CONTEXT)

        self.assertEqual(len(first_results), 1)
        self.assertEqual(first_results[0].to_release_id, first.id)
        first_source.refresh_from_db()
        second_source.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first_source.active_release_id, first.id)
        self.assertEqual(first.status, ContentRelease.Status.ACTIVE)
        self.assertIsNone(second_source.active_release_id)
        self.assertEqual(second_source.revision, original_source_revision)
        self.assertEqual(second.status, ContentRelease.Status.READY)
        self.assertIsNone(second.activated_at)
        self.assertEqual(second.revision, original_release_revision)
        self.assertEqual(
            set(
                ActiveContentPath.objects.values_list(
                    "source_id",
                    "release_id",
                    "exact_public_path",
                )
            ),
            {
                (first_source.id, first.id, "/Fixture/Exact.html"),
                (first_source.id, first.id, "/assets/Fixture-Logo.svg"),
            },
        )
        self.assertEqual(
            AuditEvent.objects.filter(action="content.release.activate").count(),
            before_audits + 1,
        )

    def test_cross_source_contenders_return_one_collision_and_rollback_loser(self) -> None:
        first_source = make_source(stable_id="portable-race-first")
        second_source = make_source(stable_id="portable-race-second")
        first = make_ready_release(first_source, commit_character="a")
        second = make_ready_release(second_source, commit_character="b")
        commands = (
            self._activation_command(first_source, first),
            self._activation_command(second_source, second),
        )
        original_source_revisions = {
            first_source.id: commands[0].expected_source_revision,
            second_source.id: commands[1].expected_source_revision,
        }
        original_release_revisions = {
            first.id: commands[0].expected_release_revision,
            second.id: commands[1].expected_release_revision,
        }
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
        self.assertEqual(len(winners), 1, results)
        self.assertEqual(len(collisions), 1, results)
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
        self.assertEqual(
            sources[loser_source_id].revision, original_source_revisions[loser_source_id]
        )
        self.assertEqual(releases[loser_release_id].status, ContentRelease.Status.READY)
        self.assertIsNone(releases[loser_release_id].activated_at)
        self.assertEqual(
            releases[loser_release_id].revision,
            original_release_revisions[loser_release_id],
        )
        # Scoped to the two contending sources: the test database also carries
        # the reviewed documentation release, which claims its own paths.
        self.assertEqual(
            set(
                ActiveContentPath.objects.filter(source_id__in=sources).values_list(
                    "source_id", "release_id"
                )
            ),
            {(winner_source_id, winner_release_id)},
        )
        self.assertEqual(
            AuditEvent.objects.filter(action="content.release.activate").count(),
            before_audits + 1,
        )
        active_owners = set(
            ContentDocument.objects.filter(
                release__status=ContentRelease.Status.ACTIVE,
                exact_public_path="/Fixture/Exact.html",
            ).values_list("release__source_id", flat=True)
        )
        self.assertEqual(active_owners, {winner_source_id})

    def test_rollback_revalidates_namespace_after_adversarial_interleaving(self) -> None:
        rollback_source = make_source(stable_id="rollback-interleaving")
        retained = activate(
            rollback_source,
            make_ready_release(rollback_source, commit_character="a"),
        )
        current = activate(
            rollback_source,
            make_ready_release(
                rollback_source,
                commit_character="b",
                public_path="/rollback/current.html",
                asset_path="/assets/rollback-current.svg",
            ),
        )
        contender_source = make_source(stable_id="rollback-path-contender")
        contender = make_ready_release(contender_source, commit_character="c")
        contender_command = self._activation_command(contender_source, contender)
        rollback_source.refresh_from_db()
        retained.refresh_from_db()
        rollback_command = RollbackContentRelease(
            rollback_source.id,
            retained.id,
            rollback_source.revision,
            retained.revision,
            "adversarial rollback interleaving",
        )
        hook_calls = 0

        def activate_contender_after_rollback_validates() -> None:
            nonlocal hook_calls
            hook_calls += 1
            if hook_calls == 1:
                activate_content_release(contender_command, context=CONTEXT)

        before_rollbacks = AuditEvent.objects.filter(action="content.release.rollback").count()
        with (
            patch(
                "content.services._before_release_swap",
                side_effect=activate_contender_after_rollback_validates,
            ),
            self.assertRaisesRegex(
                ContentCollisionError,
                "active content path namespace collision",
            ),
        ):
            rollback_content_release(rollback_command, context=CONTEXT)

        rollback_source.refresh_from_db()
        contender_source.refresh_from_db()
        retained.refresh_from_db()
        current.refresh_from_db()
        contender.refresh_from_db()
        self.assertEqual(rollback_source.active_release_id, current.id)
        self.assertEqual(current.status, ContentRelease.Status.ACTIVE)
        self.assertEqual(retained.status, ContentRelease.Status.SUPERSEDED)
        self.assertEqual(contender_source.active_release_id, contender.id)
        self.assertEqual(contender.status, ContentRelease.Status.ACTIVE)
        self.assertEqual(
            set(
                ActiveContentPath.objects.filter(
                    exact_public_path="/Fixture/Exact.html"
                ).values_list("source_id", "release_id")
            ),
            {(contender_source.id, contender.id)},
        )
        self.assertEqual(
            AuditEvent.objects.filter(action="content.release.rollback").count(),
            before_rollbacks,
        )

    def test_transient_database_contention_retries_only_the_atomic_swap(self) -> None:
        source = make_source(stable_id="portable-swap-retry")
        release = make_ready_release(source, commit_character="a")
        command = self._activation_command(source, release)

        from content import services

        replace_claims = services._replace_active_path_claims
        attempts = 0

        def fail_once(*args, **kwargs) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OperationalError("simulated portable write contention")
            replace_claims(*args, **kwargs)

        with (
            patch("content.services._before_release_swap") as before_swap,
            patch("content.services._replace_active_path_claims", side_effect=fail_once),
        ):
            result = activate_content_release(command, context=CONTEXT)

        self.assertEqual(result.to_release_id, release.id)
        self.assertEqual(attempts, 2)
        before_swap.assert_called_once_with()
        source.refresh_from_db()
        release.refresh_from_db()
        self.assertEqual(source.active_release_id, release.id)
        self.assertEqual(release.status, ContentRelease.Status.ACTIVE)
        self.assertEqual(
            ActiveContentPath.objects.filter(source=source, release=release).count(),
            2,
        )
        self.assertEqual(
            AuditEvent.objects.filter(action="content.release.activate").count(),
            1,
        )

    def test_partial_failure_restores_the_previous_claims_and_pointer(self) -> None:
        source = make_source(stable_id="claim-partial-failure")
        current = activate(
            source,
            make_ready_release(source, commit_character="a"),
        )
        candidate = make_ready_release(
            source,
            commit_character="b",
            public_path="/partial/candidate.html",
            asset_path="/assets/partial-candidate.svg",
        )
        command = self._activation_command(source, candidate)
        original_claims = set(
            ActiveContentPath.objects.filter(source=source).values_list(
                "path_digest",
                "exact_public_path",
                "release_id",
            )
        )

        with (
            patch("content.services.record_audit_event", side_effect=RuntimeError("audit")),
            self.assertRaisesRegex(RuntimeError, "audit"),
        ):
            activate_content_release(command, context=CONTEXT)

        source.refresh_from_db()
        current.refresh_from_db()
        candidate.refresh_from_db()
        self.assertEqual(source.active_release_id, current.id)
        self.assertEqual(current.status, ContentRelease.Status.ACTIVE)
        self.assertEqual(candidate.status, ContentRelease.Status.READY)
        self.assertIsNone(candidate.activated_at)
        self.assertEqual(
            set(
                ActiveContentPath.objects.filter(source=source).values_list(
                    "path_digest",
                    "exact_public_path",
                    "release_id",
                )
            ),
            original_claims,
        )

    def test_exhausted_database_contention_fails_without_partial_state(self) -> None:
        source = make_source(stable_id="portable-swap-exhausted")
        release = make_ready_release(source, commit_character="a")
        command = self._activation_command(source, release)

        from content import services

        with (
            patch("content.services._before_release_swap") as before_swap,
            patch("content.services.sleep") as retry_sleep,
            patch(
                "content.services._replace_active_path_claims",
                side_effect=OperationalError("persistent portable write contention"),
            ) as replace_claims,
            self.assertRaisesRegex(OperationalError, "persistent portable write contention"),
        ):
            activate_content_release(command, context=CONTEXT)

        before_swap.assert_called_once_with()
        self.assertEqual(replace_claims.call_count, services._RELEASE_SWAP_ATTEMPTS)
        self.assertEqual(retry_sleep.call_count, services._RELEASE_SWAP_ATTEMPTS - 1)
        source.refresh_from_db()
        release.refresh_from_db()
        self.assertIsNone(source.active_release_id)
        self.assertEqual(source.revision, command.expected_source_revision)
        self.assertEqual(release.status, ContentRelease.Status.READY)
        self.assertIsNone(release.activated_at)
        self.assertEqual(release.revision, command.expected_release_revision)
        self.assertFalse(ActiveContentPath.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(action="content.release.activate").exists())
