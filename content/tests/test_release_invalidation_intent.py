"""Acceptance tests for the durable release invalidation intent (PUB-07).

Activation and rollback must commit exactly one bound invalidation intent
inside the pointer-swap transaction; delivery happens only after commit.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

from community_base.jobs.dispatch import DispatchConflict
from community_base.jobs.models import JobIntent
from community_base.jobs.registry import JobContext
from community_base.jobs.runner import PermanentJobError
from django.db import transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase

from content.jobs import invalidate_release_paths
from content.models import ContentRelease
from content.services import (
    ActivateContentRelease,
    CreateContentSource,
    RollbackContentRelease,
    activate_content_release,
    create_content_source,
    rollback_content_release,
)
from core.models import RevisionConflict

from .factories import CONTEXT, activate, make_ready_release, make_source

_HANDLER = "content.release.invalidate"
_JOB_CONTEXT = JobContext(
    job_id=uuid.uuid4(),
    correlation_id=None,
    attempt=1,
    worker_id="test",
    lease_token=uuid.uuid4(),
)


def _intents_for(source) -> list[JobIntent]:
    return list(
        JobIntent.objects.filter(
            handler=_HANDLER,
            payload__source_id=str(source.id),
        ).order_by("created_at", "id")
    )


class ReleaseInvalidationIntentTests(TestCase):
    def test_first_activation_records_one_site_wildcard_intent(self) -> None:
        source = make_source()
        release = make_ready_release(source, commit_character="a")

        activate(source, release)

        source.refresh_from_db()
        intents = _intents_for(source)
        self.assertEqual(len(intents), 1)
        intent = intents[0]
        self.assertEqual(intent.handler, _HANDLER)
        # The intent is durable before any delivery: submission waits for commit.
        self.assertEqual(intent.status, JobIntent.Status.PENDING)
        self.assertEqual(
            intent.payload,
            {
                "version": 1,
                "transition": "activation",
                "source_id": str(source.id),
                "from_release_id": None,
                "to_release_id": str(release.id),
                "source_revision": source.revision,
                "path_prefixes": ["/"],
            },
        )

    def test_mounted_source_covers_mount_and_site_root(self) -> None:
        source = create_content_source(
            CreateContentSource(
                stable_id="mounted-docs",
                display_name="Mounted docs",
                repository_owner="DataTalksClub",
                repository_name="mounted-docs",
                branch="main",
                path_allowlist=("content/",),
                adapter_type="fixture",
                mount_path="/docs/",
                enabled=True,
            ),
            context=CONTEXT,
        )
        release = make_ready_release(source, commit_character="a")

        activate(source, release)

        (intent,) = _intents_for(source)
        self.assertEqual(intent.payload["path_prefixes"], ["/docs/", "/"])

    def test_rollback_records_its_own_transition_intent(self) -> None:
        source = make_source()
        first = activate(source, make_ready_release(source, commit_character="a"))
        second = activate(source, make_ready_release(source, commit_character="b"))

        source.refresh_from_db()
        first.refresh_from_db()
        rollback_content_release(
            RollbackContentRelease(
                source_id=source.id,
                release_id=first.id,
                expected_source_revision=source.revision,
                expected_release_revision=first.revision,
                reason="fixture rollback",
            ),
            context=CONTEXT,
        )

        intents = _intents_for(source)
        self.assertEqual(len(intents), 3)
        self.assertEqual(len({intent.key_hash for intent in intents}), 3)
        first_activation, second_activation, rollback_intent = intents
        self.assertEqual(first_activation.payload["transition"], "activation")
        self.assertIsNone(first_activation.payload["from_release_id"])
        self.assertEqual(first_activation.payload["to_release_id"], str(first.id))
        self.assertEqual(second_activation.payload["from_release_id"], str(first.id))
        self.assertEqual(second_activation.payload["to_release_id"], str(second.id))
        # Rollback is its own transition identity, not a reused activation.
        self.assertEqual(rollback_intent.payload["transition"], "rollback")
        self.assertEqual(rollback_intent.payload["from_release_id"], str(second.id))
        self.assertEqual(rollback_intent.payload["to_release_id"], str(first.id))

    def test_audit_failure_aborts_swap_and_leaves_no_intent(self) -> None:
        source = make_source()
        release = make_ready_release(source, commit_character="a")

        source.refresh_from_db()
        release.refresh_from_db()
        with patch(
            "content.services.record_audit_event",
            side_effect=RuntimeError("injected audit failure"),
        ):
            with self.assertRaises(RuntimeError):
                activate_content_release(
                    ActivateContentRelease(
                        source.id,
                        release.id,
                        source.revision,
                        release.revision,
                        reason="fixture activation",
                    ),
                    context=CONTEXT,
                )

        source.refresh_from_db()
        release.refresh_from_db()
        self.assertIsNone(source.active_release)
        self.assertEqual(release.status, ContentRelease.Status.READY)
        self.assertEqual(_intents_for(source), [])

    def test_dispatch_failure_aborts_swap_and_leaves_no_intent(self) -> None:
        source = make_source()
        release = make_ready_release(source, commit_character="a")

        source.refresh_from_db()
        release.refresh_from_db()
        with patch(
            "content.services.dispatch_after_commit",
            side_effect=DispatchConflict("injected dispatch conflict"),
        ):
            with self.assertRaises(DispatchConflict):
                activate_content_release(
                    ActivateContentRelease(
                        source.id,
                        release.id,
                        source.revision,
                        release.revision,
                        reason="fixture activation",
                    ),
                    context=CONTEXT,
                )

        source.refresh_from_db()
        release.refresh_from_db()
        self.assertIsNone(source.active_release)
        self.assertEqual(release.status, ContentRelease.Status.READY)
        self.assertEqual(_intents_for(source), [])

    def test_revision_conflict_publishes_no_intent(self) -> None:
        source = make_source()
        release = make_ready_release(source, commit_character="a")

        source.refresh_from_db()
        with self.assertRaises(RevisionConflict):
            activate_content_release(
                ActivateContentRelease(
                    source.id,
                    release.id,
                    source.revision + 1,
                    release.revision,
                    reason="stale contender",
                ),
                context=CONTEXT,
            )

        self.assertEqual(_intents_for(source), [])


class ReleaseInvalidationIntentDeliveryTests(TransactionTestCase):
    def test_intent_runs_to_succeeded_after_commit(self) -> None:
        source = make_source()
        release = make_ready_release(source, commit_character="a")

        activate(source, release)

        (intent,) = _intents_for(source)
        intent.refresh_from_db()
        self.assertEqual(intent.status, JobIntent.Status.SUCCEEDED)
        self.assertEqual(intent.attempts, 1)

    def test_outer_transaction_rollback_discards_the_intent(self) -> None:
        source = make_source()
        release = make_ready_release(source, commit_character="a")

        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                activate(source, release)
                raise RuntimeError("crash before commit")

        self.assertEqual(
            JobIntent.objects.filter(handler=_HANDLER).count(),
            0,
        )


class InvalidateReleasePathsHandlerTests(SimpleTestCase):
    def _valid_payload(self) -> dict[str, object]:
        return {
            "version": 1,
            "transition": "activation",
            "source_id": str(uuid.uuid4()),
            "from_release_id": None,
            "to_release_id": str(uuid.uuid4()),
            "source_revision": 7,
            "path_prefixes": ["/docs/", "/"],
        }

    def test_valid_payload_is_accepted(self) -> None:
        invalidate_release_paths(_JOB_CONTEXT, self._valid_payload())
        with_from = self._valid_payload() | {
            "transition": "rollback",
            "from_release_id": str(uuid.uuid4()),
        }
        invalidate_release_paths(_JOB_CONTEXT, with_from)

    def test_malformed_payloads_are_permanently_rejected(self) -> None:
        base = self._valid_payload()
        mutations: list[dict[str, object]] = [
            base | {"version": 2},
            base | {"version": "1"},
            base | {"transition": "delete"},
            base | {"source_id": "not-a-uuid"},
            base | {"from_release_id": "not-a-uuid"},
            base | {"from_release_id": 5},
            base | {"to_release_id": "not-a-uuid"},
            base | {"source_revision": True},
            base | {"source_revision": 0},
            base | {"source_revision": "7"},
            base | {"path_prefixes": []},
            base | {"path_prefixes": "docs/"},
            base | {"path_prefixes": ["docs/"]},
            base | {"path_prefixes": ["/docs"]},
            base | {"path_prefixes": ["//evil.invalid/"]},
            base | {"path_prefixes": ["/docs/?q=/"]},
            base | {"path_prefixes": ["/docs/#x/"]},
            base | {"path_prefixes": ["/docs /"]},
        ]
        for payload in mutations:
            with self.subTest(payload=payload):
                with self.assertRaises(PermanentJobError):
                    invalidate_release_paths(_JOB_CONTEXT, payload)
