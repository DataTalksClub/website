from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase


class EmailUserTests(TestCase):
    def test_user_is_created_and_authenticates_by_normalized_email(self) -> None:
        user_model = get_user_model()
        user = user_model.objects.create_user("LEARNER@EXAMPLE.COM", "test-password")

        self.assertEqual(user.email, "learner@example.com")
        self.assertTrue(self.client.login(email="learner@example.com", password="test-password"))

    def test_email_is_required_and_unique(self) -> None:
        user_model = get_user_model()
        with self.assertRaises(ValueError):
            user_model.objects.create_user("", "test-password")

        user_model.objects.create_user("learner@example.com", "test-password")
        with self.assertRaises(IntegrityError):
            user_model.objects.create_user("learner@example.com", "different-password")

    def test_superuser_invariants_are_enforced(self) -> None:
        user_model = get_user_model()
        with self.assertRaises(ValueError):
            user_model.objects.create_superuser(
                "admin@example.com", "test-password", is_staff=False
            )
