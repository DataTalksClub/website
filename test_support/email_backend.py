from __future__ import annotations

from collections.abc import Iterable

from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.message import EmailMessage

from .messaging import CaptureMailbox

SYNTHETIC_EMAIL_BACKEND = "test_support.email_backend.SyntheticCaptureEmailBackend"


class SyntheticCaptureEmailBackend(BaseEmailBackend):
    """Test-only send boundary that captures reserved recipients without a provider."""

    mailbox = CaptureMailbox()

    def send_messages(self, email_messages: Iterable[EmailMessage]) -> int:
        messages = list(email_messages)
        recipients = [
            (message, recipient) for message in messages for recipient in message.recipients()
        ]
        # Validate the complete batch before recording any part of it.
        for _message, recipient in recipients:
            CaptureMailbox().send(
                purpose="django-test-validation",
                recipient=recipient,
                subject="Synthetic validation",
                body="",
            )
        for message, recipient in recipients:
            self.mailbox.send(
                purpose="django-test-email",
                recipient=recipient,
                subject=str(message.subject),
                body=str(message.body),
            )
        return len(messages)


def reset_capture_mailbox() -> None:
    SyntheticCaptureEmailBackend.mailbox.messages.clear()
