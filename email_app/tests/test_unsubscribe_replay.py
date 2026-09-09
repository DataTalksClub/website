"""The durable half of the opt-out promise.

Once a recipient has been told their request is recorded, it has to happen.  The
intent is persisted in the accepting transaction and replayed by a leased durable
job afterwards, which is the architecture's boundary for any Relay mutation.
"""

from __future__ import annotations

import uuid
from unittest import mock

from community_base.jobs.models import JobIntent
from community_base.jobs.registry import (
    JobContext,
    registered_handler_names,
    validate_payload,
)
from community_base.jobs.runner import PermanentJobError, RetryableJobError
from django.db.models import F
from django.test import TestCase, override_settings

from email_app import relay_links, services
from email_app.jobs import replay_unsubscribe
from email_app.models import PendingUnsubscribe
from email_app.tests.support import FakeRelay, unreachable_relay

RELAY = "http://relay.website.internal:8000"
TOKEN = "kD3Yy8x-Ug2f_QwErTyUiOpAsDfGhJkLzXcVbNm1234"


def job_context() -> JobContext:
    return JobContext(
        job_id=uuid.uuid4(),
        correlation_id=None,
        attempt=1,
        worker_id="test",
        lease_token=uuid.uuid4(),
    )


@override_settings(RELAY_LINK_BRIDGE_BASE_URL=RELAY)
class AcceptanceTests(TestCase):
    def test_accepting_persists_the_intent_and_one_durable_job(self) -> None:
        accepted = services.accept_unsubscribe_for_replay(token=TOKEN, scope="audience")
        pending = PendingUnsubscribe.objects.get(pk=accepted.pending_id)
        self.assertEqual(pending.scope, "audience")
        self.assertEqual(pending.unsubscribe_token, TOKEN)
        jobs = JobIntent.objects.filter(handler=services.UNSUBSCRIBE_REPLAY_HANDLER)
        self.assertEqual(jobs.count(), 1)
        self.assertEqual(jobs.get().max_attempts, services.UNSUBSCRIBE_REPLAY_MAX_ATTEMPTS)

    def test_reaccepting_a_pending_opt_out_creates_a_fresh_generation(self) -> None:
        """A second accepted request during one outage must produce runnable
        work, not a second forked opt-out and not a reuse of the first
        request's job key (audits BE-10/BE-11)."""

        first = services.accept_unsubscribe_for_replay(token=TOKEN, scope="client")
        second = services.accept_unsubscribe_for_replay(token=TOKEN, scope="global")
        self.assertEqual(first.pending_id, second.pending_id)
        self.assertEqual(PendingUnsubscribe.objects.count(), 1)
        pending = PendingUnsubscribe.objects.get()
        self.assertEqual(pending.scope, "global")
        self.assertEqual(pending.generation, 2)
        intents = JobIntent.objects.filter(handler=services.UNSUBSCRIBE_REPLAY_HANDLER)
        self.assertEqual(intents.count(), 2)
        self.assertEqual(
            len({intent.key_hash for intent in intents}),
            2,
            "each generation must own its own durable job key",
        )

    def test_a_reacceptance_after_job_exhaustion_creates_runnable_work(self) -> None:
        """BE-10: a job exhausted by a long outage is terminal and never
        claimable again; the recipient's re-submission must dispatch a fresh
        intent for the still-pending opt-out instead of reusing the dead
        key."""

        services.accept_unsubscribe_for_replay(token=TOKEN, scope="client")
        JobIntent.objects.update(status=JobIntent.Status.DEAD)

        services.accept_unsubscribe_for_replay(token=TOKEN, scope="client")

        pending = PendingUnsubscribe.objects.get()
        self.assertEqual(pending.generation, 2)
        self.assertEqual(
            sorted(
                JobIntent.objects.filter(handler=services.UNSUBSCRIBE_REPLAY_HANDLER).values_list(
                    "status", flat=True
                )
            ),
            ["dead", "pending"],
            "the fresh generation's intent must be claimable",
        )

    def test_an_exhausted_job_never_settles_the_pending_row_it_belongs_to(self) -> None:
        services.accept_unsubscribe_for_replay(token=TOKEN, scope="client")
        JobIntent.objects.update(status=JobIntent.Status.DEAD)

        services.accept_unsubscribe_for_replay(token=TOKEN, scope="client")

        # The pending opt-out survives the exhaustion; only a fresh generation
        # plus runnable work settles it.
        pending = PendingUnsubscribe.objects.get()
        self.assertEqual(pending.status, PendingUnsubscribe.Status.PENDING)

    def test_a_malformed_request_is_never_made_durable(self) -> None:
        with self.assertRaises(ValueError):
            services.accept_unsubscribe_for_replay(token="short", scope="client")
        with self.assertRaises(ValueError):
            services.accept_unsubscribe_for_replay(token=TOKEN, scope="everything")
        self.assertFalse(PendingUnsubscribe.objects.exists())

    def test_the_job_payload_carries_an_identifier_and_never_the_token(self) -> None:
        accepted = services.accept_unsubscribe_for_replay(token=TOKEN, scope="client")
        payload = JobIntent.objects.get(handler=services.UNSUBSCRIBE_REPLAY_HANDLER).payload
        self.assertEqual(payload, {"pending_unsubscribe_id": str(accepted.pending_id)})
        self.assertNotIn(TOKEN, str(payload))
        # The durable payload contract rejects a protected value outright; this
        # asserts the payload is inside it rather than merely tidy.
        self.assertEqual(validate_payload(payload), payload)

    def test_the_handler_is_registered_under_its_contract_name(self) -> None:
        self.assertIn(services.UNSUBSCRIBE_REPLAY_HANDLER, registered_handler_names())


@override_settings(RELAY_LINK_BRIDGE_BASE_URL=RELAY)
class ReplayTests(TestCase):
    def setUp(self) -> None:
        self.accepted = services.accept_unsubscribe_for_replay(token=TOKEN, scope="client")

    def _replay(self, relay: FakeRelay) -> None:
        with mock.patch.object(relay_links, "_pool", return_value=relay):
            replay_unsubscribe(
                job_context(), {"pending_unsubscribe_id": str(self.accepted.pending_id)}
            )

    def test_a_successful_replay_applies_the_opt_out_and_drops_the_record(self) -> None:
        relay = FakeRelay(status_code=200)
        self._replay(relay)
        self.assertEqual(relay.calls[-1].data, {"scope": "client"})
        self.assertEqual(relay.calls[-1].url, f"{RELAY}/unsubscribe/{TOKEN}")
        # The row exists only to carry the token until Relay has the opt-out.
        self.assertFalse(PendingUnsubscribe.objects.exists())

    def test_an_unreachable_relay_keeps_the_intent_and_asks_to_retry(self) -> None:
        with self.assertRaises(RetryableJobError):
            self._replay(unreachable_relay())
        pending = PendingUnsubscribe.objects.get()
        self.assertEqual(pending.status, PendingUnsubscribe.Status.PENDING)
        self.assertEqual(pending.attempt_count, 1)
        self.assertEqual(pending.last_outcome, "unavailable")

    def test_a_link_relay_does_not_know_stops_retrying(self) -> None:
        self._replay(FakeRelay(status_code=404))
        pending = PendingUnsubscribe.objects.get()
        self.assertEqual(pending.status, PendingUnsubscribe.Status.REJECTED)

    def test_replaying_a_settled_intent_calls_relay_again_never(self) -> None:
        self._replay(FakeRelay(status_code=200))
        relay = FakeRelay(status_code=200)
        self._replay(relay)
        self.assertFalse(relay.called)

    def test_an_invalid_payload_fails_permanently(self) -> None:
        for payload in ({}, {"pending_unsubscribe_id": 1}, {"pending_unsubscribe_id": "nope"}):
            with self.subTest(payload=payload), self.assertRaises(PermanentJobError):
                replay_unsubscribe(job_context(), payload)  # type: ignore[arg-type]

    @override_settings(RELAY_LINK_BRIDGE_BASE_URL="")
    def test_a_deployment_with_no_relay_fails_permanently_rather_than_retrying(self) -> None:
        with self.assertRaises(PermanentJobError):
            self._replay(FakeRelay(status_code=200))


@override_settings(RELAY_LINK_BRIDGE_BASE_URL=RELAY)
@override_settings(RELAY_LINK_BRIDGE_BASE_URL=RELAY)
class SupersededGenerationTests(TestCase):
    """BE-11: a worker finishing an older generation can never erase the
    recipient's newer choice.  The Relay call is simulated with a relay that
    changes the row mid-flight, exactly as a concurrent re-acceptance would."""

    def setUp(self) -> None:
        self.accepted = services.accept_unsubscribe_for_replay(token=TOKEN, scope="client")

    def _row(self) -> PendingUnsubscribe:
        return PendingUnsubscribe.objects.get(pk=self.accepted.pending_id)

    def _replay_against_racing_relay(self, relay: FakeRelay) -> str:
        with mock.patch.object(relay_links, "_pool", return_value=relay):
            return services.replay_pending_unsubscribe(self.accepted.pending_id)

    def _racing_relay(self, status_code: int, new_scope: str) -> FakeRelay:
        pending_id = self.accepted.pending_id

        class RacingRelay(FakeRelay):
            def request(self, method, url, **kwargs):
                # The concurrent re-acceptance: newer scope, newer generation.
                PendingUnsubscribe.objects.filter(pk=pending_id).update(
                    scope=new_scope, generation=F("generation") + 1
                )
                return super().request(method, url, **kwargs)

        return RacingRelay(status_code=status_code)

    def test_an_older_success_cannot_settle_a_newer_wider_choice(self) -> None:
        relay = self._racing_relay(status_code=200, new_scope="global")

        outcome = self._replay_against_racing_relay(relay)

        self.assertEqual(outcome, "superseded")
        self.assertEqual(relay.calls[-1].data, {"scope": "client"})
        row = self._row()
        self.assertEqual(row.scope, "global")
        self.assertEqual(row.generation, 2)
        self.assertEqual(row.status, PendingUnsubscribe.Status.PENDING)

    def test_an_older_rejection_cannot_settle_a_newer_narrower_choice(self) -> None:
        relay = self._racing_relay(status_code=404, new_scope="audience")

        outcome = self._replay_against_racing_relay(relay)

        self.assertEqual(outcome, "superseded")
        row = self._row()
        self.assertEqual(row.scope, "audience")
        self.assertEqual(row.generation, 2)
        self.assertEqual(row.status, PendingUnsubscribe.Status.PENDING)

    def test_the_newer_generation_replay_applies_the_newer_choice(self) -> None:
        self._replay_against_racing_relay(self._racing_relay(status_code=200, new_scope="global"))
        relay = FakeRelay(status_code=200)

        outcome = self._replay_against_racing_relay(relay)

        self.assertEqual(outcome, "applied")
        self.assertEqual(relay.calls[-1].data, {"scope": "global"})
        self.assertFalse(PendingUnsubscribe.objects.exists())

    def test_the_worker_treats_a_superseded_attempt_as_done(self) -> None:
        relay = self._racing_relay(status_code=200, new_scope="global")

        with mock.patch.object(relay_links, "_pool", return_value=relay):
            replay_unsubscribe(
                job_context(),
                {"pending_unsubscribe_id": str(self.accepted.pending_id)},
            )

        # No exception: the newer generation's job owns the row now.
        self.assertEqual(self._row().status, PendingUnsubscribe.Status.PENDING)

    def test_a_stale_attempt_does_not_overwrite_the_newer_outcome_metadata(self) -> None:
        relay = self._racing_relay(status_code=200, new_scope="global")

        self._replay_against_racing_relay(relay)

        row = self._row()
        # The stale attempt's outcome is not recorded onto the newer
        # generation's row; the newer choice has no outcome of its own yet.
        self.assertEqual(row.last_outcome, "")
        self.assertEqual(row.attempt_count, 0)
