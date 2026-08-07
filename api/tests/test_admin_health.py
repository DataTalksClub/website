from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class AdminHealthTests(TestCase):
    def test_anonymous_request_is_denied_without_redirect_or_details(self) -> None:
        response = self.client.get(reverse("api:admin-health"))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "authentication_required")
        self.assertNotIn("Location", response.headers)
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_non_staff_request_is_forbidden(self) -> None:
        user = get_user_model().objects.create_user(
            username="learner", email="learner@example.com", password="test-password"
        )
        self.client.force_login(user)
        response = self.client.get(reverse("api:admin-health"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "permission_denied")

    def test_staff_request_returns_versioned_health(self) -> None:
        user = get_user_model().objects.create_user(
            username="staff",
            email="staff@example.com",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(user)
        response = self.client.get(reverse("api:admin-health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["actor"], "staff@example.com")
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])
