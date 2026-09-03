"""A Datamailer contact failure never carries the address it was about.

`contact_status` and `contact_preferences` put the member's address in the
request query string, because that is the shape Datamailer defines for them.
`raise_for_status()` then builds an `HTTPError` whose message embeds
`response.url`, so the address is in the exception text and in every traceback
that exception appears in.  The handlers identified the member by id, exactly
as they should, and `logger.exception` attached the address anyway.

Since this deployment configures no logging handlers, a traceback reaching
`logging.lastResort` is the one log channel that is live — so these tests check
the two things that decide whether an address reaches it: what the handler logs,
and what it raises when strict mode is on.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import requests

from django.test import SimpleTestCase, TestCase, override_settings

from accounts.models import CustomUser
from course_management.datamailer.preferences import get_email_preferences_for_user
from course_management.datamailer.redacted_errors import (
    DatamailerContactError,
    redacted_contact_error,
)
from course_management.datamailer.sync.status import get_contact_status

MEMBER_EMAIL = "learner@example.invalid"
LEAKY_URL = (
    f"https://datamailer.example.invalid/api/contacts/status?email={MEMBER_EMAIL}"
)

DATAMAILER_SETTINGS = {
    "DATAMAILER_URL": "https://datamailer.example.invalid",
    "DATAMAILER_API_KEY": "test-key",
    "DATAMAILER_CLIENT": "test-client",
    "DATAMAILER_AUDIENCE": "test-audience",
}


def _leaky_http_error() -> requests.HTTPError:
    """The exception `requests` raises for a failing contact lookup."""

    response = requests.Response()
    response.status_code = 400
    response.url = LEAKY_URL
    response.reason = "Bad Request"
    try:
        response.raise_for_status()
    except requests.HTTPError as error:
        return error
    raise AssertionError("raise_for_status did not raise")


class RedactedContactErrorTests(SimpleTestCase):
    def test_the_original_exception_carries_the_address(self) -> None:
        # The premise of the whole fix.  If this ever stops being true the
        # tests below stop meaning anything.
        self.assertIn(MEMBER_EMAIL, str(_leaky_http_error()))

    def test_the_restated_error_carries_neither_address_nor_url(self) -> None:
        failure = redacted_contact_error("contact status lookup", _leaky_http_error())

        message = str(failure)
        self.assertNotIn(MEMBER_EMAIL, message)
        self.assertNotIn("datamailer.example.invalid", message)

    def test_it_still_says_what_failed_and_how(self) -> None:
        failure = redacted_contact_error("contact status lookup", _leaky_http_error())

        self.assertIn("contact status lookup", str(failure))
        self.assertIn("400", str(failure))
        self.assertEqual(failure.status_code, 400)

    def test_it_is_still_a_request_failure_for_existing_handlers(self) -> None:
        failure = redacted_contact_error("contact status lookup", _leaky_http_error())

        self.assertIsInstance(failure, requests.RequestException)
        self.assertIsInstance(failure, DatamailerContactError)


@override_settings(**DATAMAILER_SETTINGS)
class ContactStatusLoggingTests(TestCase):
    def test_the_log_record_has_no_address_and_no_traceback(self) -> None:
        error = _leaky_http_error()

        with patch(
            "course_management.datamailer.client_contacts."
            "DatamailerContactClient.contact_status",
            side_effect=error,
        ):
            with self.assertLogs(
                "course_management.datamailer.sync.status", level=logging.ERROR
            ) as captured:
                result = get_contact_status(MEMBER_EMAIL)

        self.assertIsNone(result)
        record = captured.records[0]
        self.assertNotIn(MEMBER_EMAIL, record.getMessage())
        # `exc_info` is what turned an id-only message into an address in the
        # log, because the traceback carries the URL.
        self.assertIsNone(record.exc_info)

    @override_settings(DATAMAILER_STRICT=True)
    def test_strict_mode_raises_a_failure_with_no_address_and_no_cause(self) -> None:
        error = _leaky_http_error()

        with patch(
            "course_management.datamailer.client_contacts."
            "DatamailerContactClient.contact_status",
            side_effect=error,
        ):
            with self.assertLogs(
                "course_management.datamailer.sync.status", level=logging.ERROR
            ):
                with self.assertRaises(requests.RequestException) as raised:
                    get_contact_status(MEMBER_EMAIL)

        self.assertNotIn(MEMBER_EMAIL, str(raised.exception))
        # Raised `from None`: a chained cause would put the original message
        # and its URL back into the traceback Django's handler prints.
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)


@override_settings(**DATAMAILER_SETTINGS)
class ContactPreferencesLoggingTests(TestCase):
    def test_the_member_is_named_by_id_and_only_by_id(self) -> None:
        member = CustomUser.objects.create_user(
            username=MEMBER_EMAIL, email=MEMBER_EMAIL, password="test"
        )
        error = _leaky_http_error()

        with patch(
            "course_management.datamailer.client_contacts."
            "DatamailerContactClient.contact_preferences",
            side_effect=error,
        ):
            with self.assertLogs(
                "course_management.datamailer.preferences", level=logging.ERROR
            ) as captured:
                result = get_email_preferences_for_user(member)

        self.assertIsNone(result)
        record = captured.records[0]
        self.assertIn(f"user_id={member.pk}", record.getMessage())
        self.assertNotIn(MEMBER_EMAIL, record.getMessage())
        self.assertIsNone(record.exc_info)
