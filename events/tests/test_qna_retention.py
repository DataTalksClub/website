"""Retention honors the approved contract; expired buckets do not accumulate.

The retention contract (``_docs/architecture/event-qna-integration.md``) maps
the reference 365-day default to a website-approved finite class and states
that ``null``/indefinite retention is never enabled by accident.  The command
boundary once accepted ``retention_days: null`` from any operator, silently
making a session's questions, names, and participant digests immortal.  These
tests pin the fail-closed guard, the rate-bucket expiry policy (a hashed
identity is not a deleted one), and the aggregate-only decision inventory.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from events.identity import create_event_identity
from events.models import EventQnaRateLimit, EventQnaSession
from events.qna import services
from events.qna.errors import QnaError

IP_A = "203.0.113.10"
IP_B = "203.0.113.20"
IP_B = "203.0.113.20"


def _seed_bucket(scope: str, *, window_seconds: int, started_at: datetime, hits: int = 1):
    digest = hashlib.sha256(f"{settings.SECRET_KEY}:{scope}".encode()).hexdigest()
    return EventQnaRateLimit.objects.create(
        scope_digest=digest,
        window_seconds=window_seconds,
        window_started_at=started_at,
        hits=hits,
    )


class RetentionContractTests(TestCase):
    def setUp(self) -> None:
        self.event = create_event_identity(
            title="Retention contract test event",
            source_repository="DataTalksClub/events",
            source_revision="0" * 40,
            source_key="retention-contract-test",
        )
        services.transition_session(self.event.id, EventQnaSession.State.OPEN)
        self.session = EventQnaSession.objects.get(event=self.event)

    def test_null_retention_is_refused_at_the_command_boundary(self) -> None:
        with self.assertRaises(QnaError) as caught:
            services.update_session(self.event.id, {"retention_days": None})
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(caught.exception.code, "retention_policy_required")

        self.session.refresh_from_db()
        # The reviewed default is untouched: refusing the request never
        # silently rewrote the row to indefinite.
        self.assertEqual(self.session.retention_days, 365)

    def test_non_positive_and_non_integer_retention_are_still_refused(self) -> None:
        for value in (0, -5, True, "365"):
            with self.subTest(value=value):
                with self.assertRaises(QnaError):
                    services.update_session(self.event.id, {"retention_days": value})
        self.session.refresh_from_db()
        self.assertEqual(self.session.retention_days, 365)

    def test_a_finite_approved_value_still_applies(self) -> None:
        services.update_session(self.event.id, {"retention_days": 30})
        self.session.refresh_from_db()
        self.assertEqual(self.session.retention_days, 30)

    def test_the_inventory_counts_the_decision_gate_inputs(self) -> None:
        services.transition_session(self.event.id, EventQnaSession.State.ARCHIVED)
        archived = EventQnaSession.objects.get(event=self.event)

        inventory = services.retention_inventory()

        self.assertEqual(inventory["archived_with_delete_deadline"], 1)
        # Freshly archived: the seven-day undo clock has not run out.
        self.assertEqual(inventory["archived_past_delete_deadline"], 0)
        self.assertEqual(inventory["indefinite_retention_sessions"], 0)
        self.assertIsNotNone(archived.archive_delete_at)

    def test_the_inventory_reports_past_due_and_indefinite_rows(self) -> None:
        services.transition_session(self.event.id, EventQnaSession.State.ARCHIVED)
        EventQnaSession.objects.update(
            archive_delete_at=timezone.now() - timedelta(days=1),
            retention_days=None,
        )

        inventory = services.retention_inventory()

        self.assertEqual(inventory["archived_with_delete_deadline"], 1)
        self.assertEqual(inventory["archived_past_delete_deadline"], 1)
        self.assertEqual(inventory["indefinite_retention_sessions"], 1)


class RateBucketExpiryTests(TestCase):
    """A hashed identity is not a deleted one: expired windows are reclaimed."""

    def test_an_expired_bucket_is_pruned_and_an_active_one_survives(self) -> None:
        now = timezone.now()
        expired = _seed_bucket(
            f"question-ip:{IP_A}",
            window_seconds=3600,
            started_at=now - timedelta(seconds=7200),
        )
        active = _seed_bucket(
            f"question-ip:{IP_B}",
            window_seconds=3600,
            started_at=now - timedelta(seconds=1800),
        )

        deleted = services.prune_expired_rate_buckets(now=now)

        self.assertEqual(deleted, 1)
        self.assertFalse(EventQnaRateLimit.objects.filter(pk=expired.pk).exists())
        # The active window's counter is untouched: an in-flight admission
        # never loses its budget to a cleanup run.
        active.refresh_from_db()
        self.assertEqual(active.hits, 1)

    def test_a_window_boundary_bucket_survives_until_its_window_fully_passes(self) -> None:
        now = timezone.now()
        bucket = _seed_bucket(
            f"question-ip:{IP_A}",
            window_seconds=3600,
            started_at=now - timedelta(seconds=3600),
        )

        # Exactly at the boundary the window is over; one second earlier not.
        before = services.prune_expired_rate_buckets(now=now - timedelta(seconds=1))
        self.assertEqual(before, 0)
        self.assertTrue(EventQnaRateLimit.objects.filter(pk=bucket.pk).exists())

        at_boundary = services.prune_expired_rate_buckets(now=now)
        self.assertEqual(at_boundary, 1)
        self.assertFalse(EventQnaRateLimit.objects.filter(pk=bucket.pk).exists())

    def test_pruning_is_bounded_and_resumable(self) -> None:
        now = timezone.now()
        started = now - timedelta(seconds=7200)
        for index in range(5):
            _seed_bucket(
                f"question-ip:{IP_A}:{index}",
                window_seconds=3600,
                started_at=started,
            )

        first = services.prune_expired_rate_buckets(now=now, batch_size=2)
        second = services.prune_expired_rate_buckets(now=now, batch_size=2)
        third = services.prune_expired_rate_buckets(now=now, batch_size=2)

        self.assertEqual((first, second, third), (2, 2, 1))
        self.assertEqual(EventQnaRateLimit.objects.count(), 0)

    def test_pruning_never_touches_an_admission_made_after_it_started(self) -> None:
        # A bucket created inside a live window outlives the cleanup: pruning
        # selects only rows whose window is entirely over at its own ``now``.
        now = timezone.now()
        seed = _seed_bucket(
            f"question:{IP_A}",
            window_seconds=3600,
            started_at=now - timedelta(seconds=10),
        )

        self.assertEqual(services.prune_expired_rate_buckets(now=now), 0)
        seed.refresh_from_db()
        self.assertEqual(seed.hits, 1)

    def test_the_inventory_counts_expired_buckets(self) -> None:
        now = timezone.now()
        _seed_bucket(
            "question-ip:expired",
            window_seconds=3600,
            started_at=now - timedelta(seconds=7200),
        )

        inventory = services.retention_inventory()

        self.assertEqual(inventory["expired_rate_buckets"], 1)

    def test_pruning_works_against_real_service_created_buckets(self) -> None:
        # The buckets admit_rate itself writes are the ones that must drain.
        scope = f"question:{IP_A}"
        self.assertIsNone(services.admit_rate(scope, window_seconds=3600, limit=1))
        # One admission creates exactly one bucket in the current window.
        self.assertEqual(EventQnaRateLimit.objects.count(), 1)
        # It is still active, so pruning now keeps it...
        self.assertEqual(services.prune_expired_rate_buckets(), 0)
        # ...and after its window has fully passed, it goes.
        future = timezone.now() + timedelta(seconds=7200)
        self.assertEqual(services.prune_expired_rate_buckets(now=future), 1)
        self.assertEqual(EventQnaRateLimit.objects.count(), 0)
