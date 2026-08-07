from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class StudioAccessTests(TestCase):
    def test_anonymous_user_is_sent_to_safe_login_flow(self) -> None:
        response = self.client.get(reverse("studio:home"))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('studio:home')}",
            fetch_redirect_response=False,
        )
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])

        login_response = self.client.get(response.headers["Location"])
        self.assertContains(login_response, "DataTalks.Club")
        self.assertIn("private", login_response.headers["Cache-Control"])
        self.assertIn("no-store", login_response.headers["Cache-Control"])

    def test_non_staff_user_is_denied(self) -> None:
        user = get_user_model().objects.create_user(
            username="learner", email="learner@example.com", password="test-password"
        )
        self.client.force_login(user)
        response = self.client.get(reverse("studio:home"))
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, "Traceback", status_code=403)

    def test_staff_user_sees_studio_shell_with_private_headers(self) -> None:
        user = get_user_model().objects.create_user(
            username="staff",
            email="staff@example.com",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(user)
        response = self.client.get(reverse("studio:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Studio")
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
