from django.contrib.auth import get_user_model
from django.test import TestCase


class AdoptedUserIntegrationTests(TestCase):
    def test_user_is_created_and_authenticates_by_email(self) -> None:
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="learner", email="learner@example.com", password="test-password"
        )

        self.assertEqual(user.email, "learner@example.com")
        self.assertTrue(self.client.login(email="learner@example.com", password="test-password"))

    def test_source_user_identity_remains_username_based_during_adoption(self) -> None:
        user_model = get_user_model()
        with self.assertRaises(TypeError):
            user_model.objects.create_user(email="", password="test-password")

        user_model.objects.create_user(
            username="learner-one", email="learner@example.com", password="test-password"
        )
        user_model.objects.create_user(
            username="learner-two", email="learner@example.com", password="different-password"
        )
        self.assertEqual(user_model.objects.filter(email="learner@example.com").count(), 2)

    def test_superuser_invariants_are_enforced(self) -> None:
        user_model = get_user_model()
        with self.assertRaises(ValueError):
            user_model.objects.create_superuser(
                username="admin",
                email="admin@example.com",
                password="test-password",
                is_staff=False,
            )
