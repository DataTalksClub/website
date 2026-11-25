"""A confirmed seat hears "you're registered" once and gets reminders; imports get nothing."""

from __future__ import annotations

from datetime import timedelta

from community_base.events.models import Event, EventRegistration, EventReminder
from community_base.events.reminders import claim_due_reminders
from community_base.jobs.registry import registered_schedules
from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from event_registrants import registrant_mail
from event_registrants.models import EventOperatorConfig
from events.identity import create_event_identity
from test_support.event_pictures import TEST_EVENT_PICTURE

JOIN_URL = "https://example.invalid/zoom/j/123?pwd=synthetic"


class RegistrantMailTestCase(TestCase):
    def setUp(self) -> None:
        self.starts_at = (timezone.now() + timedelta(days=3)).replace(microsecond=0)
        self.event = create_event_identity(
            picture=TEST_EVENT_PICTURE,
            title="A Zoom session",
            event_type="webinar",
            speakers=[("janedoe", "Jane Doe")],
            description="What the session is about.",
            starts_at=self.starts_at,
        )
        Event.objects.filter(pk=self.event.pk).update(platform="zoom", zoom_join_url=JOIN_URL)
        self.event.refresh_from_db()
        self.path = f"/events/{self.event.public_id}/{self.event.slug}"

    def confirmed_mail(self):
        return EmailDelivery.objects.filter(purpose=registrant_mail.CONFIRMED_PURPOSE)

    def seat(self, email: str, *, status=EventRegistration.Status.CONFIRMED, **extra):
        return EventRegistration.objects.create(
            event=self.event,
            original_email=email,
            normalized_email=email,
            status=status,
            verified_at=extra.pop("verified_at", timezone.now() - timedelta(days=5)),
            **extra,
        )


class ConfirmationTests(RegistrantMailTestCase):
    def test_one_click_registration_sends_the_confirmation_once_with_the_join_link(self) -> None:
        member = get_user_model().objects.create_user(
            username="member",
            email="member@example.invalid",
            password="pw",
            preferred_timezone="America/New_York",
        )
        member.newsletter_subscribed = True
        member.save(update_fields=["newsletter_subscribed"])
        self.client.force_login(member)

        self.client.post(f"{self.path}/register")
        self.client.post(f"{self.path}/register")

        delivery = self.confirmed_mail().get()
        self.assertEqual(delivery.recipient_email, "member@example.invalid")
        context = delivery.context_data
        self.assertEqual(context["join_url"], JOIN_URL)
        self.assertEqual(context["event_title"], "A Zoom session")
        self.assertIn("(America/New_York)", context["event_when"])
        self.assertTrue(context["event_url"].endswith(self.path))
        self.assertTrue(context["calendar_url"].startswith("https://calendar.google.com/"))

    def test_email_registration_gets_the_link_only_after_confirming(self) -> None:
        self.client.post(
            f"{self.path}/register",
            data={"email": "guest@example.invalid", "newsletter_consent": "on"},
        )
        verify = EmailDelivery.objects.get(purpose="event-registration-verify")
        self.assertNotIn(JOIN_URL, str(verify.context_data))
        self.assertFalse(self.confirmed_mail().exists())

        verify_url = verify.context_data["verify_url"]
        self.client.get(verify_url[verify_url.index("/events/") :])
        self.client.get(verify_url[verify_url.index("/events/") :])

        delivery = self.confirmed_mail().get()
        self.assertEqual(delivery.context_data["join_url"], JOIN_URL)
        self.assertIn("(Europe/Berlin)", delivery.context_data["event_when"])

    def test_an_imported_seat_is_never_confirmed_by_mail(self) -> None:
        registration = self.seat(
            "imported@example.invalid", acquisition_metadata={"source": "academy-sessions"}
        )

        self.assertIsNone(registrant_mail.send_registration_confirmed(registration))
        self.assertFalse(self.confirmed_mail().exists())

    def test_the_join_link_reaches_only_a_confirmed_seat_at_a_zoom_event(self) -> None:
        pending = self.seat(
            "pending@example.invalid", status=EventRegistration.Status.PENDING_VERIFICATION
        )
        confirmed = self.seat("confirmed@example.invalid")

        self.assertNotIn("join_url", registrant_mail.seat_context(pending))
        self.assertIsNone(registrant_mail.send_registration_confirmed(pending))
        self.assertEqual(registrant_mail.seat_context(confirmed)["join_url"], JOIN_URL)

        Event.objects.filter(pk=self.event.pk).update(platform="")
        confirmed.refresh_from_db()
        self.assertNotIn("join_url", registrant_mail.seat_context(confirmed))


class ReminderTests(RegistrantMailTestCase):
    def test_the_site_planner_replaces_the_package_schedule(self) -> None:
        names = {item.name for item in registered_schedules()}

        self.assertIn("event_registrants.plan_reminders.every_5_minutes", names)
        self.assertNotIn(registrant_mail.PACKAGE_REMINDER_SCHEDULE, names)

    def test_reminders_are_planned_at_each_interval_once(self) -> None:
        registration = self.seat("seat@example.invalid")
        before_24h = self.starts_at - timedelta(hours=24, minutes=1)
        after_24h = self.starts_at - timedelta(hours=23, minutes=59)
        after_20m = self.starts_at - timedelta(minutes=19)

        self.assertEqual(registrant_mail.plan_due_reminders(now=before_24h), 0)
        self.assertEqual(registrant_mail.plan_due_reminders(now=after_24h), 1)
        self.assertEqual(registrant_mail.plan_due_reminders(now=after_24h), 0)
        self.assertEqual(registrant_mail.plan_due_reminders(now=after_20m), 1)

        planned = {
            row.interval: row.scheduled_for
            for row in EventReminder.objects.filter(registration=registration)
        }
        self.assertEqual(
            planned,
            {
                "24h": self.starts_at - timedelta(hours=24),
                "20m": self.starts_at - timedelta(minutes=20),
            },
        )

    def test_the_events_own_intervals_apply(self) -> None:
        EventOperatorConfig.objects.filter(event=self.event).update(reminder_intervals=["1h"])
        self.seat("seat@example.invalid")

        registrant_mail.plan_due_reminders(now=self.starts_at - timedelta(hours=2))
        self.assertFalse(EventReminder.objects.exists())
        registrant_mail.plan_due_reminders(now=self.starts_at - timedelta(minutes=59))
        self.assertEqual(list(EventReminder.objects.values_list("interval", flat=True)), ["1h"])

        EventOperatorConfig.objects.filter(event=self.event).update(reminders_enabled=False)
        EventReminder.objects.all().delete()
        registrant_mail.plan_due_reminders(now=self.starts_at - timedelta(minutes=59))
        self.assertFalse(EventReminder.objects.exists())

    def test_imported_pending_and_late_seats_get_no_reminder(self) -> None:
        self.seat("imported@example.invalid", acquisition_metadata={"source": "academy-sessions"})
        self.seat("pending@example.invalid", status=EventRegistration.Status.PENDING_VERIFICATION)
        # Confirmed two hours before the start: the 24h moment had passed.
        self.seat("late@example.invalid", verified_at=self.starts_at - timedelta(hours=2))

        registrant_mail.plan_due_reminders(now=self.starts_at - timedelta(hours=1))

        self.assertFalse(EventReminder.objects.exists())

    def test_a_due_reminder_is_sent_with_the_join_link(self) -> None:
        registration = self.seat("seat@example.invalid")
        moment = self.starts_at - timedelta(minutes=19)
        registrant_mail.plan_due_reminders(now=moment)
        (claimed,) = claim_due_reminders(at=moment)

        self.assertEqual(registrant_mail.send_reminder(claimed.reminder_id), "sent")
        self.assertEqual(registrant_mail.send_reminder(claimed.reminder_id), "not_claimed")

        delivery = EmailDelivery.objects.get(purpose=registrant_mail.REMINDER_PURPOSE)
        self.assertEqual(delivery.recipient_email, registration.normalized_email)
        self.assertEqual(delivery.context_data["join_url"], JOIN_URL)
        self.assertEqual(delivery.context_data["starts_in"], "in 20 minutes")
        reminder = EventReminder.objects.get(pk=claimed.reminder_id)
        self.assertEqual(reminder.status, EventReminder.Status.SENT)

    def test_a_seat_cancelled_after_planning_is_skipped(self) -> None:
        registration = self.seat("seat@example.invalid")
        moment = self.starts_at - timedelta(minutes=19)
        registrant_mail.plan_due_reminders(now=moment)
        (claimed,) = claim_due_reminders(at=moment)
        EventRegistration.objects.filter(pk=registration.pk).update(
            status=EventRegistration.Status.CANCELLED
        )

        self.assertEqual(
            registrant_mail.send_reminder(claimed.reminder_id), "registration_inactive"
        )
        self.assertFalse(EmailDelivery.objects.exists())

    def test_interval_wording(self) -> None:
        self.assertEqual(registrant_mail._starts_in(timedelta(hours=24)), "tomorrow")
        self.assertEqual(registrant_mail._starts_in(timedelta(hours=1)), "in 1 hour")
        self.assertEqual(registrant_mail._starts_in(timedelta(minutes=20)), "in 20 minutes")


class ImportSourceTests(TestCase):
    def test_the_academy_import_marks_its_seats_as_imported(self) -> None:
        from scripts.prod.import_academy_sessions import REGISTRATION_SOURCE

        self.assertIn(REGISTRATION_SOURCE, registrant_mail.IMPORTED_SOURCES)
