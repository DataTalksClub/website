"""Rate-limit identities match the declared quotas (audit EVT-06).

The reviewed contract (``_docs/architecture/event-qna-integration.md``, "Rate
limits and errors") scopes questions and votes per participant/session, and
question-abuse, co-host redemption and general public traffic per source IP
across sessions.  The view layer once appended ``REMOTE_ADDR`` to every scope,
so a participant's own quota reset on every IP change, and co-host redemption
was fenced per event instead of being the one global per-IP budget.  These
tests pin the identity matrix, the global budgets, and the refusal contract.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from events.models import (
    EventQnaQuestion,
    EventQnaRateLimit,
    EventQnaSession,
    create_event_identity,
)
from events.qna import security, services

IP_A = "203.0.113.10"
IP_B = "203.0.113.20"
SPOOFED = "198.51.100.99"


def _seed_ip_bucket(scope: str, ip: str, *, window_seconds: int, hits: int) -> None:
    """Pre-fill one fixed-window bucket exactly as the service hashes it."""

    digest = hashlib.sha256(f"{settings.SECRET_KEY}:{scope}:{ip}".encode()).hexdigest()
    epoch = int(timezone.now().timestamp())
    bucket = datetime.fromtimestamp(epoch - (epoch % window_seconds), tz=UTC)
    EventQnaRateLimit.objects.create(
        scope_digest=digest,
        window_seconds=window_seconds,
        window_started_at=bucket,
        hits=hits,
    )


class RateIdentityTests(TestCase):
    def setUp(self) -> None:
        self.event = create_event_identity(
            title="Rate identity test event",
            source_repository="DataTalksClub/events",
            source_revision="e" * 40,
            source_key="rate-identity-test",
        )
        services.transition_session(self.event.id, EventQnaSession.State.OPEN)

    def _questions_path(self) -> str:
        return f"{services.event_qna_path(self.event)}/api/questions/"

    def _cohost_path(self, event: Any) -> str:
        return f"{services.event_qna_path(event)}/cohost/operator/"

    def _ask(self, *, ip: str, cookie: str | None = None, forwarded: str | None = None):
        if cookie:
            self.client.cookies[security.PARTICIPANT_COOKIE] = cookie
        extra: dict[str, Any] = {"REMOTE_ADDR": ip}
        if forwarded is not None:
            extra["HTTP_X_FORWARDED_FOR"] = forwarded
        return self.client.post(
            self._questions_path(),
            data=json.dumps({"text": "A question about rate identities"}),
            content_type="application/json",
            **extra,
        )

    @staticmethod
    def _asked_total(event: Any) -> int:
        return EventQnaQuestion.objects.filter(session__event=event).count()

    def test_one_participant_shares_its_budget_across_ips(self) -> None:
        _participant, cookie = security.new_participant()

        first = self._ask(ip=IP_A, cookie=cookie)
        self.assertEqual(first.status_code, 201, first.content)
        limited = self._ask(ip=IP_A, cookie=cookie)
        self.assertEqual(limited.status_code, 429, limited.content)
        # The same signed participant from another IP does NOT get a fresh
        # participant quota: the identity is the participant/session pair.
        moved = self._ask(ip=IP_B, cookie=cookie)
        self.assertEqual(moved.status_code, 429, moved.content)
        self.assertEqual(self._asked_total(self.event), 1)

    def test_participants_behind_one_ip_have_separate_budgets(self) -> None:
        first = self._ask(ip=IP_A, cookie=security.new_participant()[1])
        self.assertEqual(first.status_code, 201, first.content)
        second = self._ask(ip=IP_A, cookie=security.new_participant()[1])
        self.assertEqual(second.status_code, 201, second.content)
        self.assertEqual(self._asked_total(self.event), 2)

    def test_the_question_ip_budget_is_shared_across_participants(self) -> None:
        _seed_ip_bucket("question-ip", IP_A, window_seconds=3600, hits=300)

        exhausted_a = self._ask(ip=IP_A, cookie=security.new_participant()[1])
        self.assertEqual(exhausted_a.status_code, 429, exhausted_a.content)
        exhausted_b = self._ask(ip=IP_A, cookie=security.new_participant()[1])
        self.assertEqual(exhausted_b.status_code, 429, exhausted_b.content)
        # Another IP has its own abuse budget.
        elsewhere = self._ask(ip=IP_B, cookie=security.new_participant()[1])
        self.assertEqual(elsewhere.status_code, 201, elsewhere.content)

    def test_forwarded_headers_never_select_the_budget_identity(self) -> None:
        _participant, cookie = security.new_participant()
        self._ask(ip=IP_A, cookie=cookie)
        limited = self._ask(ip=IP_A, cookie=cookie)
        self.assertEqual(limited.status_code, 429, limited.content)

        # A spoofed forwarding header must not mint a fresh participant or IP
        # budget: REMOTE_ADDR stays the only identity source.
        spoofed = self._ask(ip=IP_A, cookie=cookie, forwarded=SPOOFED)
        self.assertEqual(spoofed.status_code, 429, spoofed.content)

    def test_cohost_redemption_is_one_global_ip_budget_across_events(self) -> None:
        other = create_event_identity(
            title="Rate identity second event",
            source_repository="DataTalksClub/events",
            source_revision="f" * 40,
            source_key="rate-identity-second",
        )
        services.transition_session(other.id, EventQnaSession.State.OPEN)

        # Ten redemption attempts exhaust the single per-IP budget; splitting
        # them across two events does not refill it.
        for index in range(10):
            event = self.event if index % 2 == 0 else other
            response = self.client.post(
                self._cohost_path(event), {"passcode": "wrong"}, REMOTE_ADDR=IP_A
            )
            self.assertEqual(response.status_code, 403, response.content)
        exhausted = self.client.post(
            self._cohost_path(other), {"passcode": "wrong"}, REMOTE_ADDR=IP_A
        )
        self.assertEqual(exhausted.status_code, 429, exhausted.content)
        # A different IP has its own redemption budget.
        fresh_ip = self.client.post(
            self._cohost_path(other), {"passcode": "wrong"}, REMOTE_ADDR=IP_B
        )
        self.assertEqual(fresh_ip.status_code, 403, fresh_ip.content)

    def test_exceeded_limits_return_429_with_retry_after_and_no_identity_echo(self) -> None:
        _participant, cookie = security.new_participant()
        self._ask(ip=IP_A, cookie=cookie)
        limited = self._ask(ip=IP_A, cookie=cookie)

        self.assertEqual(limited.status_code, 429)
        self.assertIn("Retry-After", limited.headers)
        self.assertNotIn(IP_A.encode(), limited.content)
        self.assertNotIn(cookie.encode(), limited.content)
        self.assertEqual(self._asked_total(self.event), 1)

    def test_the_general_public_traffic_budget_is_enforced(self) -> None:
        _seed_ip_bucket("public", IP_A, window_seconds=300, hits=2000)

        response = self._ask(ip=IP_A, cookie=security.new_participant()[1])

        self.assertEqual(response.status_code, 429, response.content)
        self.assertIn("Retry-After", response.headers)
