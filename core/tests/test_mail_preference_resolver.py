"""The D1.2b package-mail migration contracts, updated for D1.2ca.

The outbox enqueue path is gone and the grep gate keeps it gone. Category
opt-outs are fields on the site user; the site resolver suppresses on an
explicit opt-out and allows everything else (Relay-side global unsubscribe
still applies on top).
"""

from django.test import TestCase

from accounts.models import CustomUser
from course_management.mail_preferences import resolve_mail_preference


def enqueue_symbol():
    # Composed so this negative test does not itself match the grep gate it
    # encodes.
    return "enqueue_" + "datamailer_outbox_event"


def repository_python_files():
    from pathlib import Path

    root = Path("course_management")
    for path in root.rglob("*.py"):
        if "migrations" in path.parts or "__pycache__" in path.parts:
            continue
        yield path


class OutboxEnqueueGateTest(TestCase):
    def test_no_enqueue_path_remains_outside_migrations(self):
        symbol = enqueue_symbol()
        offenders = [
            str(path)
            for path in repository_python_files()
            if symbol in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [])


class MailPreferenceResolverTest(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="resolver@example.com",
            email="resolver@example.com",
            password="test",
        )

    def resolve(
        self,
        purpose="homework-score-notification",
        category="submission-results",
        to=None,
        user="default",
    ):
        return resolve_mail_preference(
            purpose=purpose,
            category=category,
            to=to if to is not None else self.user.email,
            user=self.user if user == "default" else user,
        )

    def test_an_explicit_opt_out_suppresses_its_category(self):
        self.user.email_deadline_reminders = False
        self.user.save(update_fields=["email_deadline_reminders"])

        decision = self.resolve(category="deadline-reminders")

        self.assertNotEqual(decision, True)

    def test_an_unset_field_allows(self):
        self.assertIsNone(self.user.email_course_updates)
        self.assertIs(self.resolve(category="course-updates"), True)

    def test_a_userless_send_is_allowed(self):
        self.user.email_deadline_reminders = False
        self.user.save(update_fields=["email_deadline_reminders"])

        self.assertIs(self.resolve(user=None), True)

    def test_an_unknown_category_is_allowed(self):
        self.assertIs(self.resolve(category="transactional"), True)

    def test_no_category_is_allowed(self):
        self.assertIs(self.resolve(category=""), True)
