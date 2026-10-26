from typing import cast
from unittest.mock import Mock, patch
from uuid import UUID

import requests
from django.test import SimpleTestCase, override_settings

from core.operator_alerts import OperatorAlertError, notify_operator_alert


@override_settings(
    OPERATOR_ALERT_TELEGRAM_BOT_TOKEN="123:synthetic_bot_credential",
    OPERATOR_ALERT_TELEGRAM_CHAT_ID="-123",
)
class OperatorAlertsTests(SimpleTestCase):
    @patch("core.operator_alerts.requests.post")
    def test_acknowledged_notice_contains_only_event_and_opaque_identifiers(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {"ok": True, "result": {"message_id": 17}}
        delivery_id = UUID("7cd26724-a6bd-4586-bc2f-75ea8b55a632")
        notify_operator_alert(
            "application_notification_failed", {"registration_id": 12, "delivery_id": delivery_id}
        )
        self.assertEqual(post.call_count, 1)
        self.assertEqual(
            post.call_args.kwargs,
            {
                "json": {
                    "chat_id": "-123",
                    "text": (
                        "Website operational alert: application_notification_failed\n"
                        f"delivery_id={delivery_id}\nregistration_id=12"
                    ),
                },
                "timeout": 10,
                "allow_redirects": False,
            },
        )

    @patch("core.operator_alerts.requests.post")
    def test_rejects_untrusted_content_before_network(self, post):
        for event, identifiers in (
            (cast(str, []), {"registration_id": 12}),
            ("arbitrary failure text", {"registration_id": 12}),
            ("registration_contacts_failed", {"email": "synthetic@example.invalid"}),
            ("registration_contacts_failed", {"registration_id": "private data"}),
            ("registration_contacts_failed", {"registration_id": True}),
            ("registration_contacts_failed", {}),
        ):
            with self.subTest(event=event, fields=list(identifiers)):
                with self.assertRaises(OperatorAlertError):
                    notify_operator_alert(event, identifiers)
        post.assert_not_called()

    @patch("core.operator_alerts.requests.post")
    def test_failure_diagnostics_never_expose_transport_secrets(self, post):
        post.side_effect = requests.RequestException("private credential-bearing URL and recipient")
        with self.assertRaises(OperatorAlertError) as caught:
            notify_operator_alert("registration_contacts_failed", {"registration_id": 12})
        self.assertEqual(str(caught.exception), "transport_failed")
        self.assertTrue(caught.exception.__suppress_context__)

    @patch("core.operator_alerts.requests.post")
    def test_requires_successful_acknowledgement(self, post):
        for status, document in (
            (302, {}),
            (500, {"ok": True}),
            (200, {"ok": False, "description": "untrusted data"}),
            (200, {"ok": True}),
            (200, {"ok": True, "result": {"message_id": True}}),
        ):
            with self.subTest(status=status, document=document):
                post.return_value = Mock(status_code=status)
                post.return_value.json.return_value = document
                with self.assertRaises(OperatorAlertError):
                    notify_operator_alert("registration_contacts_failed", {"registration_id": 12})

    @patch("core.operator_alerts.requests.post")
    def test_missing_or_invalid_configuration_is_loud_without_network(self, post):
        for token, chat in (("", ""), ("private malformed token", "-123"), ("123:valid", "name")):
            with self.subTest(configured=bool(token and chat)):
                with override_settings(
                    OPERATOR_ALERT_TELEGRAM_BOT_TOKEN=token, OPERATOR_ALERT_TELEGRAM_CHAT_ID=chat
                ):
                    with self.assertRaises(OperatorAlertError):
                        notify_operator_alert(
                            "registration_contacts_failed", {"registration_id": 12}
                        )
        post.assert_not_called()
