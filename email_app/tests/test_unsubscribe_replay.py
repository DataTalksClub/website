"""The durable half of the opt-out promise.

Once a recipient has been told their request is recorded, it has to happen.  The
intent is persisted in the accepting transaction and replayed by a leased durable
job afterwards, which is the architecture's boundary for any Relay mutation.
"""

from __future__ import annotations

import uuid
from unittest import mock

from django.test import TestCase, override_settings

from email_app import relay_links, services
from email_app.jobs import replay_unsubscribe
from email_app.models import PendingUnsubscribe
from email_app.tests.support import FakeRelay, unreachable_relay
from jobs.execution import PermanentJobError, RetryableJobError
from jobs.models import DurableJob
from jobs.registry import JobContext, registered_handler_names, validate_payload

RELAY = "http://relay.website.internal:8000"
TOKEN = "kD3Yy8x-Ug2f_QwErTyUiOpAsDfGhJkLzXcVbNm1234"


def job_context() -> JobContext:
    return JobContext(
        job_id=uuid.uuid4(),
        operation_id=None,
        request_id=None,
        correlation_id=None,
        attempt_count=1,
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
        jobs = DurableJob.objects.filter(handler=services.UNSUBSCRIBE_REPLAY_HANDLER)
        self.assertEqual(jobs.count(), 1)
        self.assertEqual(jobs.get().max_attempts, services.UNSUBSCRIBE_REPLAY_MAX_ATTEMPTS)

    def test_accepting_twice_produces_one_intent_and_honours_the_newer_scope(self) -> None:
        first = services.accept_unsubscribe_for_replay(token=TOKEN, scope="client")
        second = services.accept_unsubscribe_for_replay(token=TOKEN, scope="global")
        self.assertEqual(first.pending_id, second.pending_id)
        self.assertEqual(PendingUnsubscribe.objects.count(), 1)
        self.assertEqual(PendingUnsubscribe.objects.get().scope, "global")
        self.assertEqual(
            DurableJob.objects.filter(handler=services.UNSUBSCRIBE_REPLAY_HANDLER).count(), 1
        )

    def test_a_malformed_request_is_never_made_durable(self) -> None:
        with self.assertRaises(ValueError):
            services.accept_unsubscribe_for_replay(token="short", scope="client")
        with self.assertRaises(ValueError):
            services.accept_unsubscribe_for_replay(token=TOKEN, scope="everything")
        self.assertFalse(PendingUnsubscribe.objects.exists())

    def test_the_job_payload_carries_an_identifier_and_never_the_token(self) -> None:
        accepted = services.accept_unsubscribe_for_replay(token=TOKEN, scope="client")
        payload = DurableJob.objects.get(handler=services.UNSUBSCRIBE_REPLAY_HANDLER).payload
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
